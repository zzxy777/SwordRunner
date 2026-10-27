from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from .chapter_loader import Chapter
from .io_utils import ensure_dir, now_iso, write_json_atomic


def run_python_script(script_path: Path, project_root: Path, run_id: str, chapter_hint: str) -> int:
    cmd = [
        sys.executable,
        "-u",
        "-X",
        "utf8",
        str(script_path),
        "--project-root",
        str(project_root),
        "--run-id",
        run_id,
        "--chapter-hint",
        chapter_hint,
    ]
    env = os.environ.copy()
    env["PYTHONUTF8"] = "1"
    env["PYTHONIOENCODING"] = "utf-8"
    process = subprocess.Popen(
        cmd,
        cwd=str(script_path.parent),
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        env=env,
    )
    assert process.stdout is not None
    for line in process.stdout:
        print(f"[{script_path.name}] {line.rstrip()}", flush=True)
    return int(process.wait())


def run_video_and_subtitle(chapter: Chapter, cfg: dict[str, Any], run_id: str) -> None:
    scripts_dir = Path(cfg["paths"]["scripts_dir"])
    post = cfg.get("post") or {}
    video_script = scripts_dir / str(post.get("video_script") or "shipin_5.py")
    subtitle_script = scripts_dir / str(post.get("subtitle_script") or "zimu_7_text_timed.py")
    for script in (video_script, subtitle_script):
        if not script.exists():
            raise FileNotFoundError(f"post script not found: {script}")
        code = run_python_script(script, Path(cfg["project_root"]), run_id, chapter.safe_name)
        if code != 0:
            raise RuntimeError(f"{script.name} failed with code={code}")


def write_windows_job(chapter: Chapter, cfg: dict[str, Any], run_id: str) -> Path:
    post = cfg.get("post") or {}
    jobs_dir = ensure_dir(Path(cfg["paths"]["windows_jobs_dir"]))
    path = jobs_dir / f"{run_id}_job.json"
    payload = {
        "run_id": run_id,
        "workflow_run_id": run_id,
        "chapter_index": chapter.index,
        "chapter_hint": chapter.safe_name,
        "project_root": cfg["project_root"],
        "scripts_dir": cfg["paths"]["scripts_dir"],
        "video_script": post.get("video_script") or "shipin_5.py",
        "subtitle_script": post.get("subtitle_script") or "zimu_7_text_timed.py",
        "callback_url": post.get("callback_url") or "",
        "status": "ready_for_windows_scripts",
        "created_at": now_iso(),
        "started_at": "",
        "finished_at": "",
        "error": "",
        "retry_count": 0,
        "trigger_from": "sword_runner",
    }
    write_json_atomic(path, payload)
    return path

