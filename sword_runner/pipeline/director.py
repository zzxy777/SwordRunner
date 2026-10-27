from __future__ import annotations

from pathlib import Path
from typing import Any

from .chapter_loader import Chapter
from .io_utils import now_iso, write_json_atomic


def guess_emotion(text: str) -> str:
    if any(token in text for token in ("怒", "喝", "杀", "血", "死", "剑光", "雷")):
        return "tense"
    if any(token in text for token in ("笑", "莞尔", "玩笑")):
        return "amused"
    if any(token in text for token in ("哭", "泪", "悲", "怅然")):
        return "sad"
    if "？" in text or "?" in text:
        return "curious"
    return "neutral"


def guess_scene_type(text: str) -> str:
    rules = [
        (("山", "峰", "云", "崖"), "山野云崖"),
        (("城", "街", "巷", "市"), "城镇街巷"),
        (("江", "河", "湖", "水"), "江湖水边"),
        (("剑", "拳", "杀", "战"), "斗法交锋"),
        (("客栈", "酒楼", "屋", "宅", "院"), "室内场景"),
    ]
    for keys, label in rules:
        if any(key in text for key in keys):
            return label
    return "通用场景"


def guess_visual_mode(text: str) -> str:
    if any(token in text for token in ("说道", "问道", "笑道", "道：“", "：“")):
        return "dialogue"
    if any(token in text for token in ("剑", "拳", "杀", "战", "撞", "掠")):
        return "action"
    if any(token in text for token in ("山", "云", "江", "河", "湖", "城")):
        return "wide"
    return "face"


def guess_character(text: str) -> str:
    known = ["陈平安", "宁姚", "陆沉", "阿良", "齐静春", "宋集薪", "崔瀺", "刘羡阳"]
    for name in known:
        if name in text:
            return name
    return "旁白"


def build_director_plan(chapter: Chapter, segments: list[dict[str, Any]]) -> dict[str, Any]:
    scenes = []
    for row in segments:
        text = str(row.get("text") or "").strip()
        scenes.append(
            {
                "scene_id": str(row.get("scene_id") or len(scenes) + 1).zfill(3),
                "character": guess_character(text),
                "emotion": guess_emotion(text),
                "scene_type": guess_scene_type(text),
                "visual_mode": guess_visual_mode(text),
                "narrative": text,
                "audio": {
                    "file": None,
                    "duration": None,
                },
            }
        )
    return {
        "chapter": chapter.safe_name,
        "version": "v2",
        "generated_at": now_iso(),
        "source": "sword_runner",
        "scenes": scenes,
    }


def group_scenes(chapter: Chapter, director_plan: dict[str, Any], cfg: dict[str, Any]) -> dict[str, Any]:
    group_size = int((cfg.get("pipeline") or {}).get("visual_group_size") or 5)
    scenes = director_plan.get("scenes") or []
    groups = []
    scene_to_group: dict[str, str] = {}

    for start in range(0, len(scenes), group_size):
        rows = scenes[start : start + group_size]
        group_id = str(len(groups) + 1).zfill(3)
        image_file = f"group_{group_id}.png"
        summary = " ".join(str(row.get("narrative") or "") for row in rows).strip()
        main = rows[0] if rows else {}
        scene_ids = [str(row.get("scene_id")).zfill(3) for row in rows]
        for scene_id in scene_ids:
            scene_to_group[scene_id] = image_file
        prompt = build_visual_prompt(chapter.safe_name, group_id, scene_ids, main, summary)
        groups.append(
            {
                "group_id": group_id,
                "scene_ids": scene_ids,
                "duration_seconds": None,
                "visual_mode": main.get("visual_mode") or "face",
                "scene_type": main.get("scene_type") or "通用场景",
                "mood": main.get("emotion") or "neutral",
                "character": main.get("character") or "旁白",
                "supporting_characters": sorted({str(row.get("character") or "") for row in rows if row.get("character")}),
                "summary": summary[:1800],
                "image_file": image_file,
                "image_path_n8n": "",
                "image_path_windows": "",
                "prompt": prompt,
            }
        )

    return {
        "chapter": chapter.safe_name,
        "version": "visual_groups_sword_runner_v1",
        "generated_at": now_iso(),
        "source_director_file": f"{chapter.safe_name}_director_v2.json",
        "total_scenes": len(scenes),
        "matched_audio_count": None,
        "total_duration_seconds": None,
        "target_group_count": len(groups),
        "actual_group_count": len(groups),
        "target_hold_seconds": None,
        "hold_range": None,
        "duration_policy": "script_runner_group_size",
        "image_dir": "",
        "image_dir_n8n": "",
        "image_dir_windows": "",
        "groups": groups,
        "scene_to_group": scene_to_group,
    }


def build_visual_prompt(chapter: str, group_id: str, scene_ids: list[str], main: dict[str, Any], summary: str) -> str:
    return (
        "Create one coherent key visual for a Chinese xianxia novel audio-video chapter.\n"
        "Style: Chinese anime donghua wuxia illustration, ancient China setting, ink wash background, cinematic 16:9.\n"
        f"Chapter: {chapter}\n"
        f"Visual group: group_{group_id}, scenes {', '.join(scene_ids)}\n"
        f"Scene type: {main.get('scene_type') or '通用场景'}\n"
        f"Visual mode: {main.get('visual_mode') or 'face'}\n"
        f"Main character: {main.get('character') or '旁白'}\n"
        f"Mood: {main.get('emotion') or 'neutral'}\n"
        f"Group summary: {summary[:1400]}"
    )


def write_director_outputs(
    chapter: Chapter,
    director_plan: dict[str, Any],
    visual_groups: dict[str, Any],
    visual_planning_dir: Path,
    visual_groups_dir: Path,
) -> tuple[Path, Path]:
    plan_path = visual_planning_dir / f"{chapter.safe_name}_director_v2.json"
    groups_path = visual_groups_dir / f"{chapter.safe_name}_visual_groups.json"
    write_json_atomic(plan_path, director_plan)
    write_json_atomic(groups_path, visual_groups)
    return plan_path, groups_path

