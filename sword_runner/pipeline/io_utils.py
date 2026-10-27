from __future__ import annotations

import json
import os
import re
import time
from datetime import datetime
from pathlib import Path
from typing import Any


WINDOWS_FORBIDDEN = r'<>:"/\|?*'


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d%H%M%S")


def make_run_id(prefix: str = "script") -> str:
    return f"{prefix}_{now_stamp()}_{int(time.time() * 1000) % 100000:05d}"


def normalize_slashes(value: str) -> str:
    return str(value).replace("\\", "/")


def sanitize_filename(value: str, default: str = "untitled") -> str:
    text = str(value or "").strip()
    for ch in WINDOWS_FORBIDDEN:
        text = text.replace(ch, "_")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"_+", "_", text)
    text = text.strip("._ ")
    return text or default


def chapter_safe_name(name: str, title: str = "") -> str:
    name = str(name or "").strip()
    title = str(title or "").strip()
    if title:
        return sanitize_filename(f"{name}_{title}")
    return sanitize_filename(name)


def ensure_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def read_text(path: Path) -> str:
    return path.read_text(encoding="utf-8-sig")


def read_json(path: Path) -> Any:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def write_json_atomic(path: Path, data: Any) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def write_text_atomic(path: Path, text: str) -> None:
    ensure_dir(path.parent)
    tmp = path.with_suffix(path.suffix + f".tmp.{os.getpid()}")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def compact_text(value: str) -> str:
    text = str(value or "")
    text = text.replace("\ufeff", "")
    text = re.sub(r"[ \t\r\f\v\u3000]+", "", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def log(message: str) -> None:
    print(f"[sword_runner] {message}", flush=True)
