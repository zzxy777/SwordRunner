from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .io_utils import chapter_safe_name, compact_text, read_json, read_text


@dataclass
class Chapter:
    index: int
    name: str
    title: str
    full_title: str
    safe_name: str
    text: str
    source_start: int | None = None
    source_end: int | None = None


def normalize_hint(value: str) -> str:
    return re.sub(r"[\s_]+", "", str(value or "").strip().lower())


def load_chapter_index(path: Path) -> list[dict[str, Any]]:
    data = read_json(path)
    if not isinstance(data, list):
        raise RuntimeError(f"chapter_index must be a list: {path}")
    return data


def choose_entry(
    entries: list[dict[str, Any]],
    chapter_hint: str = "",
    chapter_index: int | None = None,
) -> tuple[int, dict[str, Any]]:
    if chapter_index:
        if chapter_index < 1 or chapter_index > len(entries):
            raise RuntimeError(f"chapter_index out of range: {chapter_index}, total={len(entries)}")
        return chapter_index, entries[chapter_index - 1]

    hint = normalize_hint(chapter_hint)
    if not hint or hint == "auto":
        return len(entries), entries[-1]

    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for idx, entry in enumerate(entries, 1):
        safe = chapter_safe_name(entry.get("name", ""), entry.get("title", ""))
        corpus = normalize_hint(" ".join([
            str(entry.get("name") or ""),
            str(entry.get("title") or ""),
            str(entry.get("fullTitle") or ""),
            safe,
        ]))
        if corpus == hint:
            ranked.append((0, idx, entry))
        elif hint in corpus or corpus in hint:
            ranked.append((1, idx, entry))

    if not ranked:
        raise RuntimeError(f"chapter not found by hint: {chapter_hint}")
    ranked.sort(key=lambda item: (item[0], -item[1]))
    _, idx, entry = ranked[0]
    return idx, entry


def strip_heading(text: str, entry: dict[str, Any]) -> str:
    full = str(entry.get("fullTitle") or "").strip()
    name = str(entry.get("name") or "").strip()
    title = str(entry.get("title") or "").strip()
    candidates = [full, f"{name} {title}".strip(), f"{name}_{title}".strip("_")]
    out = text.lstrip("\ufeff\r\n ")
    for candidate in candidates:
        if candidate and out.startswith(candidate):
            out = out[len(candidate):]
            break
    return compact_text(out)


def load_chapter(
    project_root: Path,
    chapter_index_path: Path,
    book_path: Path,
    chapter_hint: str = "",
    chapter_index: int | None = None,
) -> Chapter:
    entries = load_chapter_index(chapter_index_path)
    idx, entry = choose_entry(entries, chapter_hint=chapter_hint, chapter_index=chapter_index)
    book = read_text(book_path)

    start = int(entry.get("start") or 0)
    end = int(entry.get("end") or len(book))
    raw = book[start:end]
    text = strip_heading(raw, entry)

    name = str(entry.get("name") or f"chapter_{idx}").strip()
    title = str(entry.get("title") or "").strip()
    full_title = str(entry.get("fullTitle") or f"{name} {title}".strip()).strip()
    safe_name = chapter_safe_name(name, title)
    return Chapter(
        index=idx,
        name=name,
        title=title,
        full_title=full_title,
        safe_name=safe_name,
        text=text,
        source_start=start,
        source_end=end,
    )

