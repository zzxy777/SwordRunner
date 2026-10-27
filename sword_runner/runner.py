from __future__ import annotations

import argparse
import sys
from contextlib import contextmanager
from pathlib import Path

RUNNER_ROOT = Path(__file__).resolve().parent
if str(RUNNER_ROOT) not in sys.path:
    sys.path.insert(0, str(RUNNER_ROOT))

from pipeline.chapter_loader import Chapter, load_chapter
from pipeline.config import load_config
from pipeline.director import build_director_plan, group_scenes, write_director_outputs
from pipeline.io_utils import ensure_dir, log, make_run_id, write_json_atomic
from pipeline.llm_client import LLMClient
from pipeline.post_pipeline import run_video_and_subtitle, write_windows_job
from pipeline.segmenter import build_segments
from pipeline.state_store import StateStore
from pipeline.tts_pipeline import build_voice_scripts, render_tts


STEP_ORDER = ["chapter", "segment", "director", "voice", "tts", "post"]


@contextmanager
def step_scope(store: StateStore, state: dict, step: str):
    store.mark_step(state, step, "running")
    try:
        yield
    except Exception as exc:
        store.mark_step(state, step, "failed", error=str(exc), error_type=type(exc).__name__)
        store.mark_blocked(state, step, exc)
        log(f"BLOCKED step={step} error={exc}")
        raise


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="SwordComing script runner for WF1-WF4 replacement")
    sub = parser.add_subparsers(dest="command", required=True)

    run = sub.add_parser("run", help="run one chapter")
    add_common_args(run)
    add_execution_args(run, default_from_step="chapter", default_to_step="director")

    resume = sub.add_parser("resume", help="resume latest or specified run")
    add_common_args(resume)
    add_execution_args(resume, default_from_step="", default_to_step="post")

    inspect = sub.add_parser("inspect", help="resolve chapter and print status without running")
    add_common_args(inspect)
    return parser.parse_args()


def add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--project-root", default=os.environ.get("SWORD_RUNNER_PROJECT_ROOT", str(Path.home() / "SwordAudiobookProject")))
    parser.add_argument("--config", default="", help="optional JSON config override")
    parser.add_argument("--chapter-hint", default="", help="chapter safe name or title")
    parser.add_argument("--chapter-index", type=int, default=0, help="1-based chapter index from chapter_index.json")
    parser.add_argument("--run-id", default="", help="stable run id for resume/debug")


def add_execution_args(parser: argparse.ArgumentParser, *, default_from_step: str, default_to_step: str) -> None:
    if default_from_step:
        parser.add_argument("--from-step", choices=STEP_ORDER, default=default_from_step)
    else:
        parser.add_argument("--from-step", choices=STEP_ORDER, default="", help="override auto resume start step")
    parser.add_argument("--to-step", choices=STEP_ORDER, default=default_to_step)
    parser.add_argument("--dry-run", action="store_true", help="do not call LLM/ComfyUI/post scripts")
    parser.add_argument("--no-llm", action="store_true", help="use rule-based segmentation and narrator-only scripts")
    parser.add_argument("--force", action="store_true", help="overwrite existing generated artifacts")
    parser.add_argument("--skip-tts", action="store_true", help="skip ComfyUI TTS even when to-step includes tts")
    parser.add_argument("--run-post", action="store_true", help="run shipin_5.py and zimu_7_text_timed.py directly")
    parser.add_argument("--write-windows-job", action="store_true", help="write a windows_jobs JSON for the existing watcher")
    parser.add_argument("--max-scenes", type=int, default=0, help="limit scenes for smoke tests")


def selected_steps(from_step: str, to_step: str) -> list[str]:
    start = STEP_ORDER.index(from_step)
    end = STEP_ORDER.index(to_step)
    if end < start:
        raise RuntimeError(f"to-step must be after from-step: {from_step}->{to_step}")
    return STEP_ORDER[start : end + 1]


def load_chapter_from_args(cfg: dict, args: argparse.Namespace) -> Chapter:
    return load_chapter(
        Path(cfg["project_root"]),
        Path(cfg["paths"]["chapter_index_path"]),
        Path(cfg["paths"]["book_path"]),
        chapter_hint=args.chapter_hint,
        chapter_index=args.chapter_index or None,
    )


def prepare_context(args: argparse.Namespace) -> tuple[dict, Chapter, str, Path, StateStore, dict]:
    project_root = Path(args.project_root)
    config_path = Path(args.config) if args.config else None
    cfg = load_config(RUNNER_ROOT, project_root, config_path)
    chapter = load_chapter_from_args(cfg, args)
    run_id = args.run_id or make_run_id("script")
    run_dir = ensure_dir(Path(cfg["paths"]["runs_dir"]) / chapter.safe_name / run_id)
    store = StateStore(run_dir)
    state = store.load()
    state["run_id"] = run_id
    state["chapter"] = {
        "index": chapter.index,
        "name": chapter.name,
        "title": chapter.title,
        "full_title": chapter.full_title,
        "safe_name": chapter.safe_name,
        "chars": len(chapter.text),
    }
    store.save(state)
    return cfg, chapter, run_id, run_dir, store, state


def command_inspect(args: argparse.Namespace) -> int:
    cfg, chapter, run_id, run_dir, _store, _state = prepare_context(args)
    log(f"project_root={cfg['project_root']}")
    log(f"chapter={chapter.safe_name} index={chapter.index} chars={len(chapter.text)}")
    log(f"run_id={run_id}")
    log(f"run_dir={run_dir}")
    return 0


def command_run(args: argparse.Namespace) -> int:
    cfg, chapter, run_id, run_dir, store, state = prepare_context(args)
    steps = selected_steps(args.from_step, args.to_step)
    dry_run = bool(args.dry_run)
    use_llm = bool((cfg.get("pipeline") or {}).get("use_llm_default", True)) and not args.no_llm and not dry_run
    llm = LLMClient.from_config(cfg) if use_llm else None

    log(f"chapter={chapter.safe_name} index={chapter.index} chars={len(chapter.text)}")
    log(f"run_id={run_id}")
    log(f"state={store.path}")
    log(f"steps={','.join(steps)} dry_run={dry_run} use_llm={use_llm}")

    segments = None
    director_plan = None
    visual_groups = None
    voice_rows = None

    if "chapter" in steps:
        with step_scope(store, state, "chapter"):
            chapter_path = run_dir / "wf1_chapter.json"
            write_json_atomic(chapter_path, {
                "index": chapter.index,
                "name": chapter.name,
                "title": chapter.title,
                "full_title": chapter.full_title,
                "safe_name": chapter.safe_name,
                "text": chapter.text,
                "source_start": chapter.source_start,
                "source_end": chapter.source_end,
            })
            store.mark_step(state, "chapter", "done", path=str(chapter_path), chars=len(chapter.text))
            store.set_artifact(state, "chapter", chapter_path)
            log(f"chapter loaded -> {chapter_path}")

    if "segment" in steps:
        with step_scope(store, state, "segment"):
            prompt_path = RUNNER_ROOT / "prompts" / "wf2_segment.txt"
            segments = build_segments(chapter, cfg, llm=llm, use_llm=use_llm, prompt_path=prompt_path)
            if args.max_scenes:
                segments = segments[: args.max_scenes]
            segment_path = run_dir / "wf2_segments.json"
            write_json_atomic(segment_path, {
                "chapter": chapter.safe_name,
                "count": len(segments),
                "segments": segments,
            })
            store.mark_step(state, "segment", "done", path=str(segment_path), count=len(segments))
            store.set_artifact(state, "segments", segment_path)
            log(f"segments={len(segments)} -> {segment_path}")

    if "director" in steps:
        with step_scope(store, state, "director"):
            if segments is None:
                segment_path = Path(state["artifacts"]["segments"])
                segments = __import__("json").loads(segment_path.read_text(encoding="utf-8-sig"))["segments"]
            director_plan = build_director_plan(chapter, segments)
            visual_groups = group_scenes(chapter, director_plan, cfg)

            run_plan_path = run_dir / f"{chapter.safe_name}_director_v2.json"
            run_groups_path = run_dir / f"{chapter.safe_name}_visual_groups.json"
            write_json_atomic(run_plan_path, director_plan)
            write_json_atomic(run_groups_path, visual_groups)
            store.set_artifact(state, "director_plan_run", run_plan_path)
            store.set_artifact(state, "visual_groups_run", run_groups_path)

            if not dry_run:
                plan_path, groups_path = write_director_outputs(
                    chapter,
                    director_plan,
                    visual_groups,
                    Path(cfg["paths"]["visual_planning_dir"]),
                    Path(cfg["paths"]["visual_groups_dir"]),
                )
                store.set_artifact(state, "director_plan", plan_path)
                store.set_artifact(state, "visual_groups", groups_path)
                log(f"director outputs -> {plan_path} | {groups_path}")
            else:
                log(f"dry-run director preview -> {run_plan_path} | {run_groups_path}")
            store.mark_step(state, "director", "done", scenes=len(director_plan.get("scenes") or []))

    if "voice" in steps:
        with step_scope(store, state, "voice"):
            if director_plan is None:
                source = state["artifacts"].get("director_plan_run") or state["artifacts"].get("director_plan")
                director_plan = __import__("json").loads(Path(source).read_text(encoding="utf-8-sig"))
            prompt_path = RUNNER_ROOT / "prompts" / "wf4_voice_director.txt"
            voice_rows = build_voice_scripts(chapter, director_plan, cfg, llm=llm, use_llm=use_llm, prompt_path=prompt_path)
            if args.max_scenes:
                voice_rows = voice_rows[: args.max_scenes]
            voice_path = run_dir / "wf4_voice_scripts.json"
            write_json_atomic(voice_path, {
                "chapter": chapter.safe_name,
                "count": len(voice_rows),
                "items": voice_rows,
            })
            store.mark_step(state, "voice", "done", path=str(voice_path), count=len(voice_rows))
            store.set_artifact(state, "voice_scripts", voice_path)
            log(f"voice scripts={len(voice_rows)} -> {voice_path}")

    if "tts" in steps and not args.skip_tts:
        with step_scope(store, state, "tts"):
            if dry_run:
                log("dry-run: skip ComfyUI TTS")
                store.mark_step(state, "tts", "skipped", reason="dry_run")
            else:
                if voice_rows is None:
                    voice_path = Path(state["artifacts"]["voice_scripts"])
                    voice_rows = __import__("json").loads(voice_path.read_text(encoding="utf-8-sig"))["items"]
                result = render_tts(
                    chapter,
                    voice_rows,
                    cfg,
                    force=args.force,
                    max_scenes=args.max_scenes or None,
                )
                store.mark_step(state, "tts", "done", **{k: v for k, v in result.items() if k != "manifest"})
                store.set_artifact(state, "tts_dir", result["tts_dir"])
                log(f"tts generated={result['generated']} exists={result['exists']} dir={result['tts_dir']}")
    elif "tts" in steps and args.skip_tts:
        with step_scope(store, state, "tts"):
            store.mark_step(state, "tts", "skipped", reason="skip_tts")
            log("skip ComfyUI TTS by flag")

    if "post" in steps:
        with step_scope(store, state, "post"):
            if dry_run:
                log("dry-run: skip post stage")
                store.mark_step(state, "post", "skipped", reason="dry_run")
            elif args.run_post:
                run_video_and_subtitle(chapter, cfg, run_id)
                store.mark_step(state, "post", "done", mode="direct")
            elif args.write_windows_job:
                job_path = write_windows_job(chapter, cfg, run_id)
                store.mark_step(state, "post", "queued", mode="windows_job", path=str(job_path))
                store.set_artifact(state, "windows_job", job_path)
                log(f"windows job -> {job_path}")
            else:
                store.mark_step(state, "post", "skipped", reason="no --run-post or --write-windows-job")
                log("post skipped; pass --run-post or --write-windows-job when ready")

    log(f"state={store.path}")
    return 0


def latest_run_id(cfg: dict, chapter: Chapter) -> str:
    root = Path(cfg["paths"]["runs_dir"]) / chapter.safe_name
    runs = [path for path in root.glob("*") if path.is_dir()]
    if not runs:
        raise RuntimeError(f"no previous run found for chapter: {chapter.safe_name}")
    runs.sort(key=lambda path: path.stat().st_mtime, reverse=True)
    return runs[0].name


def first_incomplete_step(state: dict) -> str:
    steps = state.get("steps") or {}
    complete_statuses = {"done", "skipped", "queued"}
    for step in STEP_ORDER:
        if (steps.get(step) or {}).get("status") not in complete_statuses:
            return step
    return ""


def command_resume(args: argparse.Namespace) -> int:
    project_root = Path(args.project_root)
    cfg = load_config(RUNNER_ROOT, project_root, Path(args.config) if args.config else None)
    chapter = load_chapter_from_args(cfg, args)
    if not args.run_id:
        args.run_id = latest_run_id(cfg, chapter)

    _cfg, _chapter, _run_id, _run_dir, store, state = prepare_context(args)
    auto_step = first_incomplete_step(state)
    if not auto_step:
        log(f"run already complete: {args.run_id}")
        log(f"state={store.path}")
        return 0
    args.from_step = args.from_step or auto_step
    if STEP_ORDER.index(args.from_step) > STEP_ORDER.index(args.to_step):
        log(f"run already complete through {args.to_step}: {args.run_id}")
        log(f"next incomplete step={auto_step}")
        log(f"state={store.path}")
        return 0
    log(f"resume run_id={args.run_id} from_step={args.from_step} to_step={args.to_step}")
    return command_run(args)


def main() -> int:
    args = parse_args()
    try:
        if args.command == "inspect":
            return command_inspect(args)
        if args.command == "run":
            return command_run(args)
        if args.command == "resume":
            return command_resume(args)
        raise RuntimeError(f"unknown command: {args.command}")
    except Exception as exc:
        log(f"ERROR: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
