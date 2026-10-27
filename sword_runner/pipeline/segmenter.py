from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from .chapter_loader import Chapter
from .io_utils import compact_text
from .llm_client import LLMClient, extract_json_array


SENTENCE_RE = re.compile(r"[^。！？!?；;]+(?:[。！？!?；;][”’」』]*)?")


def fill_prompt_template(template: str, **values: str) -> str:
    out = template
    for key, value in values.items():
        out = out.replace("{" + key + "}", str(value))
    return out


def split_long_text(text: str, max_chars: int) -> list[str]:
    parts: list[str] = []
    remain = text
    while len(remain) > max_chars:
        cut = max(remain.rfind("，", 0, max_chars), remain.rfind("、", 0, max_chars))
        if cut < max_chars * 0.55:
            cut = max_chars
        parts.append(remain[: cut + 1].strip())
        remain = remain[cut + 1 :].strip()
    if remain:
        parts.append(remain)
    return parts


def rule_segment_text(
    text: str,
    *,
    min_chars: int = 80,
    target_chars: int = 140,
    max_chars: int = 180,
) -> list[str]:
    clean = compact_text(text).replace("\n", "")
    raw_sentences = [m.group(0).strip() for m in SENTENCE_RE.finditer(clean) if m.group(0).strip()]
    sentences: list[str] = []
    for sentence in raw_sentences:
        if len(sentence) > max_chars:
            sentences.extend(split_long_text(sentence, target_chars))
        else:
            sentences.append(sentence)

    out: list[str] = []
    current = ""
    for sentence in sentences:
        if not current:
            current = sentence
            continue
        if len(current) < min_chars or len(current) + len(sentence) <= max_chars:
            current += sentence
        else:
            out.append(current)
            current = sentence
    if current:
        out.append(current)

    fixed: list[str] = []
    for item in out:
        if len(item) <= max_chars:
            fixed.append(item)
        else:
            fixed.extend(split_long_text(item, target_chars))
    return [x.strip() for x in fixed if x.strip()]


def chunk_text_for_llm(text: str, chunk_chars: int) -> list[str]:
    clean = compact_text(text).replace("\n", "")
    chunks: list[str] = []
    current = ""
    for sentence in [m.group(0) for m in SENTENCE_RE.finditer(clean)]:
        if len(current) + len(sentence) > chunk_chars and current:
            chunks.append(current)
            current = sentence
        else:
            current += sentence
    if current:
        chunks.append(current)
    return chunks


def segment_with_llm(
    chapter: Chapter,
    cfg: dict[str, Any],
    llm: LLMClient,
    prompt_path: Path,
) -> list[str]:
    pipe = cfg.get("pipeline") or {}
    chunk_chars = int(pipe.get("llm_chunk_chars") or 3200)
    all_segments: list[str] = []
    prompt_template = prompt_path.read_text(encoding="utf-8-sig")

    for idx, chunk in enumerate(chunk_text_for_llm(chapter.text, chunk_chars), 1):
        prompt = fill_prompt_template(
            prompt_template,
            chapter_title=chapter.safe_name,
            text=chunk,
        )
        content = llm.chat(
            "你只输出可解析 JSON，不输出解释。",
            prompt,
            temperature=0.1,
        )
        rows = extract_json_array(content)
        for row in rows:
            text = str(row.get("text") if isinstance(row, dict) else row).strip()
            if text:
                all_segments.append(text)
    return all_segments


def build_segments(
    chapter: Chapter,
    cfg: dict[str, Any],
    *,
    llm: LLMClient | None = None,
    use_llm: bool = False,
    prompt_path: Path | None = None,
) -> list[dict[str, Any]]:
    pipe = cfg.get("pipeline") or {}
    min_chars = int(pipe.get("segment_min_chars") or 80)
    target_chars = int(pipe.get("segment_target_chars") or 140)
    max_chars = int(pipe.get("segment_max_chars") or 180)

    if use_llm:
        if not llm or not prompt_path:
            raise RuntimeError("LLM segmentation requested but client/prompt is missing")
        texts = segment_with_llm(chapter, cfg, llm, prompt_path)
    else:
        texts = rule_segment_text(
            chapter.text,
            min_chars=min_chars,
            target_chars=target_chars,
            max_chars=max_chars,
        )

    return [
        {
            "scene_id": str(index).zfill(3),
            "text": text,
            "char_count": len(text),
        }
        for index, text in enumerate(texts, 1)
    ]
