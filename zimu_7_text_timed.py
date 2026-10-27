#!/usr/bin/env python
"""Text-timed subtitle prototype for SwordComing.

This script is intentionally independent from the production watcher. It builds
subtitle timing from known director text plus per-scene TTS wav durations, then
burns an ASS subtitle track into a test mp4.
"""

from __future__ import annotations

import argparse
import json
import math
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any


DEFAULT_PROJECT_ROOT = os.environ.get("SWORD_RUNNER_PROJECT_ROOT", str(Path.home() / "SwordAudiobookProject"))

# ================= 用户常用微调区 =================
# ASS 颜色格式是 &HAABBGGRR：
#   暖白/米白: &H00DCF0F5
#   纯白:     &H00FFFFFF
#   经典黄:   &H0000FFFF
#   黑色:     &H00000000
# 数字改法：
#   size 越大字越大；pos_y 越大字幕越靠下；outline_width 越大黑边越粗。
FONT_CONFIG = {
    "font": "Microsoft YaHei",
    "size": 70,
    "primary": "&H00DCF0F5",
    "outline": "&H00141414",
    "back": "&H70000000",
    "outline_width": 4,
    "shadow": 1,
    "margin_v": 36,
    "pos_x": 640,
    "pos_y": 665,
    "fade_ms": 40,
}

SUBTITLE_CONFIG = {
    # True = 尽量横排单行；False = 按 line_max_chars 自动折成两行。
    "one_line": True,
    # 每条字幕最多多少字。越大越长，越小切得越碎。
    "max_chars": 20,
    "min_chars": 4,
    # one_line=False 时生效；例如 15 会比较容易变两行。
    "line_max_chars": 20,
    "min_duration": 0.95,
    "max_duration": 3.40,
    "gap": 0.02,
    "scene_lead": 0.02,
    "scene_tail": 0.04,
    "voice_offset": 0.05,
    "detect_leading_silence": True,
    "leading_silence_noise": "-45dB",
    "leading_silence_min_duration": 0.05,
    "leading_silence_max": 0.60,
    "comfort_merge_gap": 0.24,
    "comfort_merge_max_chars": 26,
    "comfort_merge_max_duration": 4.20,
    "comfort_min_display": 0.95,
    "comfort_linger": 0.10,
    "comfort_gap": 0.018,
    "comfort_short_display": 0.80,
    "comfort_short_merge_gap": 0.65,
    "comfort_short_merge_max_chars": 32,
    "timeline_min_display": 0.30,
    "timeline_min_gap": 0.005,
    "timeline_end_margin": 0.02,
}

PUNCTUATION = "，。！？；：,.!?;:、"
SENTENCE_END = "。！？.!?"
SOFT_END = "，；：,;:、"
CONTROL_TAG_RE = re.compile(r"\[(PAUSE|SLOW|SOFT|FAST|BREATH|silence:[^\]]+|pause:[^\]]+)\]", re.I)


def log(message: str) -> None:
    print(f"[zimu_7_text_timed] {message}", flush=True)


def run_command(cmd: list[str], desc: str, verbose: bool = True) -> subprocess.CompletedProcess[bytes]:
    if verbose:
        log(f"{desc}: {' '.join(cmd)}")
    return subprocess.run(cmd, check=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)


def safe_decode(data: bytes | None) -> str:
    if not data:
        return ""
    for encoding in ("utf-8", "gbk", "mbcs"):
        try:
            return data.decode(encoding, errors="replace")
        except LookupError:
            continue
    return data.decode(errors="replace")


def normalize_token(value: Any) -> str:
    text = str(value or "").strip().lower()
    text = re.sub(r"_director_v\d+\.json$", "", text, flags=re.I)
    text = re.sub(r"_director.*$", "", text, flags=re.I)
    text = re.sub(r"\.(json|mp4|wav|srt|ass)$", "", text, flags=re.I)
    text = re.sub(r"^final_", "", text, flags=re.I)
    text = re.sub(r"_v\d+(?:\.\d+)?(?:_.*)?$", "", text, flags=re.I)
    text = re.sub(r"[\s\\/:*?\"<>|]+", "_", text)
    return text.strip("_")


def text_len(value: str) -> int:
    return len(re.sub(r"\s+", "", value or ""))


def locate_latest_file(directory: Path, pattern: str, chapter_hint: str, strict: bool = True) -> Path:
    files = [p for p in directory.glob(pattern) if p.is_file()]
    if not files:
        raise FileNotFoundError(f"no files matched {directory}\\{pattern}")

    hint = normalize_token(chapter_hint)
    if hint and hint != "auto":
        matched = [p for p in files if hint in normalize_token(p.name)]
        if matched:
            return max(matched, key=lambda p: p.stat().st_mtime)
        if strict:
            raise FileNotFoundError(f"no file matched chapter hint: {chapter_hint}")

    return max(files, key=lambda p: p.stat().st_mtime)


def is_base_video(path: Path) -> bool:
    name = path.name
    upper = name.upper()
    if any(token in upper for token in ("_AI_SUB", "_TEXT_SUB", "_SUBTITLE")):
        return False
    return bool(re.search(r"_v\d+(?:\.\d+)?\.mp4$", name, flags=re.I))


def locate_latest_base_video(directory: Path, chapter_hint: str, strict: bool = True) -> Path:
    files = [p for p in directory.glob("final_*.mp4") if p.is_file() and is_base_video(p)]
    if not files:
        raise FileNotFoundError(f"no base no-subtitle mp4 found in: {directory}")

    hint = normalize_token(chapter_hint)
    if hint and hint != "auto":
        matched = [p for p in files if hint in normalize_token(p.name)]
        if matched:
            return max(matched, key=lambda p: p.stat().st_mtime)
        if strict:
            raise FileNotFoundError(f"no base no-subtitle mp4 matched chapter hint: {chapter_hint}")

    return max(files, key=lambda p: p.stat().st_mtime)


def locate_tts_dir(root: Path, chapter_hint: str, plan_path: Path) -> Path:
    tts_root = root / "tts"
    if not tts_root.is_dir():
        raise FileNotFoundError(f"tts root not found: {tts_root}")

    candidates = [normalize_token(chapter_hint), normalize_token(plan_path.stem)]
    for directory in tts_root.iterdir():
        if not directory.is_dir():
            continue
        token = normalize_token(directory.name)
        if any(c and c != "auto" and (c in token or token in c) for c in candidates):
            return directory
    raise FileNotFoundError(f"tts dir not found for chapter: {chapter_hint}")


def ffprobe_duration(path: Path) -> float:
    result = run_command(
        [
            "ffprobe",
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(path),
        ],
        desc="ffprobe duration",
        verbose=False,
    )
    return float(safe_decode(result.stdout).strip())


def detect_leading_silence(path: Path, cache: dict[Path, float] | None = None) -> float:
    if cache is not None and path in cache:
        return cache[path]

    cfg = SUBTITLE_CONFIG
    if not bool(cfg.get("detect_leading_silence", True)):
        if cache is not None:
            cache[path] = 0.0
        return 0.0

    max_lead = max(0.0, float(cfg.get("leading_silence_max", 0.6)))
    noise = str(cfg.get("leading_silence_noise", "-45dB"))
    min_duration = max(0.01, float(cfg.get("leading_silence_min_duration", 0.05)))
    try:
        result = run_command(
            [
                "ffmpeg",
                "-hide_banner",
                "-nostats",
                "-i",
                str(path),
                "-af",
                f"silencedetect=noise={noise}:d={min_duration:.3f}",
                "-f",
                "null",
                "-",
            ],
            desc="detect leading silence",
            verbose=False,
        )
        text = safe_decode(result.stderr)
    except Exception:
        text = ""

    leading = 0.0
    starts_at_zero = re.search(r"silence_start:\s*0(?:\.0+)?\b", text)
    end_match = re.search(r"silence_end:\s*([0-9.]+)\s*\|", text)
    if starts_at_zero and end_match:
        try:
            leading = min(max(float(end_match.group(1)), 0.0), max_lead)
        except ValueError:
            leading = 0.0

    if cache is not None:
        cache[path] = leading
    return leading


def load_scenes(plan_path: Path) -> tuple[str, list[dict[str, str]]]:
    with plan_path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)

    chapter = str(data.get("chapter") or plan_path.stem.replace("_director_v2", ""))
    raw_scenes = data.get("scenes") or data.get("data", {}).get("scenes") or []
    scenes: list[dict[str, str]] = []

    for index, scene in enumerate(raw_scenes, 1):
        scene_id = str(scene.get("scene_id") or index).strip()
        if scene_id.isdigit():
            scene_id = scene_id.zfill(3)
        text = str(scene.get("narrative") or scene.get("narration") or "").strip()
        if not text:
            continue
        scenes.append({"scene_id": scene_id, "text": clean_script_text(text)})

    if not scenes:
        raise RuntimeError(f"no usable scenes in plan: {plan_path}")
    return chapter, scenes


def clean_script_text(value: str) -> str:
    text = CONTROL_TAG_RE.sub("。", value or "")
    text = re.sub(r"\[[^\]]+\]", "", text)
    text = re.sub(r"\s+", "", text)
    text = re.sub(r"。{2,}", "。", text)
    return text.strip("，。；：、,. ")


def tts_file_map(tts_dir: Path) -> dict[str, Path]:
    result: dict[str, Path] = {}
    for wav in sorted(tts_dir.glob("*.wav")):
        match = re.search(r"_(\d{3,4})\.wav$", wav.name, flags=re.I)
        if match:
            result[match.group(1)[-3:]] = wav
    return result


def maybe_repair_mojibake(text: str) -> str:
    """Repair UTF-8 text that was accidentally decoded as GBK."""
    if not text:
        return ""
    try:
        repaired = text.encode("gbk").decode("utf-8")
    except UnicodeError:
        return text
    punct = "\u3002\uff0c\uff01\uff1f\uff1b\uff1a\u3001"
    original_score = sum(text.count(ch) for ch in punct)
    repaired_score = sum(repaired.count(ch) for ch in punct)
    return repaired if repaired_score > original_score else text


def clean_subtitle_text(text: str) -> str:
    text = maybe_repair_mojibake(str(text or ""))
    text = CONTROL_TAG_RE.sub("", text)
    text = re.sub(r"\[(?!pause:)[^\]]{1,16}\]\s*", "", text, flags=re.I)
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def load_cue_sheets(tts_dir: Path) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    sources = (
        (tts_dir / "_timing", "*.tts_timing.json", r"_(\d{3,4})\.tts_timing\.json$"),
        (tts_dir / "_mix_debug", "*.cue_sheet_debug.json", r"_(\d{3,4})\.cue_sheet_debug\.json$"),
    )
    for directory, pattern, scene_pattern in sources:
        if not directory.is_dir():
            continue
        for path in sorted(directory.glob(pattern)):
            match = re.search(scene_pattern, path.name, flags=re.I)
            if not match:
                continue
            scene_id = match.group(1)[-3:]
            if scene_id in result:
                continue
            try:
                with path.open("r", encoding="utf-8-sig") as handle:
                    data = json.load(handle)
            except (OSError, json.JSONDecodeError):
                continue
            result[scene_id] = data
    return result


def locate_subtitle_timeline(root: Path, video_path: Path, chapter_hint: str, explicit_path: str = "") -> Path | None:
    if explicit_path:
        path = Path(explicit_path)
        return path if path.is_file() else None

    direct = Path(f"{video_path.with_suffix('')}_subtitle_timeline.json")
    if direct.is_file():
        return direct

    output_dir = root / "video_output"
    if not output_dir.is_dir():
        return None

    files = [p for p in output_dir.glob("*_subtitle_timeline.json") if p.is_file()]
    if not files:
        return None

    video_stem = normalize_token(video_path.stem)
    direct_matches = [p for p in files if video_stem and video_stem in normalize_token(p.stem)]
    if direct_matches:
        return max(direct_matches, key=lambda p: p.stat().st_mtime)

    hint = normalize_token(chapter_hint)
    hint_matches = [p for p in files if hint and hint != "auto" and hint in normalize_token(p.name)]
    if hint_matches:
        return max(hint_matches, key=lambda p: p.stat().st_mtime)
    return None


def load_subtitle_timeline(path: Path | None) -> dict[str, dict[str, Any]]:
    if not path:
        return {}
    with path.open("r", encoding="utf-8-sig") as handle:
        data = json.load(handle)
    result: dict[str, dict[str, Any]] = {}
    for item in data.get("scenes") or []:
        scene_id = str(item.get("scene_id") or "").strip()
        if not scene_id:
            continue
        try:
            start = float(item.get("start") or 0.0)
            end = float(item.get("end") or start)
            duration = float(item.get("duration") or max(0.0, end - start))
            source_duration = float(item.get("source_duration") or 0.0)
            clipped_seconds = float(item.get("clipped_seconds") or 0.0)
        except (TypeError, ValueError):
            continue
        result[scene_id] = {
            "start": max(0.0, start),
            "end": max(0.0, end),
            "duration": max(0.0, duration),
            "source_duration": max(0.0, source_duration),
            "group_index": item.get("group_index"),
            "clipped_seconds": max(0.0, clipped_seconds),
        }
    return result


def split_by_punctuation(text: str) -> list[str]:
    parts: list[str] = []
    current: list[str] = []
    for ch in text:
        current.append(ch)
        if ch in PUNCTUATION:
            segment = "".join(current).strip()
            if segment:
                parts.append(segment)
            current = []
    tail = "".join(current).strip()
    if tail:
        parts.append(tail)
    return parts


def split_long_text(text: str, max_chars: int) -> list[str]:
    if text_len(text) <= max_chars:
        return [text]

    chunks: list[str] = []
    rest = text
    while text_len(rest) > max_chars:
        cut = pick_cut_index(rest, max_chars)
        chunks.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        chunks.append(rest)
    return [c for c in chunks if c]


def pick_cut_index(text: str, max_chars: int) -> int:
    best = -1
    limit = min(len(text), max_chars + 4)
    floor = max(4, int(max_chars * 0.55))
    for idx in range(min(limit, len(text)) - 1, floor - 1, -1):
        if text[idx] in SOFT_END:
            best = idx + 1
            break
    if best > 0:
        return best

    # Chinese text has no spaces, so fall back to a visual length cut.
    count = 0
    for idx, ch in enumerate(text):
        if not ch.isspace():
            count += 1
        if count >= max_chars:
            return idx + 1
    return min(len(text), max_chars)


def semantic_segments(text: str, max_chars: int, min_chars: int) -> list[str]:
    raw_parts: list[str] = []
    for clause in split_by_punctuation(text):
        raw_parts.extend(split_long_text(clause, max_chars))

    merged: list[str] = []
    current = ""
    for part in raw_parts:
        if not current:
            current = part
            continue
        merged_len = text_len(current + part)
        if text_len(current) < min_chars or merged_len <= max_chars:
            current += part
        else:
            merged.append(current)
            current = part
    if current:
        merged.append(current)

    # Avoid orphan very short tail subtitles.
    balanced: list[str] = []
    for part in merged:
        if balanced and text_len(part) < min_chars and text_len(balanced[-1] + part) <= max_chars + 4:
            balanced[-1] += part
        else:
            balanced.append(part)
    return balanced or [text]


def segment_weight(text: str) -> float:
    length = max(1, text_len(text))
    bonus = 0.0
    if text and text[-1] in SENTENCE_END:
        bonus += 2.0
    elif text and text[-1] in SOFT_END:
        bonus += 0.9
    return length + bonus


def allocate_durations(weights: list[float], total: float, min_duration: float, max_duration: float) -> list[float]:
    count = len(weights)
    if count == 0:
        return []
    if total <= 0:
        return [min_duration for _ in weights]

    # Relax bounds when the scene is too short or too long for the segment count.
    min_d = min(min_duration, total / count * 0.82)
    max_d = max(max_duration, total / count * 1.28)
    remaining = set(range(count))
    durations = [0.0] * count
    remaining_total = total

    for _ in range(count + 2):
        if not remaining:
            break
        weight_sum = sum(weights[i] for i in remaining) or len(remaining)
        changed = False
        for i in list(remaining):
            value = remaining_total * weights[i] / weight_sum
            if value < min_d:
                durations[i] = min_d
                remaining_total -= min_d
                remaining.remove(i)
                changed = True
            elif value > max_d:
                durations[i] = max_d
                remaining_total -= max_d
                remaining.remove(i)
                changed = True
        if not changed:
            for i in remaining:
                durations[i] = remaining_total * weights[i] / weight_sum
            break

    correction = total - sum(durations)
    if durations:
        durations[-1] = max(0.08, durations[-1] + correction)
    return durations


def append_subtitle_parts(
    subtitles: list[dict[str, Any]],
    scene_id: str,
    text: str,
    start_time: float,
    end_time: float,
    cfg: dict[str, Any],
) -> int:
    text = clean_subtitle_text(text)
    if not text:
        return 0

    parts = semantic_segments(text, int(cfg["max_chars"]), int(cfg["min_chars"]))
    if not parts:
        return 0

    duration = max(0.08, end_time - start_time)
    gap = min(float(cfg["gap"]), duration / max(1, len(parts)) * 0.12)
    available = duration - gap * max(0, len(parts) - 1)
    if available <= 0:
        available = max(0.08, duration - gap * max(0, len(parts) - 1))

    weights = [segment_weight(part) for part in parts]
    durations = allocate_durations(
        weights,
        available,
        float(cfg["min_duration"]),
        float(cfg["max_duration"]),
    )

    local_cursor = start_time
    for part, part_duration in zip(parts, durations):
        start = max(0.0, local_cursor)
        end = local_cursor + max(0.08, part_duration)
        local_cursor += max(0.08, part_duration) + gap
        subtitles.append(
            {
                "scene_id": scene_id,
                "start": start,
                "end": max(start + 0.08, min(end, end_time)),
                "text": part,
            }
        )
    return len(parts)


def cue_voice_segments(cue_sheet: dict[str, Any]) -> list[tuple[str, float, float]]:
    result: list[tuple[str, float, float]] = []
    for segment in cue_sheet.get("segments") or []:
        if str(segment.get("kind") or "") != "voice":
            continue
        text = clean_subtitle_text(str(segment.get("text") or ""))
        if not text:
            continue
        start_ms = float(segment.get("start_ms") or 0)
        end_ms = float(segment.get("end_ms") or 0)
        if end_ms <= start_ms:
            end_ms = start_ms + float(segment.get("duration_ms") or 0)
        if end_ms <= start_ms:
            continue
        result.append((text, start_ms / 1000.0, end_ms / 1000.0))
    return result


def build_subtitles(
    scenes: list[dict[str, str]],
    wavs: dict[str, Path],
    cue_sheets: dict[str, dict[str, Any]] | None = None,
    scene_timeline: dict[str, dict[str, Any]] | None = None,
    global_offset: float = 0.0,
) -> tuple[list[dict[str, Any]], float]:
    cfg = SUBTITLE_CONFIG
    cursor = 0.0
    subtitles: list[dict[str, Any]] = []
    missing: list[str] = []
    voice_offset = float(cfg.get("voice_offset", 0.0) or 0.0)
    leading_silence_cache: dict[Path, float] = {}

    for scene in scenes:
        scene_id = scene["scene_id"]
        wav = wavs.get(scene_id)
        if not wav:
            missing.append(scene_id)
            continue

        source_duration = ffprobe_duration(wav)
        timeline_item = (scene_timeline or {}).get(scene_id)
        if timeline_item:
            scene_start = float(timeline_item.get("start") or 0.0)
            scene_end = float(timeline_item.get("end") or scene_start)
            duration = max(0.0, scene_end - scene_start)
            source_duration = max(source_duration, float(timeline_item.get("source_duration") or 0.0))
            cursor += source_duration
            if duration <= 0.02:
                log(f"warning: skip scene {scene_id} because subtitle_timeline duration is too short: {duration:.3f}s")
                continue
        else:
            duration = source_duration
            scene_start = cursor
            scene_end = cursor + duration
            cursor = scene_end

        cue_sheet = (cue_sheets or {}).get(scene_id)
        used_cue_sheet = False
        if cue_sheet:
            precise_timing = str(cue_sheet.get("schema") or "") == "swordcoming.tts_timing.v1"
            cue_offset = 0.0 if precise_timing else voice_offset
            # cue_sheet times already describe voice segments; do not add leading-silence detection here.
            for text, cue_start, cue_end in cue_voice_segments(cue_sheet):
                cue_start = min(max(0.0, cue_start), source_duration)
                cue_end = min(max(cue_end, cue_start + 0.08), source_duration)
                start_time = scene_start + cue_start + cue_offset + global_offset
                end_time = min(scene_start + cue_end + cue_offset + global_offset, scene_end + global_offset)
                if end_time <= start_time:
                    continue
                if append_subtitle_parts(subtitles, scene_id, text, start_time, end_time, cfg) > 0:
                    used_cue_sheet = True
            if used_cue_sheet:
                continue

        text = clean_subtitle_text(scene["text"])
        parts = semantic_segments(text, cfg["max_chars"], cfg["min_chars"])
        if not parts:
            continue

        gap = min(cfg["gap"], duration / max(1, len(parts)) * 0.15)
        available = duration - cfg["scene_lead"] - cfg["scene_tail"] - gap * max(0, len(parts) - 1)
        if available <= 0:
            available = max(0.08, duration - gap * max(0, len(parts) - 1))

        weights = [segment_weight(part) for part in parts]
        durations = allocate_durations(weights, available, cfg["min_duration"], cfg["max_duration"])

        leading_silence = detect_leading_silence(wav, leading_silence_cache)
        fallback_delay = max(leading_silence, voice_offset)
        local_cursor = scene_start + float(cfg["scene_lead"]) + fallback_delay
        for part, part_duration in zip(parts, durations):
            start = local_cursor + global_offset
            end = local_cursor + max(0.08, part_duration) + global_offset
            local_cursor += max(0.08, part_duration) + gap

            start = max(0.0, start)
            end = max(start + 0.08, min(end, scene_end + global_offset))
            subtitles.append(
                {
                    "scene_id": scene_id,
                    "start": start,
                    "end": end,
                    "text": part,
                }
            )

    if missing:
        preview = ",".join(missing[:20])
        raise RuntimeError(f"missing wav files for {len(missing)} scenes: {preview}")
    return subtitles, cursor


def format_srt_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    millis = int(round(seconds * 1000))
    hh = millis // 3_600_000
    millis %= 3_600_000
    mm = millis // 60_000
    millis %= 60_000
    ss = millis // 1000
    ms = millis % 1000
    return f"{hh:02d}:{mm:02d}:{ss:02d},{ms:03d}"


def format_ass_time(seconds: float) -> str:
    seconds = max(0.0, seconds)
    centis = int(round(seconds * 100))
    hh = centis // 360_000
    centis %= 360_000
    mm = centis // 6_000
    centis %= 6_000
    ss = centis // 100
    cs = centis % 100
    return f"{hh}:{mm:02d}:{ss:02d}.{cs:02d}"


def apply_line_break(text: str, line_max_chars: int) -> str:
    if text_len(text) <= line_max_chars:
        return text
    if text_len(text) <= line_max_chars * 2:
        target = text_len(text) / 2
        count = 0
        best_index = len(text) // 2
        best_score = math.inf
        for idx, ch in enumerate(text):
            if not ch.isspace():
                count += 1
            score = abs(count - target)
            if ch in SOFT_END:
                score -= 2.0
            if score < best_score:
                best_score = score
                best_index = idx + 1
        return text[:best_index].strip() + "\n" + text[best_index:].strip()
    return text


def escape_ass_text(text: str) -> str:
    text = text.replace("\\", "\\\\")
    text = text.replace("{", "\\{").replace("}", "\\}")
    text = apply_line_break(text, int(SUBTITLE_CONFIG["line_max_chars"]))
    return text.replace("\n", r"\N")


def ass_filter_path(path: Path) -> str:
    value = str(path.resolve()).replace("\\", "/")
    return value.replace(":", r"\:")


def write_subtitle_files(video_path: Path, subtitles: list[dict[str, Any]], suffix: str) -> tuple[Path, Path]:
    stem = video_path.with_suffix("")
    srt_path = Path(f"{stem}_{suffix}.srt")
    ass_path = Path(f"{stem}_{suffix}.ass")

    with srt_path.open("w", encoding="utf-8") as handle:
        for index, item in enumerate(subtitles, 1):
            text = apply_line_break(str(item["text"]), int(SUBTITLE_CONFIG["line_max_chars"]))
            handle.write(f"{index}\n")
            handle.write(f"{format_srt_time(item['start'])} --> {format_srt_time(item['end'])}\n")
            handle.write(f"{text}\n\n")

    font = FONT_CONFIG
    with ass_path.open("w", encoding="utf-8-sig") as handle:
        handle.write("[Script Info]\n")
        handle.write("ScriptType: v4.00+\n")
        handle.write("PlayResX: 1280\n")
        handle.write("PlayResY: 720\n")
        handle.write("WrapStyle: 2\n")
        handle.write("ScaledBorderAndShadow: yes\n\n")
        handle.write("[V4+ Styles]\n")
        handle.write(
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, "
            "Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
        )
        handle.write(
            "Style: Default,"
            f"{font['font']},{font['size']},{font['primary']},{font['primary']},{font['outline']},{font['back']},"
            f"0,0,0,0,100,100,0,0,1,{font['outline_width']},{font['shadow']},2,50,50,{font['margin_v']},1\n\n"
        )
        handle.write("[Events]\n")
        handle.write("Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n")
        for item in subtitles:
            text = escape_ass_text(str(item["text"]))
            if not text:
                continue
            handle.write(
                f"Dialogue: 0,{format_ass_time(item['start'])},{format_ass_time(item['end'])},"
                f"Default,,0,0,0,,{{\\fad({font['fade_ms']},{font['fade_ms']})\\an2"
                f"\\pos({font['pos_x']},{font['pos_y']})}}{text}\n"
            )
    return srt_path, ass_path


def scale_subtitles(subtitles: list[dict[str, Any]], factor: float) -> None:
    for item in subtitles:
        item["start"] = max(0.0, float(item["start"]) * factor)
        item["end"] = max(item["start"] + 0.08, float(item["end"]) * factor)


def combine_subtitle_text(left: str, right: str) -> str:
    left = str(left or "").strip()
    right = str(right or "").strip()
    if not left:
        return right
    if not right:
        return left
    return left + right


def is_punctuation_only(text: str) -> bool:
    text = str(text or "").strip()
    if not text:
        return True
    return re.search(r"[A-Za-z0-9_\u4e00-\u9fff]", text) is None


def merge_short_subtitles(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int, int]:
    cfg = SUBTITLE_CONFIG
    short_display = max(0.2, float(cfg.get("comfort_short_display", 0.8)))
    short_gap = max(0.0, float(cfg.get("comfort_short_merge_gap", 0.65)))
    short_max_chars = max(int(cfg.get("comfort_merge_max_chars", 26)), int(cfg.get("comfort_short_merge_max_chars", 32)))
    short_max_duration = max(float(cfg.get("comfort_merge_max_duration", 4.2)), 5.2)

    result: list[dict[str, Any]] = []
    merged_count = 0
    dropped_count = 0

    index = 0
    while index < len(items):
        item = dict(items[index])
        duration = float(item.get("end", 0.0)) - float(item.get("start", 0.0))
        too_short = duration < short_display
        punct_only = is_punctuation_only(str(item.get("text") or ""))

        if (too_short or punct_only) and result:
            prev = result[-1]
            same_scene = str(prev.get("scene_id") or "") == str(item.get("scene_id") or "")
            gap = float(item.get("start", 0.0)) - float(prev.get("end", 0.0))
            combined_text = combine_subtitle_text(str(prev.get("text") or ""), str(item.get("text") or ""))
            combined_duration = float(item.get("end", 0.0)) - float(prev.get("start", 0.0))
            if same_scene and gap <= short_gap and text_len(combined_text) <= short_max_chars and combined_duration <= short_max_duration:
                prev["end"] = max(float(prev.get("end", 0.0)), float(item.get("end", 0.0)))
                prev["text"] = combined_text
                merged_count += 1
                index += 1
                continue

        if too_short or punct_only:
            next_item = dict(items[index + 1]) if index + 1 < len(items) else None
            if next_item:
                same_scene = str(next_item.get("scene_id") or "") == str(item.get("scene_id") or "")
                gap = float(next_item.get("start", 0.0)) - float(item.get("end", 0.0))
                combined_text = combine_subtitle_text(str(item.get("text") or ""), str(next_item.get("text") or ""))
                combined_duration = float(next_item.get("end", 0.0)) - float(item.get("start", 0.0))
                if same_scene and gap <= short_gap and text_len(combined_text) <= short_max_chars and combined_duration <= short_max_duration:
                    next_item["start"] = min(float(item.get("start", 0.0)), float(next_item.get("start", 0.0)))
                    next_item["text"] = combined_text
                    result.append(next_item)
                    merged_count += 1
                    index += 2
                    continue

        if punct_only:
            dropped_count += 1
            index += 1
            continue

        result.append(item)
        index += 1

    return result, merged_count, dropped_count


def smooth_subtitle_display(
    subtitles: list[dict[str, Any]],
    video_duration: float | None = None,
) -> dict[str, int]:
    cfg = SUBTITLE_CONFIG
    merge_gap = max(0.0, float(cfg.get("comfort_merge_gap", 0.0)))
    merge_max_chars = max(int(cfg.get("max_chars", 20)), int(cfg.get("comfort_merge_max_chars", 26)))
    merge_max_duration = max(0.5, float(cfg.get("comfort_merge_max_duration", 4.2)))
    min_display = max(0.2, float(cfg.get("comfort_min_display", 0.95)))
    linger = max(0.0, float(cfg.get("comfort_linger", 0.32)))
    gap = max(0.0, float(cfg.get("comfort_gap", 0.018)))
    video_limit = None
    if video_duration is not None and math.isfinite(video_duration) and video_duration > 0:
        video_limit = max(0.0, float(video_duration) - float(cfg.get("timeline_end_margin", 0.02)))

    ordered = sorted(
        (dict(item) for item in subtitles if str(item.get("text") or "").strip()),
        key=lambda item: (float(item.get("start") or 0.0), float(item.get("end") or 0.0)),
    )
    merged: list[dict[str, Any]] = []
    stats = {
        "input": len(subtitles),
        "output": 0,
        "merged": 0,
        "short_merged": 0,
        "short_dropped": 0,
        "extended": 0,
    }

    for item in ordered:
        item["start"] = float(item.get("start") or 0.0)
        item["end"] = float(item.get("end") or item["start"])
        item["text"] = str(item.get("text") or "").strip()
        if not merged:
            merged.append(item)
            continue

        prev = merged[-1]
        same_scene = str(prev.get("scene_id") or "") == str(item.get("scene_id") or "")
        item_gap = float(item["start"]) - float(prev["end"])
        combined_text = combine_subtitle_text(str(prev.get("text") or ""), str(item.get("text") or ""))
        combined_duration = float(item["end"]) - float(prev["start"])
        can_merge = (
            same_scene
            and item_gap <= merge_gap
            and text_len(combined_text) <= merge_max_chars
            and combined_duration <= merge_max_duration
        )
        if can_merge:
            prev["end"] = max(float(prev["end"]), float(item["end"]))
            prev["text"] = combined_text
            stats["merged"] += 1
        else:
            merged.append(item)

    merged, short_merged, short_dropped = merge_short_subtitles(merged)
    stats["short_merged"] = short_merged
    stats["short_dropped"] = short_dropped

    for index, item in enumerate(merged):
        next_start = float(merged[index + 1]["start"]) if index + 1 < len(merged) else video_limit
        limit = None
        if next_start is not None:
            limit = max(float(item["start"]), float(next_start) - gap)
        if video_limit is not None:
            limit = min(limit, video_limit) if limit is not None else video_limit
        if limit is None:
            continue

        desired_end = max(float(item["end"]) + linger, float(item["start"]) + min_display)
        new_end = min(desired_end, limit)
        if new_end > float(item["end"]) + 0.001:
            item["end"] = new_end
            stats["extended"] += 1

    subtitles[:] = merged
    stats["output"] = len(merged)
    return stats


def validate_and_repair_subtitles(
    subtitles: list[dict[str, Any]],
    video_duration: float | None = None,
) -> dict[str, int]:
    cfg = SUBTITLE_CONFIG
    min_display = max(0.04, float(cfg.get("timeline_min_display", 0.12)))
    min_gap = max(0.0, float(cfg.get("timeline_min_gap", 0.005)))
    end_margin = max(0.0, float(cfg.get("timeline_end_margin", 0.02)))
    video_limit: float | None = None
    if video_duration is not None and math.isfinite(video_duration) and video_duration > 0:
        video_limit = max(0.0, float(video_duration) - end_margin)

    stats = {
        "input": len(subtitles),
        "output": 0,
        "dropped": 0,
        "reordered": 0,
        "invalid_timing_fixed": 0,
        "start_clamped": 0,
        "end_clamped": 0,
        "overlap_fixed": 0,
        "min_duration_fixed": 0,
    }
    prepared: list[tuple[float, float, int, dict[str, Any], str]] = []
    for index, item in enumerate(subtitles):
        text = str(item.get("text") or "").strip()
        if not text:
            stats["dropped"] += 1
            continue
        try:
            start = float(item.get("start", 0.0))
            end = float(item.get("end", start))
        except (TypeError, ValueError):
            start = 0.0
            end = 0.0
            stats["invalid_timing_fixed"] += 1
        if not math.isfinite(start):
            start = 0.0
            stats["invalid_timing_fixed"] += 1
        if not math.isfinite(end):
            end = start
            stats["invalid_timing_fixed"] += 1
        prepared.append((start, end, index, item, text))

    original_order = [original_index for _, _, original_index, _, _ in prepared]
    ordered = sorted(prepared, key=lambda value: (value[0], value[1], value[2]))
    if [original_index for _, _, original_index, _, _ in ordered] != original_order:
        stats["reordered"] = 1

    repaired: list[dict[str, Any]] = []
    prev_end = 0.0
    for raw_start, raw_end, _, item, text in ordered:
        start = raw_start
        end = raw_end
        if start < 0:
            start = 0.0
            stats["start_clamped"] += 1
        if end <= start:
            end = start + min_display
            stats["invalid_timing_fixed"] += 1
        elif end - start < min_display:
            end = start + min_display
            stats["min_duration_fixed"] += 1

        if video_limit is not None:
            if start >= video_limit:
                start = max(0.0, video_limit - min_display)
                end = video_limit
                stats["start_clamped"] += 1
                stats["end_clamped"] += 1
            elif end > video_limit:
                end = video_limit
                stats["end_clamped"] += 1
                if end - start < min_display:
                    start = max(0.0, end - min_display)
                    stats["start_clamped"] += 1

        if repaired:
            earliest_start = prev_end + min_gap
            if start < earliest_start:
                start = earliest_start
                stats["overlap_fixed"] += 1

        if end - start < min_display:
            end = start + min_display
            stats["min_duration_fixed"] += 1

        if video_limit is not None and end > video_limit:
            hard_min_display = min(0.08, min_display)
            if video_limit - start >= hard_min_display:
                end = video_limit
                stats["end_clamped"] += 1
            else:
                stats["dropped"] += 1
                continue

        if end <= start:
            stats["dropped"] += 1
            continue

        new_item = dict(item)
        new_item["start"] = start
        new_item["end"] = end
        new_item["text"] = text
        repaired.append(new_item)
        prev_end = end

    subtitles[:] = repaired
    stats["output"] = len(repaired)
    return stats


def burn_video(video_path: Path, ass_path: Path, suffix: str, dry_run: bool = False) -> Path:
    output_path = Path(f"{video_path.with_suffix('')}_{suffix}.mp4")
    if dry_run:
        return output_path

    gpu_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"ass='{ass_filter_path(ass_path)}'",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-dn",
        "-sn",
        "-c:v",
        "h264_amf",
        "-rc",
        "cqp",
        "-qp_i",
        "22",
        "-qp_p",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "copy",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    try:
        run_command(gpu_cmd, "burn subtitles with h264_amf")
        return output_path
    except Exception as exc:
        log(f"GPU encode failed, fallback to libx264: {exc}")

    cpu_cmd = [
        "ffmpeg",
        "-y",
        "-i",
        str(video_path),
        "-vf",
        f"ass='{ass_filter_path(ass_path)}'",
        "-map",
        "0:v:0",
        "-map",
        "0:a:0",
        "-dn",
        "-sn",
        "-c:v",
        "libx264",
        "-preset",
        "veryfast",
        "-crf",
        "22",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "128k",
        "-ar",
        "48000",
        "-ac",
        "2",
        "-movflags",
        "+faststart",
        str(output_path),
    ]
    run_command(cpu_cmd, "burn subtitles with libx264")
    return output_path


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build test subtitles from director text and TTS wav timings.")
    parser.add_argument("--project-root", default=DEFAULT_PROJECT_ROOT)
    parser.add_argument("--run-id", default="", help="Accepted for watcher compatibility.")
    parser.add_argument("--chapter-hint", required=True)
    parser.add_argument("--plan", default="")
    parser.add_argument("--video", default="")
    parser.add_argument("--tts-dir", default="")
    parser.add_argument("--timeline", default="", help="Optional subtitle timeline json exported by shipin_5.py.")
    parser.add_argument("--suffix", default="AI_SUB")
    parser.add_argument("--global-offset", type=float, default=0.0)
    parser.add_argument("--voice-offset", type=float, default=SUBTITLE_CONFIG["voice_offset"], help="Delay subtitle voice timing without changing media timeline.")
    parser.add_argument("--max-chars", type=int, default=SUBTITLE_CONFIG["max_chars"])
    parser.add_argument("--line-max-chars", type=int, default=SUBTITLE_CONFIG["line_max_chars"])
    parser.add_argument("--one-line", action="store_true", default=None, help="Prefer single-line horizontal subtitles.")
    parser.add_argument("--font-size", type=int, default=FONT_CONFIG["size"], help="ASS font size.")
    parser.add_argument("--subtitle-y", type=int, default=FONT_CONFIG["pos_y"], help="Subtitle vertical position.")
    parser.add_argument("--outline", type=float, default=FONT_CONFIG["outline_width"], help="Outline thickness.")
    parser.add_argument("--shadow", type=float, default=FONT_CONFIG["shadow"], help="Shadow size.")
    parser.add_argument("--dry-run", action="store_true", help="Only write SRT/ASS, do not burn mp4.")
    parser.add_argument("--no-scale-to-video", action="store_true", help="Do not scale subtitle timeline to mp4 duration.")
    parser.add_argument("--no-cue-sheets", action="store_true", help="Ignore WF4 cue_sheet_debug timing files.")
    parser.add_argument("--no-subtitle-timeline", action="store_true", help="Ignore shipin_5.py subtitle timeline json.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    root = Path(args.project_root)
    SUBTITLE_CONFIG["max_chars"] = int(args.max_chars)
    SUBTITLE_CONFIG["line_max_chars"] = int(args.line_max_chars)
    SUBTITLE_CONFIG["voice_offset"] = float(args.voice_offset)
    one_line = bool(SUBTITLE_CONFIG.get("one_line", False)) if args.one_line is None else bool(args.one_line)
    if one_line:
        SUBTITLE_CONFIG["line_max_chars"] = max(int(args.line_max_chars), int(args.max_chars), 999)
    FONT_CONFIG["size"] = int(args.font_size)
    FONT_CONFIG["pos_y"] = int(args.subtitle_y)
    FONT_CONFIG["outline_width"] = float(args.outline)
    FONT_CONFIG["shadow"] = float(args.shadow)

    plan_path = Path(args.plan) if args.plan else locate_latest_file(
        root / "visual_planning", "*_director*.json", args.chapter_hint
    )
    video_path = Path(args.video) if args.video else locate_latest_base_video(
        root / "video_output", args.chapter_hint
    )
    if "_AI_SUB" in video_path.name or f"_{args.suffix}" in video_path.name:
        raise RuntimeError(f"please use the base no-subtitle mp4, got: {video_path}")

    tts_dir = Path(args.tts_dir) if args.tts_dir else locate_tts_dir(root, args.chapter_hint, plan_path)
    chapter, scenes = load_scenes(plan_path)
    wavs = tts_file_map(tts_dir)
    cue_sheets = {} if args.no_cue_sheets else load_cue_sheets(tts_dir)
    timeline_path = None if args.no_subtitle_timeline else locate_subtitle_timeline(
        root,
        video_path,
        args.chapter_hint,
        explicit_path=args.timeline,
    )
    scene_timeline = load_subtitle_timeline(timeline_path)

    log(f"chapter={chapter}")
    log(f"plan={plan_path}")
    log(f"video={video_path}")
    log(f"tts_dir={tts_dir}")
    precise_cue_sheets = sum(
        1
        for cue_sheet in cue_sheets.values()
        if str(cue_sheet.get("schema") or "") == "swordcoming.tts_timing.v1"
    )
    log(
        f"scenes={len(scenes)} wavs={len(wavs)} cue_sheets={len(cue_sheets)} "
        f"precise_timing={precise_cue_sheets}"
    )
    log(f"voice_offset={float(SUBTITLE_CONFIG.get('voice_offset') or 0.0):.3f}s")
    log("fallback_offset=max(leading_silence, voice_offset)")
    if timeline_path and scene_timeline:
        clipped = sum(float(item.get("clipped_seconds") or 0.0) for item in scene_timeline.values())
        log(f"subtitle_timeline={timeline_path} scenes={len(scene_timeline)} clipped={clipped:.3f}s")
    elif not args.no_subtitle_timeline:
        log("subtitle_timeline=not found, using wav cumulative timing")

    subtitles, audio_duration = build_subtitles(
        scenes,
        wavs,
        cue_sheets=cue_sheets,
        scene_timeline=scene_timeline,
        global_offset=args.global_offset,
    )
    video_duration = ffprobe_duration(video_path)
    log(f"subtitles={len(subtitles)} audio_duration={audio_duration:.3f}s video_duration={video_duration:.3f}s")

    if subtitles and scene_timeline:
        log("skip global scaling because subtitle_timeline is active")
    elif subtitles and not args.no_scale_to_video and audio_duration > 0:
        factor = video_duration / audio_duration
        delta = video_duration - audio_duration
        if abs(1.0 - factor) > 0.0005:
            if abs(delta) <= 1.0 and 0.95 <= factor <= 1.05:
                scale_subtitles(subtitles, factor)
                log(f"scaled subtitle timeline to video duration: factor={factor:.6f}")
            else:
                log(f"warning: skip global scaling because duration delta is too large: delta={delta:.3f}s factor={factor:.6f}")

    if subtitles:
        comfort_stats = smooth_subtitle_display(subtitles, video_duration)
        if comfort_stats["merged"] or comfort_stats["extended"]:
            log(
                "subtitle comfort: "
                f"merged={comfort_stats['merged']} "
                f"short_merged={comfort_stats['short_merged']} "
                f"short_dropped={comfort_stats['short_dropped']} "
                f"extended={comfort_stats['extended']} "
                f"output={comfort_stats['output']}/{comfort_stats['input']}"
            )

    if subtitles:
        timeline_stats = validate_and_repair_subtitles(subtitles, video_duration)
        changed = {
            key: value
            for key, value in timeline_stats.items()
            if key not in ("input", "output") and value
        }
        if changed:
            details = " ".join(f"{key}={value}" for key, value in changed.items())
            log(f"timeline repaired: {details} output={timeline_stats['output']}/{timeline_stats['input']}")

    if subtitles:
        drift = video_duration - subtitles[-1]["end"]
        log(f"last_sub_end={subtitles[-1]['end']:.3f}s drift_to_video_end={drift:.3f}s")
        if abs(drift) > 1.0:
            log("warning: audio-derived subtitle timeline differs from video duration by more than 1s")

    srt_path, ass_path = write_subtitle_files(video_path, subtitles, args.suffix)
    log(f"srt={srt_path}")
    log(f"ass={ass_path}")
    output_path = burn_video(video_path, ass_path, args.suffix, dry_run=args.dry_run)
    log(f"output={output_path}")
    print(f"SUBTITLE_OUTPUT={output_path}")
    return 0


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except Exception as exc:
        log(f"ERROR: {exc}")
        raise
