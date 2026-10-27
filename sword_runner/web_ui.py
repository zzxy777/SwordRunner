from __future__ import annotations

import argparse
import json
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
import webbrowser
from dataclasses import dataclass, field
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

RUNNER_ROOT = Path(__file__).resolve().parent
if str(RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNNER_ROOT))

from pipeline.chapter_loader import load_chapter_index
from pipeline.config import load_config
from pipeline.io_utils import chapter_safe_name, make_run_id, write_json_atomic


STEP_ORDER = ["chapter", "segment", "director", "voice", "tts", "post"]
DEFAULT_PROJECT_ROOT = os.environ.get("SWORD_RUNNER_PROJECT_ROOT", str(Path.home() / "SwordAudiobookProject"))
USER_SETTINGS_PATH = RUNNER_ROOT / "config" / "user_settings.json"
ENV_PATH = RUNNER_ROOT / "config" / ".env"
STATIC_ROOT = RUNNER_ROOT / "static"
MAX_LOG_LINES = 12000
SENSITIVE_MASK = "********"


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def normalize_query(value: str) -> str:
    return "".join(str(value or "").lower().replace("_", "").split())


def read_json_safe(path: Path, fallback: Any) -> Any:
    try:
        if path.exists():
            return json.loads(path.read_text(encoding="utf-8-sig"))
    except Exception:
        return fallback
    return fallback


def read_env_file(path: Path = ENV_PATH) -> dict[str, str]:
    rows: dict[str, str] = {}
    if not path.exists():
        return rows
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        rows[key.strip()] = value.strip().strip('"').strip("'")
    return rows


def write_env_file(updates: dict[str, str], path: Path = ENV_PATH) -> None:
    current = read_env_file(path)
    for key, value in updates.items():
        if value and SENSITIVE_MASK not in value:
            current[key] = value
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Local secrets for sword_runner. Do not share this file."]
    for key in sorted(current):
        lines.append(f"{key}={current[key]}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def mask_secret(value: str) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if len(text) <= 8:
        return SENSITIVE_MASK
    return text[:4] + SENSITIVE_MASK + text[-4:]


def env_secret_state(env_name: str) -> dict[str, Any]:
    value = os.environ.get(env_name, "") or read_env_file().get(env_name, "")
    return {
        "env": env_name,
        "configured": bool(value),
        "masked": mask_secret(value),
    }


def http_json(url: str, *, method: str = "GET", payload: dict[str, Any] | None = None, headers: dict[str, str] | None = None, timeout: float = 10) -> Any:
    body = None
    final_headers = dict(headers or {})
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        final_headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=body, headers=final_headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as response:
        raw = response.read().decode("utf-8", errors="replace")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        return {"raw": raw}


STATE_PATH_RE = re.compile(r"state=([A-Za-z]:[^\r\n]+?state\.json|\\\\[^\r\n]+?state\.json)")


def state_path_from_logs(logs: list[str]) -> Path | None:
    for line in reversed(logs):
        match = STATE_PATH_RE.search(str(line))
        if match:
            return Path(match.group(1).strip())
    return None


def summarize_runner_state(state: dict[str, Any]) -> dict[str, Any]:
    steps = state.get("steps") or {}
    current = ""
    for step in STEP_ORDER:
        status = (steps.get(step) or {}).get("status")
        if status in {"running", "failed"}:
            current = step
            break
    if not current:
        for step in STEP_ORDER:
            if (steps.get(step) or {}).get("status") not in {"done", "skipped", "queued"}:
                current = step
                break
    return {
        "chapter": state.get("chapter") or {},
        "steps": steps,
        "blocked": state.get("blocked") or {},
        "current_step": current,
        "artifacts": state.get("artifacts") or {},
        "updated_at": state.get("updated_at") or "",
    }


def safe_static_path(request_path: str) -> Path:
    relative = urllib.parse.unquote(request_path.removeprefix("/static/")).replace("\\", "/")
    if not relative or relative.startswith("/") or ".." in relative.split("/"):
        raise FileNotFoundError("invalid static path")
    path = (STATIC_ROOT / relative).resolve()
    root = STATIC_ROOT.resolve()
    if root != path and root not in path.parents:
        raise FileNotFoundError("static path outside root")
    return path


def load_index_html() -> str:
    path = STATIC_ROOT / "index.html"
    if path.is_file():
        return path.read_text(encoding="utf-8")
    return INDEX_HTML


@dataclass
class WebJob:
    id: str
    command: list[str]
    chapter: str
    mode: str
    queue_id: str = ""
    queue_position: int = 0
    status: str = "queued"
    created_at: str = field(default_factory=now_iso)
    started_at: str = ""
    finished_at: str = ""
    returncode: int | None = None
    logs: list[str] = field(default_factory=list)
    process: subprocess.Popen[str] | None = None

    def append_log(self, line: str) -> None:
        self.logs.append(line)
        if len(self.logs) > MAX_LOG_LINES:
            self.logs = self.logs[-MAX_LOG_LINES:]

    def summary(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "chapter": self.chapter,
            "mode": self.mode,
            "queue_id": self.queue_id,
            "queue_position": self.queue_position,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "returncode": self.returncode,
            "command": self.command,
            "log_tail": self.logs[-20:],
        }


@dataclass
class WebQueue:
    id: str
    chapters: list[dict[str, Any]]
    base_payload: dict[str, Any]
    status: str = "queued"
    created_at: str = field(default_factory=now_iso)
    started_at: str = ""
    finished_at: str = ""
    current_index: int = -1
    current_job_id: str = ""
    completed: int = 0
    failed: int = 0
    stop_requested: bool = False
    job_ids: list[str] = field(default_factory=list)
    logs: list[str] = field(default_factory=list)

    def append_log(self, line: str) -> None:
        self.logs.append(line)
        if len(self.logs) > MAX_LOG_LINES:
            self.logs = self.logs[-MAX_LOG_LINES:]

    def summary(self) -> dict[str, Any]:
        total = len(self.chapters)
        current = self.chapters[self.current_index] if 0 <= self.current_index < total else None
        return {
            "id": self.id,
            "status": self.status,
            "created_at": self.created_at,
            "started_at": self.started_at,
            "finished_at": self.finished_at,
            "total": total,
            "completed": self.completed,
            "failed": self.failed,
            "current_index": self.current_index,
            "current": current,
            "current_job_id": self.current_job_id,
            "job_ids": list(self.job_ids),
            "log_tail": self.logs[-30:],
        }


class JobManager:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.jobs: dict[str, WebJob] = {}
        self.queues: dict[str, WebQueue] = {}

    def start(self, command: list[str], chapter: str, mode: str, *, queue_id: str = "", queue_position: int = 0) -> WebJob:
        job = WebJob(id=make_run_id("ui"), command=command, chapter=chapter, mode=mode, queue_id=queue_id, queue_position=queue_position)
        with self.lock:
            self.jobs[job.id] = job
        threading.Thread(target=self._run_job, args=(job,), daemon=True).start()
        return job

    def _run_job(self, job: WebJob) -> None:
        self._run_job_sync(job)

    def _run_job_sync(self, job: WebJob) -> int:
        env = os.environ.copy()
        env.update(read_env_file())
        env["PYTHONUTF8"] = "1"
        env["PYTHONIOENCODING"] = "utf-8"
        env["PYTHONUNBUFFERED"] = "1"

        with self.lock:
            job.status = "running"
            job.started_at = now_iso()
            job.append_log("[ui] command: " + " ".join(job.command))
        print(f"[job:{job.id}] chapter={job.chapter} mode={job.mode} started", flush=True)
        print(f"[job:{job.id}] command: {' '.join(job.command)}", flush=True)

        returncode = 1
        try:
            process = subprocess.Popen(
                job.command,
                cwd=str(RUNNER_ROOT),
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                text=True,
                encoding="utf-8",
                errors="replace",
                env=env,
                bufsize=1,
            )
            with self.lock:
                job.process = process
            assert process.stdout is not None
            for raw in process.stdout:
                line = raw.rstrip("\r\n")
                with self.lock:
                    job.append_log(line)
                print(line, flush=True)
            returncode = process.wait()
            with self.lock:
                job.returncode = returncode
                job.status = "done" if returncode == 0 else "failed"
                job.finished_at = now_iso()
                job.append_log(f"[ui] finished returncode={returncode}")
            print(f"[job:{job.id}] finished returncode={returncode}", flush=True)
        except Exception as exc:
            with self.lock:
                job.status = "failed"
                job.finished_at = now_iso()
                job.append_log(f"[ui] ERROR: {exc}")
            print(f"[job:{job.id}] ERROR: {exc}", flush=True)
            returncode = 1
        return returncode

    def start_queue(self, chapters: list[dict[str, Any]], base_payload: dict[str, Any]) -> WebQueue:
        queue = WebQueue(id=make_run_id("queue"), chapters=chapters, base_payload=base_payload)
        with self.lock:
            running = [row for row in self.queues.values() if row.status in {"queued", "running", "stopping"}]
            if running:
                raise RuntimeError(f"已有队列正在运行: {running[0].id}")
            self.queues[queue.id] = queue
        threading.Thread(target=self._run_queue, args=(queue,), daemon=True).start()
        return queue

    def _run_queue(self, queue: WebQueue) -> None:
        with self.lock:
            queue.status = "running"
            queue.started_at = now_iso()
            queue.append_log(f"[queue] start total={len(queue.chapters)}")
        print(f"[queue:{queue.id}] start total={len(queue.chapters)}", flush=True)

        for index, chapter in enumerate(queue.chapters):
            with self.lock:
                if queue.stop_requested:
                    queue.status = "stopped"
                    queue.finished_at = now_iso()
                    queue.append_log("[queue] stopped before next chapter")
                    break
                queue.current_index = index
                queue.current_job_id = ""
                queue.append_log(f"[queue] chapter {index + 1}/{len(queue.chapters)} -> {chapter['safe_name']}")

            payload = dict(queue.base_payload)
            payload["chapter_index"] = int(chapter["index"])
            payload["chapter_hint"] = str(chapter["safe_name"])
            payload["run_id"] = ""
            command, label, mode = build_runner_command(payload)
            job = WebJob(
                id=make_run_id("ui"),
                command=command,
                chapter=label,
                mode=mode,
                queue_id=queue.id,
                queue_position=index + 1,
            )
            with self.lock:
                self.jobs[job.id] = job
                queue.current_job_id = job.id
                queue.job_ids.append(job.id)
            returncode = self._run_job_sync(job)

            with self.lock:
                if returncode == 0 and not queue.stop_requested:
                    queue.completed += 1
                    queue.append_log(f"[queue] done {chapter['safe_name']}")
                    continue
                if queue.stop_requested:
                    queue.status = "stopped"
                    queue.finished_at = now_iso()
                    queue.append_log(f"[queue] stopped at {chapter['safe_name']} returncode={returncode}")
                else:
                    queue.failed += 1
                    queue.status = "failed"
                    queue.finished_at = now_iso()
                    queue.append_log(f"[queue] failed at {chapter['safe_name']} returncode={returncode}")
                break
        else:
            with self.lock:
                queue.status = "done"
                queue.finished_at = now_iso()
                queue.append_log("[queue] all chapters done")
        with self.lock:
            summary = queue.summary()
        print(f"[queue:{queue.id}] finished status={summary['status']} completed={summary['completed']}/{summary['total']} failed={summary['failed']}", flush=True)

    def list_jobs(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = [job.summary() for job in self.jobs.values()]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows

    def get_job(self, job_id: str) -> dict[str, Any] | None:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job:
                return None
            data = job.summary()
            data["logs"] = list(job.logs)
            state_path = state_path_from_logs(job.logs)
            if state_path:
                data["state_path"] = str(state_path)
                state = read_json_safe(state_path, {})
                if isinstance(state, dict) and state:
                    data["runner_state"] = summarize_runner_state(state)
            return data

    def stop(self, job_id: str) -> bool:
        with self.lock:
            job = self.jobs.get(job_id)
            if not job or not job.process or job.process.poll() is not None:
                return False
            job.status = "stopping"
            job.append_log("[ui] stopping process...")
            job.process.terminate()
            return True

    def list_queues(self) -> list[dict[str, Any]]:
        with self.lock:
            rows = [queue.summary() for queue in self.queues.values()]
        rows.sort(key=lambda row: row["created_at"], reverse=True)
        return rows

    def get_queue(self, queue_id: str) -> dict[str, Any] | None:
        with self.lock:
            queue = self.queues.get(queue_id)
            if not queue:
                return None
            data = queue.summary()
            data["chapters"] = list(queue.chapters)
            data["logs"] = list(queue.logs)
            return data

    def stop_queue(self, queue_id: str) -> bool:
        current_job_id = ""
        with self.lock:
            queue = self.queues.get(queue_id)
            if not queue or queue.status not in {"queued", "running", "stopping"}:
                return False
            queue.stop_requested = True
            queue.status = "stopping"
            queue.append_log("[queue] stop requested")
            current_job_id = queue.current_job_id
        if current_job_id:
            self.stop(current_job_id)
        return True


class AppState:
    def __init__(self, project_root: str, config_path: str = "") -> None:
        self.project_root = project_root
        self.explicit_config_path = config_path
        self.jobs = JobManager()
        saved = read_json_safe(USER_SETTINGS_PATH, {})
        if isinstance(saved, dict) and saved.get("project_root"):
            self.project_root = str(saved["project_root"])

    @property
    def config_path(self) -> str:
        if self.explicit_config_path:
            return self.explicit_config_path
        return str(USER_SETTINGS_PATH) if USER_SETTINGS_PATH.exists() else ""

    def load_cfg(self, project_root: str | None = None) -> dict[str, Any]:
        root = Path(project_root or self.project_root)
        cfg_path = Path(self.config_path) if self.config_path else None
        return load_config(RUNNER_ROOT, root, cfg_path)


APP: AppState


def current_settings() -> dict[str, Any]:
    cfg = APP.load_cfg()
    for key, value in read_env_file().items():
        if key and key not in os.environ:
            os.environ[key] = value
    llm = cfg.get("llm") or {}
    return {
        "project_root": APP.project_root,
        "settings_path": str(USER_SETTINGS_PATH),
        "env_path": str(ENV_PATH),
        "paths": cfg.get("paths") or {},
        "pipeline": cfg.get("pipeline") or {},
        "post": cfg.get("post") or {},
        "comfyui": cfg.get("comfyui") or {},
        "llm": {
            "primary": {
                **(llm.get("primary") or {}),
                "secret": env_secret_state(str((llm.get("primary") or {}).get("api_key_env") or "DEEPSEEK_API_KEY")),
            },
            "fallback": {
                **(llm.get("fallback") or {}),
                "secret": env_secret_state(str((llm.get("fallback") or {}).get("api_key_env") or "GEMINI_API_KEY")),
            },
        },
    }


def save_settings(payload: dict[str, Any]) -> dict[str, Any]:
    project_root = str(payload.get("project_root") or APP.project_root).strip()
    if project_root:
        APP.project_root = project_root

    data = {
        "project_root": APP.project_root,
        "paths": payload.get("paths") or {},
        "pipeline": payload.get("pipeline") or {},
        "llm": payload.get("llm") or {},
        "comfyui": payload.get("comfyui") or {},
        "post": payload.get("post") or {},
    }

    env_updates: dict[str, str] = {}
    for provider in ("primary", "fallback"):
        raw = ((data.get("llm") or {}).get(provider) or {})
        env_name = str(raw.get("api_key_env") or ("DEEPSEEK_API_KEY" if provider == "primary" else "GEMINI_API_KEY"))
        api_key = str(raw.get("api_key") or "")
        if api_key and SENSITIVE_MASK not in api_key:
            env_updates[env_name] = api_key
        raw.pop("api_key", None)
    if env_updates:
        write_env_file(env_updates)
        os.environ.update(env_updates)

    write_json_atomic(USER_SETTINGS_PATH, data)
    return current_settings()


def chapter_artifact_status(project_root: Path, safe_name: str) -> dict[str, Any]:
    visual_plan = project_root / "visual_planning" / f"{safe_name}_director_v2.json"
    visual_groups = project_root / "visual_groups" / f"{safe_name}_visual_groups.json"
    tts_dir = project_root / "tts" / safe_name
    video_dir = project_root / "video_output"
    video = video_dir / f"final_{safe_name}_v7.4.mp4"
    subtitle = video_dir / f"final_{safe_name}_v7.4_TEXT_SUB.mp4"
    wav_count = len(list(tts_dir.glob("*.wav"))) if tts_dir.is_dir() else 0

    stage = "未开始"
    if visual_plan.exists():
        stage = "已导演"
    if wav_count > 0:
        stage = f"TTS {wav_count}"
    if video.exists():
        stage = "视频完成"
    if subtitle.exists():
        stage = "字幕完成"

    return {
        "stage": stage,
        "director": visual_plan.exists(),
        "visual_groups": visual_groups.exists(),
        "tts_dir": tts_dir.exists(),
        "wav_count": wav_count,
        "video": video.exists(),
        "subtitle": subtitle.exists(),
    }


def find_chapters(project_root: str, query: str, page: int = 1, page_size: int = 50) -> dict[str, Any]:
    cfg = APP.load_cfg(project_root)
    entries = load_chapter_index(Path(cfg["paths"]["chapter_index_path"]))
    q = normalize_query(query)
    matched: list[dict[str, Any]] = []
    root = Path(project_root)
    for index, entry in enumerate(entries, 1):
        safe = chapter_safe_name(entry.get("name", ""), entry.get("title", ""))
        full = str(entry.get("fullTitle") or "").strip()
        corpus = normalize_query(" ".join([safe, full, str(index)]))
        if q and q not in corpus:
            continue
        matched.append({
            "index": index,
            "name": entry.get("name") or "",
            "title": entry.get("title") or "",
            "full_title": full,
            "safe_name": safe,
            "status": chapter_artifact_status(root, safe),
        })

    page_size = max(10, min(int(page_size or 50), 5000))
    page = max(1, int(page or 1))
    total = len(matched)
    pages = max(1, (total + page_size - 1) // page_size)
    page = min(page, pages)
    start = (page - 1) * page_size
    return {
        "chapters": matched[start : start + page_size],
        "page": page,
        "page_size": page_size,
        "pages": pages,
        "total": total,
        "all_total": len(entries),
    }


def resolve_chapter_index_from_hint(entries: list[dict[str, Any]], hint: str) -> int:
    query = normalize_query(hint)
    if not query:
        return 0
    for index, entry in enumerate(entries, 1):
        safe = chapter_safe_name(entry.get("name", ""), entry.get("title", ""))
        full = str(entry.get("fullTitle") or "").strip()
        if query in {
            normalize_query(safe),
            normalize_query(full),
            normalize_query(str(index)),
        }:
            return index
    for index, entry in enumerate(entries, 1):
        safe = chapter_safe_name(entry.get("name", ""), entry.get("title", ""))
        full = str(entry.get("fullTitle") or "").strip()
        if query in normalize_query(" ".join([safe, full, str(index)])):
            return index
    return 0


def build_queue_chapters(payload: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    project_root = str(payload.get("project_root") or APP.project_root).strip()
    cfg = APP.load_cfg(project_root)
    entries = load_chapter_index(Path(cfg["paths"]["chapter_index_path"]))
    start_index = int(payload.get("queue_start_index") or payload.get("start_index") or payload.get("chapter_index") or 0)
    end_index = int(payload.get("queue_end_index") or payload.get("end_index") or 0)
    count = int(payload.get("queue_count") or payload.get("count") or 0)

    if start_index <= 0:
        start_index = resolve_chapter_index_from_hint(entries, str(payload.get("chapter_hint") or ""))
    if start_index <= 0:
        raise RuntimeError("队列起始章节未设置，请填写起始章节序号或先从章节库选择一章")
    if count > 0:
        end_index = start_index + count - 1
    if end_index <= 0:
        end_index = start_index
    if end_index < start_index:
        raise RuntimeError(f"队列结束章节不能小于起始章节: {start_index}->{end_index}")
    if end_index > len(entries):
        raise RuntimeError(f"队列结束章节超出章节库: {end_index} > {len(entries)}")

    chapters: list[dict[str, Any]] = []
    for index in range(start_index, end_index + 1):
        entry = entries[index - 1]
        safe = chapter_safe_name(entry.get("name", ""), entry.get("title", ""))
        chapters.append({
            "index": index,
            "name": entry.get("name") or "",
            "title": entry.get("title") or "",
            "full_title": str(entry.get("fullTitle") or "").strip(),
            "safe_name": safe,
        })
    if not chapters:
        raise RuntimeError("队列章节为空")

    base_payload = dict(payload)
    base_payload["project_root"] = project_root
    base_payload["run_id"] = ""
    return chapters, base_payload


def build_runner_command(payload: dict[str, Any]) -> tuple[list[str], str, str]:
    project_root = str(payload.get("project_root") or APP.project_root).strip()
    mode = str(payload.get("mode") or "run").strip()
    if mode not in ("run", "resume"):
        raise RuntimeError(f"unsupported mode: {mode}")
    command = [sys.executable, "-u", "-X", "utf8", str(RUNNER_ROOT / "runner.py"), mode, "--project-root", project_root]
    if APP.config_path:
        command.extend(["--config", APP.config_path])
    chapter = str(payload.get("chapter_hint") or "").strip()
    chapter_index = int(payload.get("chapter_index") or 0)
    if chapter:
        command.extend(["--chapter-hint", chapter])
    if chapter_index > 0:
        command.extend(["--chapter-index", str(chapter_index)])
    run_id = str(payload.get("run_id") or "").strip()
    if run_id:
        command.extend(["--run-id", run_id])
    from_step = str(payload.get("from_step") or "").strip()
    to_step = str(payload.get("to_step") or "director").strip()
    if from_step:
        command.extend(["--from-step", from_step])
    if to_step:
        command.extend(["--to-step", to_step])
    for flag in ("dry_run", "no_llm", "force", "skip_tts", "run_post", "write_windows_job"):
        if bool(payload.get(flag)):
            command.append("--" + flag.replace("_", "-"))
    max_scenes = int(payload.get("max_scenes") or 0)
    if max_scenes > 0:
        command.extend(["--max-scenes", str(max_scenes)])
    label = chapter or (f"chapter_index={chapter_index}" if chapter_index else "auto")
    return command, label, mode


def test_llm(provider: str) -> dict[str, Any]:
    settings = current_settings()
    raw = ((settings.get("llm") or {}).get(provider) or {})
    env_name = str(raw.get("api_key_env") or "")
    api_key = os.environ.get(env_name, "") or read_env_file().get(env_name, "")
    base_url = str(raw.get("base_url") or "").rstrip("/")
    model = str(raw.get("model") or "")
    timeout = float(raw.get("timeout_s") or 60)
    if not base_url or not model:
        raise RuntimeError("base_url/model 未配置")
    if not api_key:
        raise RuntimeError(f"API key 未配置: {env_name}")
    started = time.time()
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": "你只回复 OK。"},
            {"role": "user", "content": "连通性测试。"},
        ],
        "temperature": 0,
        "max_tokens": 8,
    }
    data = http_json(base_url + "/chat/completions", method="POST", payload=payload, headers={"Authorization": f"Bearer {api_key}"}, timeout=timeout)
    content = str((((data.get("choices") or [{}])[0].get("message") or {}).get("content")) or "").strip()
    return {"ok": True, "provider": provider, "model": model, "elapsed_s": round(time.time() - started, 3), "preview": content[:200]}


def test_comfyui() -> dict[str, Any]:
    cfg = APP.load_cfg()
    base = str((cfg.get("comfyui") or {}).get("base_url") or "").rstrip("/")
    if not base:
        raise RuntimeError("ComfyUI 地址未配置")
    started = time.time()
    try:
        stats = http_json(base + "/system_stats", timeout=5)
    except Exception:
        stats = http_json(base + "/object_info", timeout=8)
    return {"ok": True, "base_url": base, "elapsed_s": round(time.time() - started, 3), "keys": list(stats.keys())[:20] if isinstance(stats, dict) else []}


def preflight_check(*, quick_llm: bool = False) -> dict[str, Any]:
    cfg = APP.load_cfg()
    root = Path(APP.project_root)
    paths = cfg.get("paths") or {}
    post = cfg.get("post") or {}
    checks: list[dict[str, Any]] = []

    def add(name: str, ok: bool, detail: str = "", severity: str = "error") -> None:
        checks.append({"name": name, "ok": bool(ok), "detail": detail, "severity": severity})

    add("项目根目录", root.is_dir(), str(root))
    book_path = Path(paths.get("book_path") or "")
    add("book.txt", book_path.is_file(), str(book_path))
    index_path = Path(paths.get("chapter_index_path") or "")
    chapter_count = 0
    try:
        chapter_count = len(load_chapter_index(index_path))
        add("chapter_index.json", chapter_count > 0, f"{index_path} | chapters={chapter_count}")
    except Exception as exc:
        add("chapter_index.json", False, f"{index_path} | {exc}")
    for key in ("visual_planning_dir", "visual_groups_dir", "tts_dir", "windows_jobs_dir", "runs_dir"):
        path = Path(paths.get(key) or "")
        add(key, path.exists(), str(path), severity="warn")
    scripts_dir = Path(paths.get("scripts_dir") or "")
    add("scripts_dir", scripts_dir.is_dir(), str(scripts_dir))
    add("视频脚本", (scripts_dir / str(post.get("video_script") or "shipin_5.py")).is_file(), str(scripts_dir / str(post.get("video_script") or "shipin_5.py")))
    add("字幕脚本", (scripts_dir / str(post.get("subtitle_script") or "zimu_7_text_timed.py")).is_file(), str(scripts_dir / str(post.get("subtitle_script") or "zimu_7_text_timed.py")))
    add("ComfyUI TTS 模板", Path(paths.get("comfyui_tts_template_path") or "").is_file(), str(paths.get("comfyui_tts_template_path") or ""))
    ffmpeg_bin = str(paths.get("ffmpeg_bin") or "ffmpeg")
    ffmpeg_ok = Path(ffmpeg_bin).is_file() or shutil.which(ffmpeg_bin) is not None or shutil.which("ffmpeg") is not None
    add("ffmpeg", ffmpeg_ok, ffmpeg_bin)
    image_pool = root / "images" / "done"
    image_count = len(list(image_pool.rglob("*.png"))) + len(list(image_pool.rglob("*.jpg"))) if image_pool.is_dir() else 0
    add("备用图库 images/done", image_count > 0, f"{image_pool} | images={image_count}", severity="warn")
    for provider in ("primary", "fallback"):
        llm = ((cfg.get("llm") or {}).get(provider) or {})
        env_name = str(llm.get("api_key_env") or "")
        secret = os.environ.get(env_name, "") or read_env_file().get(env_name, "")
        add(f"LLM {provider} 配置", bool(llm.get("base_url") and llm.get("model") and secret), f"{llm.get('base_url')} | {llm.get('model')} | key={mask_secret(secret)}")
    try:
        c = test_comfyui()
        add("ComfyUI 连通", True, f"{c['base_url']} | {c['elapsed_s']}s")
    except Exception as exc:
        add("ComfyUI 连通", False, str(exc))
    if quick_llm:
        for provider in ("primary", "fallback"):
            try:
                result = test_llm(provider)
                add(f"LLM {provider} 连通", True, f"{result['model']} | {result['elapsed_s']}s | {result['preview']}")
            except Exception as exc:
                add(f"LLM {provider} 连通", False, str(exc), severity="warn")
    ok = all(row["ok"] or row["severity"] == "warn" for row in checks)
    errors = sum(1 for row in checks if not row["ok"] and row["severity"] == "error")
    warnings = sum(1 for row in checks if not row["ok"] and row["severity"] == "warn")
    return {"ok": ok, "errors": errors, "warnings": warnings, "chapter_count": chapter_count, "checks": checks, "checked_at": now_iso()}


class Handler(BaseHTTPRequestHandler):
    server_version = "SwordRunnerUI/0.2"

    def do_GET(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/":
            self.send_html(load_index_html())
            return
        if parsed.path.startswith("/static/"):
            try:
                self.send_file(safe_static_path(parsed.path))
            except FileNotFoundError:
                self.send_error(404, "static file not found")
            return
        if parsed.path == "/api/config":
            self.send_json({"project_root": APP.project_root, "steps": STEP_ORDER})
            return
        if parsed.path == "/api/settings":
            self.send_json(current_settings())
            return
        if parsed.path == "/api/chapters":
            qs = urllib.parse.parse_qs(parsed.query)
            project_root = (qs.get("project_root") or [APP.project_root])[0]
            query = (qs.get("q") or [""])[0]
            page = int((qs.get("page") or ["1"])[0])
            page_size = int((qs.get("page_size") or ["50"])[0])
            self.send_json(find_chapters(project_root, query, page=page, page_size=page_size))
            return
        if parsed.path == "/api/preflight":
            qs = urllib.parse.parse_qs(parsed.query)
            quick_llm = (qs.get("quick_llm") or ["0"])[0] in ("1", "true", "yes")
            self.send_json(preflight_check(quick_llm=quick_llm))
            return
        if parsed.path == "/api/jobs":
            self.send_json({"jobs": APP.jobs.list_jobs()})
            return
        if parsed.path == "/api/queues":
            self.send_json({"queues": APP.jobs.list_queues()})
            return
        if parsed.path.startswith("/api/queues/"):
            queue_id = parsed.path.split("/", 3)[-1]
            data = APP.jobs.get_queue(queue_id)
            if not data:
                self.send_error(404, "queue not found")
                return
            self.send_json(data)
            return
        if parsed.path.startswith("/api/jobs/"):
            job_id = parsed.path.split("/", 3)[-1]
            data = APP.jobs.get_job(job_id)
            if not data:
                self.send_error(404, "job not found")
                return
            self.send_json(data)
            return
        self.send_error(404, "not found")

    def do_POST(self) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == "/api/settings":
            try:
                self.send_json(save_settings(self.read_json()))
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/start":
            try:
                payload = self.read_json()
                command, chapter, mode = build_runner_command(payload)
                job = APP.jobs.start(command, chapter, mode)
                self.send_json({"job": job.summary()})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/queue/start":
            try:
                payload = self.read_json()
                chapters, base_payload = build_queue_chapters(payload)
                queue = APP.jobs.start_queue(chapters, base_payload)
                self.send_json({"queue": queue.summary()})
            except Exception as exc:
                self.send_json({"error": str(exc)}, status=400)
            return
        if parsed.path == "/api/test/llm":
            try:
                provider = str(self.read_json().get("provider") or "primary")
                self.send_json(test_llm(provider))
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path == "/api/test/comfyui":
            try:
                self.send_json(test_comfyui())
            except Exception as exc:
                self.send_json({"ok": False, "error": str(exc)}, status=400)
            return
        if parsed.path.startswith("/api/jobs/") and parsed.path.endswith("/stop"):
            parts = parsed.path.split("/")
            job_id = parts[3] if len(parts) >= 5 else ""
            self.send_json({"ok": APP.jobs.stop(job_id)})
            return
        if parsed.path.startswith("/api/queues/") and parsed.path.endswith("/stop"):
            parts = parsed.path.split("/")
            queue_id = parts[3] if len(parts) >= 5 else ""
            self.send_json({"ok": APP.jobs.stop_queue(queue_id)})
            return
        self.send_error(404, "not found")

    def read_json(self) -> dict[str, Any]:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8", errors="replace"))

    def send_html(self, html: str) -> None:
        body = html.encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_file(self, path: Path) -> None:
        if not path.is_file():
            raise FileNotFoundError(str(path))
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        if path.suffix == ".js":
            content_type = "application/javascript; charset=utf-8"
        elif path.suffix == ".css":
            content_type = "text/css; charset=utf-8"
        elif path.suffix == ".html":
            content_type = "text/html; charset=utf-8"
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def send_json(self, data: Any, status: int = 200) -> None:
        body = json.dumps(data, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt: str, *args: Any) -> None:
        status = 0
        if len(args) >= 2:
            try:
                status = int(args[1])
            except (TypeError, ValueError):
                status = 0
        if 200 <= status < 400:
            return
        print(f"[web_ui] {self.address_string()} {fmt % args}", flush=True)


INDEX_HTML = """<!doctype html>
<html lang="zh-CN">
<head><meta charset="utf-8"><title>Sword Audiobook Studio</title></head>
<body><h1>Sword Audiobook Studio</h1><p>static/index.html missing. Please check sword_runner/static.</p></body>
</html>"""

def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Local web UI for sword_runner")
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--config", default="")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true")
    return parser.parse_args()


def main() -> int:
    global APP
    args = parse_args()
    APP = AppState(project_root=args.project_root, config_path=args.config)
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    url = f"http://{args.host}:{args.port}/"
    print(f"[web_ui] Sword Audiobook Studio: {url}", flush=True)
    if not args.no_browser:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\n[web_ui] stopping", flush=True)
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

