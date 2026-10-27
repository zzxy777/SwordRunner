from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from typing import Any


class ComfyUIClient:
    def __init__(self, base_url: str, *, queue_timeout_s: float = 30, history_timeout_s: float = 900, poll_interval_s: float = 3):
        self.base_url = base_url.rstrip("/")
        self.queue_timeout_s = queue_timeout_s
        self.history_timeout_s = history_timeout_s
        self.poll_interval_s = poll_interval_s

    def post_prompt(self, payload: dict[str, Any]) -> str:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base_url + "/prompt",
            data=body,
            headers={"Content-Type": "application/json; charset=utf-8"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.queue_timeout_s) as response:
            data = json.loads(response.read().decode("utf-8", errors="replace"))
        prompt_id = data.get("prompt_id")
        if not prompt_id:
            raise RuntimeError(f"ComfyUI returned no prompt_id: {data}")
        return str(prompt_id)

    def get_history(self, prompt_id: str) -> dict[str, Any]:
        req = urllib.request.Request(self.base_url + "/history/" + urllib.parse.quote(prompt_id))
        with urllib.request.urlopen(req, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", errors="replace"))

    def wait_history(self, prompt_id: str) -> dict[str, Any]:
        deadline = time.time() + self.history_timeout_s
        while time.time() < deadline:
            history = self.get_history(prompt_id)
            if history:
                run = history.get(prompt_id) or next(iter(history.values()), None)
                if run and run.get("outputs"):
                    return history
            time.sleep(self.poll_interval_s)
        raise TimeoutError(f"ComfyUI history timeout: prompt_id={prompt_id}")

    def download_view(self, *, filename: str, file_type: str = "output", subfolder: str = "") -> bytes:
        query = urllib.parse.urlencode({
            "filename": filename,
            "type": file_type,
            "subfolder": subfolder,
        })
        with urllib.request.urlopen(self.base_url + "/view?" + query, timeout=120) as response:
            return response.read()


def find_audio_output(history: dict[str, Any], node_id: str = "93") -> dict[str, str]:
    run = next((value for value in history.values() if isinstance(value, dict) and value.get("outputs")), None)
    if not run:
        raise RuntimeError("ComfyUI history has no outputs")
    outputs = run.get("outputs") or {}
    audio_rows = ((outputs.get(node_id) or {}).get("audio") or [])
    if not audio_rows:
        for output in outputs.values():
            audio_rows = (output or {}).get("audio") or []
            if audio_rows:
                break
    if not audio_rows:
        raise RuntimeError("ComfyUI history has no audio output")
    row = audio_rows[0]
    return {
        "filename": str(row.get("filename") or ""),
        "type": str(row.get("type") or "output"),
        "subfolder": str(row.get("subfolder") or ""),
    }


def find_timing_text(history: dict[str, Any], node_id: str = "101") -> str:
    run = next((value for value in history.values() if isinstance(value, dict) and value.get("outputs")), None)
    if not run:
        return ""
    output = (run.get("outputs") or {}).get(node_id) or {}
    candidates = []
    for key in ("string", "text"):
        value = output.get(key)
        if isinstance(value, list):
            candidates.extend(value)
        elif isinstance(value, str):
            candidates.append(value)
    return next((str(item).strip() for item in candidates if str(item).strip()), "")

