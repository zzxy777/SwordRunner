from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path
from typing import Any

from .chapter_loader import Chapter
from .comfyui_client import ComfyUIClient, find_audio_output, find_timing_text
from .io_utils import ensure_dir, make_run_id, write_json_atomic
from .llm_client import LLMClient


VOICE_LINE_RE = re.compile(r"^.+\{[^{}]+\}:\[[^\[\]]+\].+")
FINAL_SCRIPT_PLACEHOLDER = "__FINAL_SCRIPT__"


def fill_prompt_template(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def fallback_voice_script(text: str) -> str:
    clean = str(text or "").strip()
    return f"旁白{{旁白-长篇沉稳}}:[平静]{clean}"


def is_valid_voice_script(script: str) -> bool:
    lines = [line.strip() for line in str(script or "").splitlines() if line.strip()]
    if not lines:
        return False
    valid = 0
    for line in lines:
        if line.startswith("[pause:"):
            valid += 1
        elif VOICE_LINE_RE.match(line):
            valid += 1
    return valid > 0 and valid == len(lines)


def build_voice_scripts(
    chapter: Chapter,
    director_plan: dict[str, Any],
    cfg: dict[str, Any],
    *,
    llm: LLMClient | None = None,
    use_llm: bool = False,
    prompt_path: Path | None = None,
) -> list[dict[str, Any]]:
    prompt_template = prompt_path.read_text(encoding="utf-8-sig") if prompt_path else ""
    rows: list[dict[str, Any]] = []
    for scene in director_plan.get("scenes") or []:
        scene_id = str(scene.get("scene_id") or "").zfill(3)
        text = str(scene.get("narrative") or "").strip()
        if use_llm:
            if not llm or not prompt_template:
                raise RuntimeError("LLM voice director requested but client/prompt is missing")
            prompt = fill_prompt_template(prompt_template, chapter_title=chapter.safe_name, scene_id=scene_id, text=text)
            script = llm.chat("你只输出逐行配音脚本，不输出解释。", prompt, temperature=0.1)
            if not is_valid_voice_script(script):
                raise RuntimeError(f"invalid voice script from LLM: scene={scene_id}, preview={script[:300]}")
        else:
            script = fallback_voice_script(text)
        rows.append({
            "scene_id": scene_id,
            "text": text,
            "final_script": script,
            "save_filename": f"{chapter.safe_name}_{scene_id}.wav",
        })
    return rows


def replace_final_script_placeholder(value: Any, final_script: str) -> tuple[Any, int]:
    if isinstance(value, str):
        if value == FINAL_SCRIPT_PLACEHOLDER:
            return final_script, 1
        return value, 0
    if isinstance(value, list):
        replaced_rows = []
        count = 0
        for item in value:
            new_item, item_count = replace_final_script_placeholder(item, final_script)
            replaced_rows.append(new_item)
            count += item_count
        return replaced_rows, count
    if isinstance(value, dict):
        replaced_dict = {}
        count = 0
        for key, item in value.items():
            new_item, item_count = replace_final_script_placeholder(item, final_script)
            replaced_dict[key] = new_item
            count += item_count
        return replaced_dict, count
    return value, 0


def build_comfy_prompt_payload(
    *,
    template_path: Path,
    final_script: str,
    client_id: str,
) -> dict[str, Any]:
    if not template_path.is_file():
        raise FileNotFoundError(f"ComfyUI TTS template not found: {template_path}")
    template = json.loads(template_path.read_text(encoding="utf-8-sig"))
    payload, replace_count = replace_final_script_placeholder(template, final_script)
    if replace_count != 1:
        raise RuntimeError(f"ComfyUI TTS template must contain exactly one {FINAL_SCRIPT_PLACEHOLDER}, got {replace_count}: {template_path}")
    payload["client_id"] = client_id
    return payload


def convert_audio_to_wav(
    *,
    input_path: Path,
    output_path: Path,
    ffmpeg_bin: str,
    tail_silence_sec: float,
) -> None:
    ensure_dir(output_path.parent)
    ffmpeg = ffmpeg_bin if ffmpeg_bin and Path(ffmpeg_bin).exists() else "ffmpeg"
    audio_filter = f"apad=pad_dur={max(0.0, tail_silence_sec):.3f}" if tail_silence_sec > 0 else "anull"
    cmd = [
        ffmpeg,
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-i",
        str(input_path),
        "-af",
        audio_filter,
        "-ar",
        "48000",
        "-ac",
        "2",
        str(output_path),
    ]
    subprocess.run(cmd, check=True)


def render_tts(
    chapter: Chapter,
    voice_rows: list[dict[str, Any]],
    cfg: dict[str, Any],
    *,
    force: bool = False,
    max_scenes: int | None = None,
) -> dict[str, Any]:
    paths = cfg.get("paths") or {}
    comfy_cfg = cfg.get("comfyui") or {}
    pipe = cfg.get("pipeline") or {}
    tts_dir = ensure_dir(Path(paths["tts_dir"]) / chapter.safe_name)
    temp_dir = ensure_dir(tts_dir / "_tmp_script_runner")
    timing_dir = ensure_dir(tts_dir / "_timing")
    template_path = Path(paths["comfyui_tts_template_path"])
    ffmpeg_bin = str(paths.get("ffmpeg_bin") or "ffmpeg")
    tail = float(pipe.get("tts_tail_silence_sec") or 0)

    client = ComfyUIClient(
        str(comfy_cfg.get("base_url") or ""),
        queue_timeout_s=float(comfy_cfg.get("queue_timeout_s") or 30),
        history_timeout_s=float(comfy_cfg.get("history_timeout_s") or 900),
        poll_interval_s=float(comfy_cfg.get("poll_interval_s") or 3),
    )
    prefix = str(comfy_cfg.get("client_id_prefix") or "sword_runner")
    manifest: list[dict[str, Any]] = []
    selected_rows = voice_rows[:max_scenes] if max_scenes else voice_rows

    for row in selected_rows:
        scene_id = str(row["scene_id"]).zfill(3)
        out_wav = tts_dir / str(row["save_filename"])
        if out_wav.exists() and out_wav.stat().st_size > 1024 and not force:
            manifest.append({"scene_id": scene_id, "status": "exists", "path": str(out_wav)})
            continue

        client_id = f"{prefix}_{chapter.safe_name}_{scene_id}_{make_run_id('tts')}"
        payload = build_comfy_prompt_payload(
            template_path=template_path,
            final_script=str(row["final_script"]),
            client_id=client_id,
        )
        prompt_id = client.post_prompt(payload)
        history = client.wait_history(prompt_id)
        audio_info = find_audio_output(history)
        raw_bytes = client.download_view(
            filename=audio_info["filename"],
            file_type=audio_info["type"],
            subfolder=audio_info["subfolder"],
        )

        raw_path = temp_dir / f"{chapter.safe_name}_{scene_id}.download"
        raw_path.write_bytes(raw_bytes)
        convert_audio_to_wav(
            input_path=raw_path,
            output_path=out_wav,
            ffmpeg_bin=ffmpeg_bin,
            tail_silence_sec=tail,
        )

        timing_text = find_timing_text(history)
        timing_path = ""
        if timing_text:
            try:
                timing = json.loads(timing_text)
                timing["scene_id"] = scene_id
                timing["chapter_title"] = chapter.safe_name
                timing["audio_filename"] = out_wav.name
                timing_path = str(timing_dir / f"{chapter.safe_name}_{scene_id}.tts_timing.json")
                write_json_atomic(Path(timing_path), timing)
            except Exception:
                timing_path = ""

        manifest.append({
            "scene_id": scene_id,
            "status": "generated",
            "path": str(out_wav),
            "prompt_id": prompt_id,
            "source_audio": audio_info,
            "timing_path": timing_path,
        })

    write_json_atomic(tts_dir / "script_runner_tts_manifest.json", manifest)
    return {
        "tts_dir": str(tts_dir),
        "count": len(manifest),
        "generated": sum(1 for item in manifest if item["status"] == "generated"),
        "exists": sum(1 for item in manifest if item["status"] == "exists"),
        "manifest": manifest,
    }
