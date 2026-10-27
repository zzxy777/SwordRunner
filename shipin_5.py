# -*- coding: utf-8 -*-
import argparse
import os
import glob
import subprocess
import shutil
import json
import random
import math
import time
import re
import sys
import threading
import logging
import hashlib
from concurrent.futures import ThreadPoolExecutor, as_completed
from collections import deque

# ================= 配置区域 (V7.4 收口稳态版) =================
PROJECT_ROOT = os.environ.get("SWORD_RUNNER_PROJECT_ROOT", os.path.join(os.path.expanduser("~"), "SwordAudiobookProject"))
MATERIAL_ROOT = os.path.join(PROJECT_ROOT, "scene_library")
SCRIPT_VERSION = "v7.4"
SCRIPT_VERSION_LABEL = "V7.4"

JSON_DIR = os.path.join(PROJECT_ROOT, "visual_planning")
AUDIO_ROOT = os.path.join(PROJECT_ROOT, "tts")
OUTPUT_DIR = os.path.join(PROJECT_ROOT, "video_output")
VISUAL_GROUPS_DIR = os.path.join(PROJECT_ROOT, "visual_groups")
GENERATED_IMAGES_DIR = os.path.join(PROJECT_ROOT, "images")
DONE_IMAGES_DIR = os.path.join(GENERATED_IMAGES_DIR, "done")
LOCAL_TEMP_DIR = ""
TEMP_DIR = LOCAL_TEMP_DIR
BGM_FILE = os.path.join(PROJECT_ROOT, "bgm.mp3")
AUDIO_SEARCH_ROOT = AUDIO_ROOT
BGM_VOLUME = 0.015
VOICE_LOUDNORM_I = -18.0
VOICE_LOUDNORM_TP = -1.5
VOICE_LOUDNORM_LRA = 11.0

# 兜底背景
DEFAULT_IMG = os.path.join(MATERIAL_ROOT, "通用空镜", "default.png") 

# 核心参数
TARGET_W, TARGET_H = 1280, 720
# 720p 上传足够时，内部工作画布也保持 720p，避免 4K 画布逐帧渲染再降采样。
WORK_W, WORK_H = 1280, 720
FPS = 24
FFMPEG_BIN = "ffmpeg" 
FFPROBE_BIN = "ffprobe"
CHAPTER_TAG_FONT_CANDIDATES = [
    r"C:\Windows\Fonts\msyh.ttc",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\simhei.ttf",
]
CHAPTER_TAG_FONT_SIZE = 18
CHAPTER_TAG_MARGIN_X = 30
CHAPTER_TAG_MARGIN_Y = 20
CHAPTER_TAG_FONT_ALPHA = 0.66
CHAPTER_TAG_BOX_ALPHA = 0.10
CHAPTER_TAG_BOX_BORDER = 6
CHAPTER_TAG_EXPORT_CONFIG = {
    "encoder": "h264_amf",
    "params": ["-usage", "transcoding", "-quality", "speed", "-rc", "cqp", "-qp_i", "24", "-qp_p", "24", "-profile:v", "high"],
}

# 渲染模式
RENDER_MODE = "preview" 

# 索引黑名单
EXCLUDED_DIRS = ["Character", "人物", "UI", "Icons", "Thumbnail", "Cover"]

# --- ⚡ AMD 显卡加速配置 (保持 V7.1 原参数) ---
AMD_GPU_CONFIG = {
    "preview": {
        "encoder": "h264_amf",
        "params": ["-usage", "transcoding", "-quality", "speed", "-rc", "cqp", "-qp_i", "28", "-qp_p", "28"]
    },
    "final": {
        "encoder": "h264_amf", 
        "params": ["-usage", "transcoding", "-quality", "quality", "-rc", "cqp", "-qp_i", "22", "-qp_p", "22", "-profile:v", "high"]
    }
}

# --- 节奏配置 (V7.4 超长慢节奏版) ---
# 单张静态图目标停留区间：50s ~ 70s
MIN_DISPLAY_TIME = 50.0
MAX_DISPLAY_TIME = 70.0
TARGET_SLOT_TIME = 60.0
MIN_SLOT_DURATION = 50.0
MIN_VISUAL_HOLD_SECONDS = 50.0
SCENE_FADE_DURATION = 0.8
INTERNAL_FADE_DURATION = 1.0
INTERNAL_FADE_PROBABILITY = 0.12
GROUP_TRANSITION_DURATION = 1.15

CONFIG = {
    "NAS_RETRY_DELAY": 0.3,
    "MIN_AUDIO_DURATION": 4.0,
    "MAX_SCENE_AUDIO_DURATION": 300.0,
    "MAX_FAILED_PLACEHOLDER_SECONDS": 8.0,
    "FFMPEG_TIMEOUT": 300,
}
PLACEHOLDER_SILENCE_SECONDS = 1.5

# --- V6 画质增强引擎 ---
QUALITY_CONFIG = {
    "preview": {
        "upscale_flags": "bicubic",
        "downscale_flags": "bicubic",
        "sharpen": "unsharp=3:3:0.15",
        "grain": "",
        "denoise": ""
    },
    "final": {
        "upscale_flags": "lanczos+accurate_rnd", 
        "downscale_flags": "lanczos+accurate_rnd",
        "sharpen": "unsharp=5:5:0.8:3:3:0.0",
        "grain": "noise=alls=2:allf=t+u", 
        "denoise": "" 
    }
}

# --- 情绪调色 ---
EMOTION_COLOR_GRADING = {
    "sad": { "filter": "eq=saturation=0.7:contrast=0.9,colorbalance=bs=0.05" },
    "tense": { "filter": "eq=contrast=1.2:saturation=1.1,colorbalance=rm=0.05" },
    "happy": { "filter": "eq=saturation=1.2:brightness=0.05" },
    "calm": { "filter": "eq=saturation=0.9:contrast=0.95" },
    "lonely": { "filter": "eq=saturation=0.5:contrast=1.1" },
    "neutral": { "filter": "eq=saturation=1.05" }
}

# --- 🎥 V7.4 核心：丝滑长镜头运镜库 ---
MOTION_EFFECTS = {
    "static_hold": {
        "desc": "真静止定镜",
        "type": "crop_motion",
        "canvas_zoom": 1.000,
        "x0": 0.50,
        "x1": 0.50,
        "y0": 0.50,
        "y1": 0.50
    },
    "still_hold": {
        "desc": "稳态轻漂移",
        "type": "crop_motion",
        "canvas_zoom": 1.015,
        "x0": 0.50,
        "x1": 0.50,
        "y0": 0.515,
        "y1": 0.485
    },
    "slow_push_in": {
        "desc": "平顺前移感",
        "type": "crop_motion",
        "canvas_zoom": 1.025,
        "x0": 0.495,
        "x1": 0.505,
        "y0": 0.515,
        "y1": 0.485
    },
    "slow_push_out": {
        "desc": "平顺后移感",
        "type": "crop_motion",
        "canvas_zoom": 1.025,
        "x0": 0.505,
        "x1": 0.495,
        "y0": 0.485,
        "y1": 0.515
    },
    "slow_pan_left": {
        "desc": "平顺左移",
        "type": "crop_motion",
        "canvas_zoom": 1.035,
        "x0": 0.56,
        "x1": 0.44,
        "y0": 0.50,
        "y1": 0.50
    },
    "slow_pan_right": {
        "desc": "平顺右移",
        "type": "crop_motion",
        "canvas_zoom": 1.035,
        "x0": 0.44,
        "x1": 0.56,
        "y0": 0.50,
        "y1": 0.50
    },
    "diagonal_drift": {
        "desc": "对角线慢漂",
        "type": "crop_motion",
        "canvas_zoom": 1.025,
        "x0": 0.535,
        "x1": 0.465,
        "y0": 0.525,
        "y1": 0.475
    },
    "ultra_subtle_breath": {
        "desc": "稳态兼容别名",
        "type": "crop_motion",
        "canvas_zoom": 1.000,
        "x0": 0.50,
        "x1": 0.50,
        "y0": 0.50,
        "y1": 0.50
    },
    "subtle": {
        "desc": "稳态轻漂移兼容别名",
        "type": "crop_motion",
        "canvas_zoom": 1.015,
        "x0": 0.50,
        "x1": 0.50,
        "y0": 0.515,
        "y1": 0.485
    }
}

COMPANION_EFFECT_POOL = [
    "static_hold", "static_hold", "static_hold",
    "still_hold", "still_hold", "still_hold", "still_hold", "still_hold",
    "slow_push_in", "slow_push_out"
]

TRANSITION_EFFECT_POOL = [
    {"name": "cut", "duration": 0.00},
    {"name": "cut", "duration": 0.00},
    {"name": "cut", "duration": 0.00},
    {"name": "dissolve", "duration": 0.78},
    {"name": "dissolve", "duration": 0.72},
    {"name": "dissolve", "duration": 0.68},
    {"name": "fade", "duration": 0.65},
]

EMOTION_MAP = {
    "sad": ["雨", "夜", "暗", "旧", "破"],
    "tense": ["夜", "火", "风", "红", "暗", "杀"],
    "calm": ["山", "水", "林", "云", "日"],
    "happy": ["市", "闹", "灯", "日", "亮"],
    "lonely": ["雪", "夜", "远", "空", "独"],
    "neutral": ["空镜", "景", "远"]
}
DEFAULT_EMOTION_KEYWORDS = ["空镜", "通用"]

MAX_WORKERS = min(3, os.cpu_count() or 3)
GPU_MAX_WORKERS = 1

class CommandExecutionError(RuntimeError):
    def __init__(self, message, *, cmd=None, returncode=None, stderr_text=""):
        parts = [message]
        if returncode is not None:
            parts.append(f"returncode={returncode}")
        if cmd:
            parts.append(f"cmd={subprocess.list2cmdline([str(part) for part in cmd])}")
        if stderr_text:
            parts.append(f"stderr={stderr_text}")
        super().__init__(" | ".join(parts))
        self.cmd = cmd
        self.returncode = returncode
        self.stderr_text = stderr_text

class ProjectPaths:
    def __init__(
        self,
        project_root,
        material_root,
        json_dir,
        audio_root,
        output_dir,
        temp_root,
        default_img,
        bgm_file,
        audio_search_root,
    ):
        self.project_root = project_root
        self.material_root = material_root
        self.json_dir = json_dir
        self.audio_root = audio_root
        self.output_dir = output_dir
        self.temp_root = temp_root
        self.default_img = default_img
        self.bgm_file = bgm_file
        self.audio_search_root = audio_search_root

    @classmethod
    def from_project_root(cls, project_root):
        project_root = project_root or PROJECT_ROOT
        material_root = os.path.join(project_root, "scene_library")
        audio_root = os.path.join(project_root, "tts")
        return cls(
            project_root=project_root,
            material_root=material_root,
            json_dir=os.path.join(project_root, "visual_planning"),
            audio_root=audio_root,
            output_dir=os.path.join(project_root, "video_output"),
            temp_root=resolve_temp_dir(project_root),
            default_img=resolve_default_image(material_root),
            bgm_file=os.path.join(project_root, "bgm.mp3"),
            audio_search_root=audio_root,
        )

    def with_bgm_file(self, bgm_file):
        return ProjectPaths(
            self.project_root,
            self.material_root,
            self.json_dir,
            self.audio_root,
            self.output_dir,
            self.temp_root,
            self.default_img,
            bgm_file,
            self.audio_search_root,
        )

    def with_audio_search_root(self, audio_search_root):
        return ProjectPaths(
            self.project_root,
            self.material_root,
            self.json_dir,
            self.audio_root,
            self.output_dir,
            self.temp_root,
            self.default_img,
            self.bgm_file,
            audio_search_root or self.audio_root,
        )

class SafeConsoleStream:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        try:
            return self.stream.write(data)
        except UnicodeEncodeError:
            encoding = getattr(self.stream, "encoding", None) or "utf-8"
            safe = data.encode(encoding, errors="replace").decode(encoding, errors="replace")
            return self.stream.write(safe)

    def flush(self):
        return self.stream.flush()

class RunContext:
    def __init__(self, run_id, seed, temp_dir, logger, log_path, paths):
        self.run_id = run_id
        self.seed = seed
        self.temp_dir = temp_dir
        self.logger = logger
        self.log_path = log_path
        self.paths = paths
        self.audio_index = None
        self.recent_images = deque(maxlen=15)
        self.recent_images_lock = threading.Lock()
        self.gpu_semaphore = threading.BoundedSemaphore(GPU_MAX_WORKERS)
        self.stats = {"scenes_processed": 0, "effects_used": {}, "fades_applied": 0, "errors": []}
        self.stats_lock = threading.Lock()
        self.material_index = {}
        self.all_images_pool = []
        self.total_input_audio_duration = 0.0
        self.total_groups_expected = 0
        self.total_groups_success = 0
        self.total_groups_failed = 0
        self.total_missing_audio = 0
        self.total_tasks_expected = 0
        self.final_reference_duration = 0.0
        self.last_group_motion = ""
        self.last_group_transition = ""
        self.recent_group_motions = deque(maxlen=4)
        self.recent_group_transitions = deque(maxlen=4)
        self.failure_policy = {
            "missing_audio_strict": "fail",
            "missing_audio_non_strict": "skip",
            "scene_failure": "fail_group",
            "group_failure": "fail_batch",
            "final_concat_failure": "fail_batch",
            "bgm_missing": "skip_bgm",
            "bgm_mix_failure": "skip_bgm",
            "final_consistency_failure": "fail_batch",
            "invalid_output": "fail_batch",
            "allow_partial_output": False,
        }

    def make_rng(self, *parts):
        payload = "|".join(str(p) for p in (self.seed, *parts))
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return random.Random(int(digest[:16], 16))

    def set_total_tasks_expected(self, count):
        with self.stats_lock:
            self.total_tasks_expected = int(count or 0)

    def set_total_groups_expected(self, count):
        with self.stats_lock:
            self.total_groups_expected = int(count or 0)

    def set_total_missing_audio(self, count):
        with self.stats_lock:
            self.total_missing_audio = int(count or 0)

    def add_input_audio_duration(self, seconds):
        with self.stats_lock:
            self.total_input_audio_duration += float(seconds or 0.0)

    def bump_groups_success(self):
        with self.stats_lock:
            self.total_groups_success += 1

    def bump_groups_failed(self):
        with self.stats_lock:
            self.total_groups_failed += 1

    def bump_scenes_processed(self, count=1):
        with self.stats_lock:
            self.stats["scenes_processed"] += int(count or 0)

    def record_effect(self, effect_name):
        with self.stats_lock:
            self.stats["effects_used"][effect_name] = self.stats["effects_used"].get(effect_name, 0) + 1

    def record_fade(self):
        with self.stats_lock:
            self.stats["fades_applied"] += 1

    def record_error(self, stage, message, scene_id=None, group_idx=None, cmd=None, returncode=None):
        error_item = {
            "stage": stage,
            "message": str(message),
            "scene_id": scene_id,
            "group_idx": group_idx,
            "cmd": subprocess.list2cmdline([str(part) for part in cmd]) if cmd else "",
            "returncode": returncode,
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
        }
        with self.stats_lock:
            self.stats["errors"].append(error_item)

    def snapshot_recent_images(self):
        with self.recent_images_lock:
            return set(self.recent_images)

    def push_recent_image(self, path):
        with self.recent_images_lock:
            self.recent_images.append(path)

    def set_last_group_motion(self, motion_name):
        with self.stats_lock:
            self.last_group_motion = str(motion_name or "")
            if motion_name:
                self.recent_group_motions.append(str(motion_name))

    def set_last_group_transition(self, transition_name):
        with self.stats_lock:
            self.last_group_transition = str(transition_name or "")
            if transition_name:
                self.recent_group_transitions.append(str(transition_name))

    def snapshot_recent_group_motions(self):
        with self.stats_lock:
            return list(self.recent_group_motions)

    def snapshot_recent_group_transitions(self):
        with self.stats_lock:
            return list(self.recent_group_transitions)

def log_line(message, level="info", ctx=None, logger=None):
    logger = logger or (ctx.logger if ctx else None)
    if logger:
        log_fn = getattr(logger, level, logger.info)
        log_fn(message)
    else:
        print(message, flush=True)

def log_effect(effect_name, ctx=None):
    if not ctx:
        return
    ctx.record_effect(effect_name)

# ================= 基础工具 =================

def normalize_token(value):
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())

def build_scope_tokens(*values):
    tokens = []
    for value in values:
        token = normalize_token(value)
        if token and token not in tokens and token != "auto":
            tokens.append(token)
    return tokens

def generate_run_id(prefix="shipin5"):
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    return f"{prefix}_{timestamp}_{os.getpid()}"

def compute_seed(run_id):
    digest = hashlib.sha256(str(run_id).encode("utf-8")).hexdigest()
    return int(digest[:16], 16)

def setup_logger(project_root, run_id):
    log_dir = os.path.join(project_root, "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{run_id}.log")

    logger = logging.getLogger(f"shipin_5.{run_id}")
    logger.setLevel(logging.INFO)
    logger.propagate = False
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()

    formatter = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s")
    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    stream_handler = logging.StreamHandler(SafeConsoleStream(sys.stdout))
    stream_handler.setFormatter(formatter)
    logger.addHandler(file_handler)
    logger.addHandler(stream_handler)
    return logger, log_path

def build_run_context(paths, run_id=None):
    run_id = str(run_id or "").strip() or generate_run_id()
    seed = compute_seed(run_id)
    logger, log_path = setup_logger(paths.project_root, run_id)
    run_temp_dir = os.path.join(paths.temp_root, run_id)
    return RunContext(run_id=run_id, seed=seed, temp_dir=run_temp_dir, logger=logger, log_path=log_path, paths=paths)

def shorten_text(text, limit=400):
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[-limit:]

def run_command(cmd, timeout=None, desc=None, retries=0, capture_stdout=False, capture_stderr=True, ctx=None, logger=None):
    last_error = None
    for attempt in range(retries + 1):
        try:
            completed = subprocess.run(
                cmd,
                stdout=subprocess.PIPE if capture_stdout else subprocess.DEVNULL,
                stderr=subprocess.PIPE if capture_stderr else subprocess.DEVNULL,
                timeout=timeout,
                check=False,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            if completed.returncode == 0:
                return completed

            stderr_text = shorten_text(completed.stderr)
            last_error = CommandExecutionError(
                desc or "命令执行失败",
                cmd=cmd,
                returncode=completed.returncode,
                stderr_text=stderr_text,
            )
        except subprocess.TimeoutExpired as exc:
            stderr_text = shorten_text(getattr(exc, "stderr", "") or "")
            last_error = CommandExecutionError(
                (desc or "命令执行失败") + " (timeout)",
                cmd=cmd,
                stderr_text=stderr_text,
            )
        except OSError as exc:
            last_error = CommandExecutionError(
                (desc or "命令执行失败") + f" (os error: {exc})",
                cmd=cmd,
            )

        if attempt < retries:
            log_line(f"⚠️ {desc or '命令执行'}失败，准备重试 {attempt + 1}/{retries}", ctx=ctx, logger=logger)
            time.sleep(CONFIG["NAS_RETRY_DELAY"] * (attempt + 1))

    raise last_error

def run_ffmpeg(args, **kwargs):
    return run_command([FFMPEG_BIN, *args], **kwargs)

def run_ffprobe(args, **kwargs):
    return run_command([FFPROBE_BIN, *args], **kwargs)

def check_tools():
    for tool_name, runner in [("ffmpeg", run_ffmpeg), ("ffprobe", run_ffprobe)]:
        runner(["-version"], timeout=10, desc=f"检查 {tool_name}")

def validate_path_exists(path, label):
    if not os.path.exists(path):
        raise RuntimeError(f"{label} 不存在: {path}")

def validate_directory(path, label, create=False, require_write=False):
    if create:
        os.makedirs(path, exist_ok=True)
    if not os.path.isdir(path):
        raise RuntimeError(f"{label} 不是有效目录: {path}")
    if require_write:
        probe_name = f".write_test_{os.getpid()}_{int(time.time() * 1000)}.tmp"
        probe_path = os.path.join(path, probe_name)
        try:
            with open(probe_path, "w", encoding="utf-8") as f:
                f.write("ok")
        finally:
            if os.path.exists(probe_path):
                os.remove(probe_path)

def validate_render_config(logger=None):
    if RENDER_MODE not in QUALITY_CONFIG:
        raise RuntimeError(f"不支持的 RENDER_MODE: {RENDER_MODE}")
    if RENDER_MODE not in AMD_GPU_CONFIG:
        raise RuntimeError(f"编码器配置缺失: {RENDER_MODE}")

    encoder = AMD_GPU_CONFIG[RENDER_MODE].get("encoder")
    encoders_output = run_ffmpeg(
        ["-hide_banner", "-encoders"],
        timeout=20,
        desc="检查 ffmpeg 编码器列表",
        capture_stdout=True,
        logger=logger,
    )
    available_text = "\n".join([encoders_output.stdout or "", encoders_output.stderr or ""])
    if encoder and encoder not in available_text:
        raise RuntimeError(f"当前 ffmpeg 不支持编码器 {encoder}")

def validate_runtime(paths, logger=None):
    check_tools()
    validate_path_exists(paths.project_root, "项目根目录")
    validate_directory(paths.json_dir, "剧本目录")
    validate_directory(paths.audio_root, "音频根目录")
    validate_directory(paths.output_dir, "输出目录", create=True, require_write=True)
    validate_directory(paths.temp_root, "临时目录", create=True, require_write=True)
    validate_path_exists(paths.default_img, "默认兜底图片")
    validate_render_config(logger=logger)

def ffmpeg_concat_escape(path):
    normalized = os.path.abspath(path).replace("\\", "/")
    if "\n" in normalized or "\r" in normalized:
        raise RuntimeError(f"concat 路径包含非法换行符: {path}")
    return normalized.replace("'", "'\\''")

def assert_inputs_exist(paths, label):
    missing = [p for p in paths if not os.path.exists(p)]
    if missing:
        preview = ", ".join(missing[:5])
        raise RuntimeError(f"{label} 缺少输入文件: {preview}")

def write_concat_list(paths, txt_path, label):
    assert_inputs_exist(paths, label)
    with open(txt_path, "w", encoding="utf-8", newline="\n") as f:
        for path in paths:
            f.write(f"file '{ffmpeg_concat_escape(path)}'\n")

def resolve_chapter_tag_font():
    for font_path in CHAPTER_TAG_FONT_CANDIDATES:
        if os.path.exists(font_path):
            return font_path
    raise RuntimeError("未找到可用的中文字体文件，无法绘制章节小标签")

def escape_drawtext_value(value):
    escaped = str(value).replace("\\", "/")
    for old, new in [
        (":", r"\:"),
        ("'", r"\'"),
        ("[", r"\["),
        ("]", r"\]"),
        ("%", r"\%"),
        (",", r"\,"),
        (";", r"\;"),
    ]:
        escaped = escaped.replace(old, new)
    return escaped

def normalize_chapter_tag_text(chapter_name):
    raw = str(chapter_name or "").strip().replace("_", " ")
    raw = re.sub(r"\s+", " ", raw)
    return raw or "当前章节"

def build_chapter_tag_filter(chapter_name):
    font_path = escape_drawtext_value(resolve_chapter_tag_font())
    label = escape_drawtext_value(normalize_chapter_tag_text(chapter_name))
    return (
        "drawtext="
        f"fontfile='{font_path}':"
        f"text='{label}':"
        f"fontsize={CHAPTER_TAG_FONT_SIZE}:"
        f"fontcolor=white@{CHAPTER_TAG_FONT_ALPHA:.2f}:"
        "box=1:"
        f"boxcolor=black@{CHAPTER_TAG_BOX_ALPHA:.2f}:"
        f"boxborderw={CHAPTER_TAG_BOX_BORDER}:"
        f"x=w-text_w-{CHAPTER_TAG_MARGIN_X}:"
        f"y={CHAPTER_TAG_MARGIN_Y}"
    )

def probe_media_info(media_path, ctx=None, logger=None):
    validate_path_exists(media_path, "媒体文件")
    completed = run_ffprobe(
        [
            "-v", "error",
            "-show_entries",
            "format=duration,format_name,bit_rate:stream=index,codec_type,codec_name,codec_tag_string,profile,width,height,sample_rate,channels,duration,bit_rate",
            "-of", "json",
            media_path,
        ],
        timeout=10,
        desc=f"探测媒体 {os.path.basename(media_path)}",
        capture_stdout=True,
        ctx=ctx,
        logger=logger,
    )
    try:
        return json.loads(completed.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"ffprobe 输出无法解析: {media_path}") from exc

def validate_audio_decodable(media_path, ctx=None, logger=None, desc=None):
    run_ffmpeg(
        [
            "-v", "error",
            "-xerror",
            "-i", media_path,
            "-map", "0:a:0",
            "-f", "null",
            "-",
        ],
        desc=desc or f"音频解码校验 {os.path.basename(media_path)}",
        timeout=CONFIG["FFMPEG_TIMEOUT"],
        ctx=ctx,
        logger=logger,
    )

def is_valid_media_file(media_path, expect_video=False, expect_audio=False, min_duration=0.1, ctx=None, logger=None):
    if not os.path.exists(media_path) or os.path.getsize(media_path) <= 1024:
        return False
    try:
        info = probe_media_info(media_path, ctx=ctx, logger=logger)
    except Exception as exc:
        log_line(f"⚠️ 媒体校验失败: {media_path} | {exc}", ctx=ctx, logger=logger)
        return False

    streams = [s.get("codec_type") for s in info.get("streams", [])]
    duration_raw = ((info.get("format") or {}).get("duration"))
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError):
        duration = 0.0

    if expect_video and "video" not in streams:
        return False
    if expect_audio and "audio" not in streams:
        return False
    if min_duration and duration < min_duration:
        return False
    return True

def resolve_temp_dir(project_root):
    if LOCAL_TEMP_DIR:
        try:
            os.makedirs(LOCAL_TEMP_DIR, exist_ok=True)
            return LOCAL_TEMP_DIR
        except OSError:
            pass
    return os.path.join(project_root, "temp_clips")

def resolve_default_image(material_root, rng=None):
    done_dir = DONE_IMAGES_DIR
    if os.path.isdir(done_dir):
        candidates = []
        for root, _, files in os.walk(done_dir):
            for file_name in files:
                if file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    path = os.path.join(root, file_name)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        candidates.append(path)
        if candidates:
            chooser = rng or random
            return chooser.choice(sorted(candidates))

    common_dir = os.path.join(material_root, "通用空镜")
    preferred = [
        os.path.join(common_dir, "default.png"),
        os.path.join(common_dir, "default.jpg"),
        os.path.join(common_dir, "default.jpeg"),
        os.path.join(common_dir, "default.webp"),
    ]
    for path in preferred:
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            return path

    candidates = []
    if os.path.isdir(material_root):
        for root, _, files in os.walk(material_root):
            for file_name in files:
                if file_name.lower().endswith((".png", ".jpg", ".jpeg", ".webp")):
                    path = os.path.join(root, file_name)
                    if os.path.isfile(path) and os.path.getsize(path) > 0:
                        candidates.append(path)
    if candidates:
        chooser = rng or random
        return chooser.choice(sorted(candidates))

    return os.path.join(common_dir, "default.png")

def force_cleanup(folder_path, required=False):
    if not os.path.exists(folder_path):
        return True
    last_error = None
    for attempt in range(3):
        try:
            shutil.rmtree(folder_path, ignore_errors=False)
            return True
        except (PermissionError, OSError) as exc:
            last_error = exc
            time.sleep(CONFIG["NAS_RETRY_DELAY"] * (attempt + 1))
    log_line(f"⚠️ 清理失败: {folder_path} | {last_error}")
    if required:
        raise RuntimeError(f"无法清理临时目录: {folder_path}") from last_error
    return False

def resolve_bgm_file(project_root, rng=None):
    candidates = []
    for pattern in ["bgm*.mp3", "BGM*.mp3"]:
        candidates.extend(glob.glob(os.path.join(project_root, pattern)))

    # 去重并过滤临时/空文件
    seen = set()
    valid = []
    for path in sorted(candidates):
        norm = os.path.normcase(os.path.abspath(path))
        if norm in seen:
            continue
        seen.add(norm)
        if os.path.isfile(path) and os.path.getsize(path) > 0:
            valid.append(path)

    if not valid:
        return os.path.join(project_root, "bgm.mp3")
    chooser = rng or random
    return chooser.choice(valid)

def _read_lock_pid(lock_path):
    try:
        with open(lock_path, "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("pid="):
                    return int(line.split("=", 1)[1].strip())
    except Exception:
        return None
    return None

def _is_pid_running(pid):
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        return False

def acquire_process_lock(project_root, lock_name):
    lock_dir = os.path.join(project_root, "windows_jobs", "locks")
    os.makedirs(lock_dir, exist_ok=True)
    lock_path = os.path.join(lock_dir, f"{lock_name}.lock")
    if os.path.exists(lock_path):
        pid = _read_lock_pid(lock_path)
        age = time.time() - os.path.getmtime(lock_path)
        if pid and not _is_pid_running(pid):
            try:
                os.remove(lock_path)
            except OSError:
                pass
        elif age > 30 * 60:
            try:
                os.remove(lock_path)
            except OSError:
                pass
        else:
            raise RuntimeError(f"lock_exists:{lock_path},pid={pid},age={int(age)}s")
    with open(lock_path, "w", encoding="utf-8") as f:
        f.write(f"pid={os.getpid()}\n")
        f.write(f"created_at={time.time()}\n")
    return lock_path

def release_process_lock(lock_path):
    try:
        if lock_path and os.path.exists(lock_path):
            os.remove(lock_path)
    except OSError:
        pass

def get_latest_plan(json_dir, chapter_hint=None, run_id=None, strict=False):
    files = glob.glob(os.path.join(json_dir, "*.json"))
    valid_files = [f for f in files if "package" not in f and "tsconfig" not in f]
    if not valid_files:
        return None

    hint = normalize_token(chapter_hint)
    if hint and hint != "auto":
        matched = [f for f in valid_files if hint in normalize_token(os.path.basename(f))]
        if matched:
            return max(matched, key=os.path.getmtime)
        if strict:
            return None

    if run_id:
        rid = str(run_id).strip().lower()
        matched = [f for f in valid_files if rid and rid in os.path.basename(f).lower()]
        if matched:
            return max(matched, key=os.path.getmtime)

    if strict:
        return None
    return max(valid_files, key=os.path.getmtime)

def get_audio_duration(audio_path, ctx=None, logger=None):
    info = probe_media_info(audio_path, ctx=ctx, logger=logger)
    streams = [s.get("codec_type") for s in info.get("streams", [])]
    if "audio" not in streams:
        raise RuntimeError(f"音频文件缺少音频流: {audio_path}")
    duration_raw = ((info.get("format") or {}).get("duration"))
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"音频时长解析失败: {audio_path}") from exc
    if duration <= 0:
        raise RuntimeError(f"音频时长非法: {audio_path}")
    return duration

def validate_scene_audio_duration(scene_id, audio_path, duration, ctx=None):
    max_duration = float(CONFIG.get("MAX_SCENE_AUDIO_DURATION") or 0)
    if max_duration > 0 and float(duration or 0) > max_duration:
        message = (
            f"scene {scene_id} 音频片段过长: {duration:.2f}s > {max_duration:.2f}s | "
            f"{audio_path}"
        )
        if ctx:
            ctx.record_error("audio_duration_too_long", message, scene_id=scene_id)
        raise RuntimeError(message)


def normalize_audio_for_video(audio_path, temp_root, scene_id=None, ctx=None):
    """
    将进入视频拼接链的音频统一规范成：
    - WAV
    - pcm_s16le
    - 48000 Hz
    - 2 声道
    - 统一人声响度

    这样可以避免混合格式（例如伪 .wav 的 flac、24k mono、48k stereo 混杂）
    在后半章视频封装时产生尖叫杂音、断声或丢声，同时避免 TTS 源文件偏小
    导致最终成片人声响度不稳定。
    """
    if not audio_path or not os.path.exists(audio_path):
        raise RuntimeError(f"normalize_audio_for_video 缺少音频文件: {audio_path}")

    info = probe_media_info(audio_path, ctx=ctx)
    fmt = str(((info.get("format") or {}).get("format_name")) or "").lower()
    audio_stream = None
    for stream in info.get("streams", []) or []:
        if stream.get("codec_type") == "audio":
            audio_stream = stream
            break

    if not audio_stream:
        raise RuntimeError(f"音频文件缺少音频流: {audio_path}")

    norm_dir = os.path.join(temp_root, "normalized_audio")
    os.makedirs(norm_dir, exist_ok=True)

    stem = os.path.splitext(os.path.basename(audio_path))[0]
    safe_scene = str(scene_id or stem).strip().replace(os.sep, "_")
    out_path = os.path.join(norm_dir, f"{safe_scene}.loudnorm_video_ready.wav")

    if is_valid_media_file(out_path, expect_audio=True, ctx=ctx, min_duration=0.1):
        return out_path

    audio_filter = (
        f"loudnorm=I={VOICE_LOUDNORM_I}:TP={VOICE_LOUDNORM_TP}:LRA={VOICE_LOUDNORM_LRA},"
        "alimiter=limit=0.95"
    )
    run_ffmpeg(
        [
            "-y",
            "-i", audio_path,
            "-vn",
            "-af", audio_filter,
            "-acodec", "pcm_s16le",
            "-ar", "48000",
            "-ac", "2",
            out_path,
        ],
        desc=f"统一场景音频响度 {os.path.basename(audio_path)}",
        timeout=CONFIG["FFMPEG_TIMEOUT"],
        ctx=ctx,
    )

    if not is_valid_media_file(out_path, expect_audio=True, ctx=ctx, min_duration=0.1):
        raise RuntimeError(f"统一音频格式失败: {audio_path} -> {out_path}")
    validate_audio_decodable(out_path, ctx=ctx, desc=f"校验统一后音频 {os.path.basename(out_path)}")

    return out_path

def create_silence_placeholder(scene_id, search_root, chapter_name, ctx=None, duration=PLACEHOLDER_SILENCE_SECONDS):
    if not search_root:
        raise RuntimeError("create_silence_placeholder 需要有效的 search_root")

    safe_chapter_name = str(chapter_name or "chapter").strip()
    safe_scene_id = str(scene_id).strip().zfill(3)
    out_path = os.path.join(search_root, f"{safe_chapter_name}_{safe_scene_id}.wav")

    os.makedirs(search_root, exist_ok=True)
    if is_valid_media_file(out_path, expect_audio=True, ctx=ctx, min_duration=max(0.1, duration - 0.2)):
        return out_path

    run_ffmpeg(
        [
            "-y",
            "-f", "lavfi",
            "-i", f"anullsrc=r=48000:cl=stereo",
            "-t", f"{float(duration):.2f}",
            "-c:a", "pcm_s16le",
            out_path,
        ],
        desc=f"生成静音占位音频 {safe_scene_id}",
        timeout=CONFIG["FFMPEG_TIMEOUT"],
        ctx=ctx,
    )
    if not is_valid_media_file(out_path, expect_audio=True, ctx=ctx, min_duration=max(0.1, duration - 0.2)):
        raise RuntimeError(f"静音占位音频生成失败: {out_path}")
    return out_path

def resolve_tts_dir(audio_root, chapter_name, chapter_hint):
    candidates = []
    for v in [chapter_name, chapter_hint]:
        token = normalize_token(v)
        if token and token != "auto":
            candidates.append(token)

    if os.path.isdir(audio_root):
        subdirs = [d for d in os.listdir(audio_root) if os.path.isdir(os.path.join(audio_root, d))]
    else:
        subdirs = []

    for d in subdirs:
        dtok = normalize_token(d)
        if any(c in dtok or dtok in c for c in candidates):
            return os.path.join(audio_root, d)
    return None

def resolve_visual_groups_file(project_root, chapter_name, chapter_hint=None):
    visual_groups_dir = os.path.join(project_root, "visual_groups")
    if not os.path.isdir(visual_groups_dir):
        return None

    files = glob.glob(os.path.join(visual_groups_dir, "*_visual_groups.json"))
    if not files:
        return None

    tokens = []
    for value in [chapter_name, chapter_hint]:
        token = normalize_token(value)
        if token and token != "auto":
            tokens.append(token)

    if tokens:
        matched = []
        for file_path in files:
            file_token = normalize_token(os.path.basename(file_path))
            if any(token in file_token or file_token in token for token in tokens):
                matched.append(file_path)
        if matched:
            return max(matched, key=os.path.getmtime)

    return None

def load_visual_groups(project_root, chapter_name, chapter_hint=None, ctx=None):
    groups_file = resolve_visual_groups_file(project_root, chapter_name, chapter_hint)
    if not groups_file:
        log_line(f"🖼️ 未找到 visual_groups.json，继续使用素材库选图: chapter={chapter_name}", ctx=ctx)
        return None

    try:
        with open(groups_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as exc:
        log_line(f"⚠️ visual_groups.json 读取失败，继续使用素材库选图: {groups_file} | {exc}", level="warning", ctx=ctx)
        return None

    groups = data.get("groups") or []
    scene_to_group = data.get("scene_to_group") or {}
    if not groups or not scene_to_group:
        log_line(f"⚠️ visual_groups.json 缺少 groups/scene_to_group，继续使用素材库选图: {groups_file}", level="warning", ctx=ctx)
        return None

    image_dir = data.get("image_dir_windows") or os.path.join(project_root, "images", str(data.get("chapter") or chapter_name))
    by_file = {}
    for group in groups:
        image_file = str(group.get("image_file") or "").strip()
        if image_file:
            by_file[image_file] = group

    log_line(
        f"🖼️ 已加载视觉组: {os.path.basename(groups_file)} | groups={len(groups)} | image_dir={image_dir}",
        ctx=ctx,
    )
    return {
        "file": groups_file,
        "chapter": data.get("chapter") or chapter_name,
        "image_dir": image_dir,
        "groups": groups,
        "scene_to_group": {str(k).zfill(3): str(v) for k, v in scene_to_group.items()},
        "by_file": by_file,
    }

def build_groups_from_visual_groups(tasks, visual_groups, ctx=None):
    if not tasks or not visual_groups:
        return None

    tasks_by_id = {str(t.get("scene_id")).zfill(3): t for t in tasks}
    grouped = []
    missing_tasks = []
    missing_images = []
    covered = set()
    image_dir = visual_groups.get("image_dir") or ""

    for group in visual_groups.get("groups") or []:
        scene_ids = [str(sid).zfill(3) for sid in group.get("scene_ids") or []]
        group_tasks = []
        for scene_id in scene_ids:
            task = tasks_by_id.get(scene_id)
            if task:
                group_tasks.append(dict(task))
                covered.add(scene_id)
            else:
                missing_tasks.append(scene_id)
        if not group_tasks:
            continue

        image_file = str(group.get("image_file") or f"group_{str(group.get('group_id', len(grouped) + 1)).zfill(3)}.png")
        image_path = os.path.join(image_dir, image_file)
        if os.path.exists(image_path):
            for task in group_tasks:
                task["fixed_image"] = image_path
                task["visual_group_image"] = image_path
                task["visual_group_id"] = str(group.get("group_id") or image_file)
        else:
            missing_images.append(image_file)
        grouped.append(group_tasks)

    leftovers = [dict(t) for t in tasks if str(t.get("scene_id")).zfill(3) not in covered]
    if leftovers:
        log_line(
            f"⚠️ visual_groups 未覆盖 {len(leftovers)} 个任务，尾部回退为时长分组",
            level="warning",
            ctx=ctx,
        )
        grouped.extend(build_visual_hold_groups(leftovers, MIN_VISUAL_HOLD_SECONDS))

    if missing_tasks:
        log_line(
            f"⚠️ visual_groups 引用了缺失音频任务: {','.join(missing_tasks[:20])}",
            level="warning",
            ctx=ctx,
        )
    if missing_images:
        unique_missing = sorted(set(missing_images))
        log_line(
            f"⚠️ visual_groups 缺图 {len(unique_missing)} 张，将回退 images\\done 随机图池: {','.join(unique_missing[:20])}",
            level="warning",
            ctx=ctx,
        )

    return grouped if grouped else None

def build_audio_index(search_root):
    index = {
        "search_root": search_root,
        "relative_paths": {},
        "basenames": {},
        "filenames": [],
        "directory_tokens": set(),
    }
    if not os.path.isdir(search_root):
        return index

    for root, _, files in os.walk(search_root):
        index["directory_tokens"].add(normalize_token(os.path.relpath(root, search_root)))
        for file_name in files:
            if not file_name.lower().endswith((".wav", ".mp3")):
                continue
            full_path = os.path.join(root, file_name)
            rel_path = os.path.relpath(full_path, search_root).replace("\\", "/")
            rel_key = os.path.normcase(rel_path)
            base_key = os.path.normcase(file_name)
            index["relative_paths"][rel_key] = full_path
            index["basenames"].setdefault(base_key, []).append(full_path)
            index["filenames"].append((base_key, full_path))

    index["filenames"].sort(key=lambda item: item[0])
    for key in index["basenames"]:
        index["basenames"][key].sort()
    return index

def find_audio_smart(scene_id, idx, explicit_file=None, audio_index=None, search_root=None, scope_tokens=None, require_scope_match=False):
    if not search_root:
        raise RuntimeError("find_audio_smart 需要明确的 search_root")

    def _path_in_scope(path):
        if not scope_tokens:
            return True
        normalized = normalize_token(path)
        return any(token and token in normalized for token in scope_tokens)

    if explicit_file:
        explicit_norm = os.path.normcase(str(explicit_file).replace("\\", "/"))
        if audio_index:
            direct_hit = audio_index["relative_paths"].get(explicit_norm)
            if direct_hit and (not require_scope_match or _path_in_scope(direct_hit)):
                return direct_hit
            basename_hits = audio_index["basenames"].get(os.path.normcase(os.path.basename(explicit_file)))
            if basename_hits:
                for hit in basename_hits:
                    if not require_scope_match or _path_in_scope(hit):
                        return hit
        p1 = os.path.join(search_root, explicit_file)
        if os.path.exists(p1) and (not require_scope_match or _path_in_scope(p1)):
            return p1
        p2 = os.path.join(search_root, os.path.dirname(explicit_file), os.path.basename(explicit_file))
        if os.path.exists(p2) and (not require_scope_match or _path_in_scope(p2)):
            return p2

    search_id = str(scene_id)
    search_targets = [f"{search_id}.wav", f"{search_id}.mp3"]
    suffix_targets = [f"_{search_id}.wav", f"_{search_id}.mp3"]
    if audio_index:
        for target in search_targets:
            hits = audio_index["basenames"].get(os.path.normcase(target))
            if hits:
                for hit in hits:
                    if not require_scope_match or _path_in_scope(hit):
                        return hit
        suffix_targets = tuple(os.path.normcase(s) for s in suffix_targets)
        for fname, path in audio_index["filenames"]:
            if fname.endswith(suffix_targets) and (not require_scope_match or _path_in_scope(path)):
                return path
        return None

    for root, _, files in os.walk(search_root):
        for f in files:
            candidate = os.path.join(root, f)
            if f in search_targets and (not require_scope_match or _path_in_scope(candidate)):
                return os.path.join(root, f)
            for s in suffix_targets:
                if f.endswith(s) and (not require_scope_match or _path_in_scope(candidate)):
                    return os.path.join(root, f)
    return None

# ================= 索引与选图 (V7.4 优化：运行态避让) =================

def index_materials(material_root, ctx=None, logger=None):
    material_index = {}
    all_images_pool = []
    fallback_root = DONE_IMAGES_DIR
    scan_root = fallback_root if os.path.exists(fallback_root) else material_root
    log_label = "已完成图片库" if scan_root == fallback_root else "素材库"
    log_line(f"📂 正在建立{log_label}索引...", ctx=ctx, logger=logger)
    if not os.path.exists(scan_root):
        log_line(f"❌ 兜底图片库不存在: {scan_root}", level="error", ctx=ctx, logger=logger)
        return material_index, all_images_pool

    def _scan_dir(dir_path):
        dir_name = os.path.basename(dir_path)
        if any(ex.lower() in dir_name.lower() for ex in EXCLUDED_DIRS):
            return

        images = []
        try:
            with os.scandir(dir_path) as entries:
                for entry in entries:
                    if entry.is_dir(follow_symlinks=False):
                        _scan_dir(entry.path)
                    elif entry.is_file(follow_symlinks=False) and entry.name.lower().endswith(('.png', '.jpg', '.jpeg', '.webp')):
                        images.append(entry.path)
        except OSError:
            return

        if images:
            material_index[dir_name] = images

    _scan_dir(scan_root)
    for images in material_index.values():
        all_images_pool.extend(images)
    log_line(
        f"✅ 索引完成: root={scan_root} | {len(material_index)} 个目录, 共 {len(all_images_pool)} 张图片",
        ctx=ctx,
        logger=logger,
    )
    return material_index, all_images_pool

def get_coherent_image_sequence(scene_type, emotion, count, ctx=None, rng=None, default_img=None):
    selected_images = []
    material_index = ctx.material_index if ctx else {}
    all_images_pool = ctx.all_images_pool if ctx else []
    default_img = default_img or (ctx.paths.default_img if ctx else None)
    if not default_img:
        raise RuntimeError("get_coherent_image_sequence 需要 default_img 或 ctx.paths.default_img")
    mixed_pool = list(all_images_pool)
    if not mixed_pool:
        fallback = [imgs for dname, imgs in material_index.items() if "通用" in dname]
        mixed_pool = [i for s in fallback for i in s]

    chooser = rng or (ctx.make_rng("images", scene_type, emotion, count) if ctx else random)
    local_recent = deque(maxlen=15)

    chooser.shuffle(mixed_pool)
    last_img = None
    
    for _ in range(count):
        # V7.4 核心：使用运行态 recent_images 进行避让
        recent_snapshot = ctx.snapshot_recent_images() if ctx else set(local_recent)
        candidates = [img for img in mixed_pool if img != last_img and img not in recent_snapshot]
        if not candidates:
            candidates = [img for img in mixed_pool if img != last_img]
        
        choice = chooser.choice(candidates) if candidates else (chooser.choice(mixed_pool) if mixed_pool else default_img)
        selected_images.append(choice)
        if ctx:
            ctx.push_recent_image(choice)
        else:
            local_recent.append(choice)
        last_img = choice
            
    return selected_images

# ================= V7.1 核心：加权节奏算法 (严禁修改) =================

def calculate_time_slots(total_duration, rng=None):
    # 简化稳态版：直接按 20~50 秒随机切图，最后一段自动补齐剩余时长
    if total_duration < CONFIG["MIN_AUDIO_DURATION"]:
        return [total_duration]
    if total_duration <= MAX_DISPLAY_TIME:
        return [total_duration]

    slots = []
    remaining = float(total_duration)

    while remaining > MAX_DISPLAY_TIME:
        # 保证切完当前段后，剩余时长要么为 0，要么仍然不少于最小停留时长
        max_current = min(MAX_DISPLAY_TIME, remaining - MIN_SLOT_DURATION)
        if max_current < MIN_SLOT_DURATION:
            break
        slot = (rng or random).uniform(MIN_DISPLAY_TIME, max_current)
        slots.append(slot)
        remaining -= slot

    if remaining > 0:
        slots.append(remaining)

    return slots if slots else [total_duration]

def build_visual_hold_groups(tasks, min_hold_seconds):
    groups = []
    current = []
    current_total = 0.0
    threshold = max(0.0, float(min_hold_seconds or 0.0))

    for task in tasks:
        duration = float(task.get("duration", 0.0) or 0.0)
        current.append(task)
        current_total += duration
        if current_total >= threshold:
            groups.append(current)
            current = []
            current_total = 0.0

    if current:
        if groups and current_total < threshold:
            groups[-1].extend(current)
        else:
            groups.append(current)
    return groups

def assign_visual_hold_images(tasks, min_hold_seconds):
    if not tasks:
        return tasks

    grouped = build_visual_hold_groups(tasks, min_hold_seconds)
    log_line(f"🖼️ 视觉分组完成: {len(tasks)} 场景 -> {len(grouped)} 组 | 最短持图 {min_hold_seconds:.1f}s")
    assigned = []
    for group_idx, group in enumerate(grouped):
        first = group[0]
        shared_image = None
        images = get_coherent_image_sequence(first["scene_type"], first["emotion"], 1, default_img=DEFAULT_IMG)
        if images:
            shared_image = images[0]
        for task in group:
            t = dict(task)
            t["visual_hold_group"] = group_idx
            t["fixed_image"] = shared_image
            assigned.append(t)
    return assigned

def motion_family(motion_name):
    motion_name = str(motion_name or "")
    if motion_name == "static_hold":
        return "static"
    if motion_name.startswith("slow_pan"):
        return "pan"
    if motion_name.startswith("slow_push_in"):
        return "push_in"
    if motion_name.startswith("slow_push_out"):
        return "push_out"
    if motion_name == "diagonal_drift":
        return "drift"
    if motion_name in ("still_hold", "ultra_subtle_breath", "subtle"):
        return "hold"
    return motion_name or "unknown"

def build_effect_pool(intensity):
    base_pool = list(COMPANION_EFFECT_POOL)

    if intensity == "weak":
        pool = ["static_hold", "still_hold", "still_hold", "still_hold", "diagonal_drift"]
    elif intensity == "medium":
        pool = base_pool + ["still_hold", "still_hold", "diagonal_drift"]
    else:
        pool = base_pool + ["slow_push_in", "slow_push_out", "diagonal_drift", "slow_pan_left", "slow_pan_right"]

    return pool or list(COMPANION_EFFECT_POOL)

def choose_group_motion_profile(_emotion, rng, ctx=None):
    roll = rng.random()
    if roll < 0.55:
        intensity = "weak"
    elif roll < 0.92:
        intensity = "medium"
    else:
        intensity = "strong"

    pool = build_effect_pool(intensity)
    last_motion = ctx.last_group_motion if ctx else ""
    recent_motions = ctx.snapshot_recent_group_motions() if ctx else []
    recent_families = [motion_family(name) for name in recent_motions]
    candidates = [m for m in pool if m != last_motion]

    if recent_families[-1:] and recent_families[-1] == "pan":
        candidates = [m for m in candidates if motion_family(m) != "pan"]
    if recent_families[-2:] == ["push_in", "push_in"]:
        candidates = [m for m in candidates if motion_family(m) != "push_in"]
    if recent_families[-2:] == ["static", "static"]:
        candidates = [m for m in candidates if motion_family(m) != "static"]

    if recent_families[-3:].count("pan") >= 1:
        candidates = [m for m in candidates if motion_family(m) != "pan" or recent_families[-1] != "pan"]

    if rng.random() < 0.28:
        candidates = [m for m in candidates if motion_family(m) in ("static", "hold", "push_out")] or candidates

    if not candidates:
        candidates = [m for m in pool if motion_family(m) != "pan"] or pool

    motion_name = rng.choice(candidates) if candidates else "still_hold"
    if motion_name not in MOTION_EFFECTS:
        motion_name = "still_hold"
    return {"motion_name": motion_name, "intensity": intensity}

def choose_group_transition(rng, clip_count=1, ctx=None):
    recent_transitions = ctx.snapshot_recent_group_transitions() if ctx else []
    last_transition = recent_transitions[-1] if recent_transitions else ""
    pool = list(TRANSITION_EFFECT_POOL)

    if clip_count <= 2:
        pool += [{"name": "cut", "duration": 0.00}]
    if last_transition != "cut":
        pool += [{"name": "cut", "duration": 0.00}]
    if recent_transitions[-2:] == ["dissolve", "dissolve"]:
        pool = [item for item in pool if item["name"] != "dissolve"] or pool

    picked = rng.choice(pool)
    if picked["name"] == "cut":
        return {"name": "cut", "duration": 0.0}

    duration = min(max(float(picked["duration"]), 0.50), 0.85)
    return {"name": picked["name"], "duration": duration}

def get_media_duration(media_path, ctx=None, logger=None):
    info = probe_media_info(media_path, ctx=ctx, logger=logger)
    duration_raw = ((info.get("format") or {}).get("duration"))
    try:
        duration = float(duration_raw)
    except (TypeError, ValueError) as exc:
        raise RuntimeError(f"媒体时长解析失败: {media_path}") from exc
    if duration <= 0:
        raise RuntimeError(f"媒体时长非法: {media_path}")
    return duration


def get_video_stream_duration(media_path, ctx=None, logger=None):
    info = probe_media_info(media_path, ctx=ctx, logger=logger)
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "video":
            continue
        duration_raw = stream.get("duration")
        if duration_raw in (None, "", "N/A"):
            continue
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    return get_media_duration(media_path, ctx=ctx, logger=logger)

def get_audio_stream_duration(media_path, ctx=None, logger=None):
    info = probe_media_info(media_path, ctx=ctx, logger=logger)
    for stream in info.get("streams", []):
        if stream.get("codec_type") != "audio":
            continue
        duration_raw = stream.get("duration")
        if duration_raw in (None, "", "N/A"):
            continue
        try:
            duration = float(duration_raw)
        except (TypeError, ValueError):
            continue
        if duration > 0:
            return duration
    return 0.0

def validate_av_duration_match(media_path, expected_duration=None, label="媒体", ctx=None):
    video_duration = get_video_stream_duration(media_path, ctx=ctx)
    audio_duration = get_audio_stream_duration(media_path, ctx=ctx)
    reference = float(expected_duration or video_duration or 0.0)
    tolerance = max(8.0, reference * 0.04)

    if audio_duration <= 0:
        raise RuntimeError(f"{label} 音频轨缺失或时长非法: audio={audio_duration:.2f}s")

    audio_delta = abs(audio_duration - reference)
    if reference > 0 and audio_delta > tolerance:
        raise RuntimeError(
            f"{label} 音视频时长不一致: audio={audio_duration:.2f}s "
            f"expected={reference:.2f}s video={video_duration:.2f}s delta={audio_delta:.2f}s"
        )

    return {
        "video_duration": video_duration,
        "audio_duration": audio_duration,
        "reference_duration": reference,
        "audio_delta": audio_delta,
    }

def validate_final_consistency(ctx, grouped_tasks, valid_clips, final_out):
    expected_groups = len(grouped_tasks)
    actual_groups = len(valid_clips)
    if expected_groups <= 0:
        raise RuntimeError("最终一致性校验失败: 没有有效分组")

    if ctx.failure_policy["allow_partial_output"] is False and actual_groups != expected_groups:
        raise RuntimeError(f"最终一致性校验失败: group {actual_groups}/{expected_groups}")

    if actual_groups != ctx.total_groups_success:
        raise RuntimeError(
            f"最终一致性校验失败: success_groups={ctx.total_groups_success}, clips={actual_groups}"
        )

    concat_inputs = actual_groups

    if not os.path.exists(final_out):
        raise RuntimeError(f"最终一致性校验失败: 输出文件不存在 {final_out}")
    if not is_valid_media_file(final_out, expect_video=True, expect_audio=True, ctx=ctx):
        raise RuntimeError(f"最终一致性校验失败: 输出文件无效 {final_out}")

    final_duration = get_video_stream_duration(final_out, ctx=ctx)

    # 以最终成功拼接进 raw/final 的分组视频片段时长之和为准，
    # 避免被混音后的长音频流或章节 BGM 拉高参考值。
    expected_duration = 0.0
    for clip in valid_clips:
        expected_duration += get_video_stream_duration(clip, ctx=ctx)

    tolerance = max(8.0, expected_duration * 0.08)
    duration_delta = abs(final_duration - expected_duration)
    if expected_duration > 0 and duration_delta > tolerance:
        raise RuntimeError(
            f"最终一致性校验失败: duration={final_duration:.2f}s expected={expected_duration:.2f}s delta={duration_delta:.2f}s"
        )
    av_check = validate_av_duration_match(final_out, expected_duration=expected_duration, label="最终输出", ctx=ctx)

    log_line(
        f"🧾 对账完成: tasks={ctx.total_tasks_expected} | groups={actual_groups}/{expected_groups} | concat_inputs={concat_inputs} | final_duration={final_duration:.2f}s | final_audio={av_check['audio_duration']:.2f}s | expected_video={expected_duration:.2f}s | delta={duration_delta:.2f}s",
        ctx=ctx,
    )


def _parse_group_index_from_clip(path):
    match = re.search(r"group_(\d{3,4})_done\.mp4$", os.path.basename(str(path)), flags=re.I)
    if not match:
        return None
    return int(match.group(1))


def write_subtitle_timeline(ctx, chapter_name, grouped_tasks, valid_clips, final_name):
    """
    Export the real timeline used by the final concatenated video.

    Groups can become shorter than their merged TTS audio when visual xfade is used
    and the mux step applies -shortest. Subtitles must follow the final group clip
    durations instead of the raw cumulative wav durations.
    """
    if not ctx or not grouped_tasks or not valid_clips:
        return None

    clip_by_group = {}
    for clip in valid_clips:
        group_idx = _parse_group_index_from_clip(clip)
        if group_idx is not None:
            clip_by_group[group_idx] = clip

    timeline_groups = []
    timeline_scenes = []
    cursor = 0.0
    total_audio_duration = 0.0
    total_video_duration = 0.0
    total_clipped = 0.0

    for group_idx, group_tasks in enumerate(grouped_tasks):
        clip = clip_by_group.get(group_idx)
        if not clip or not os.path.exists(clip):
            continue

        try:
            group_video_duration = get_video_stream_duration(clip, ctx=ctx)
        except Exception as exc:
            ctx.record_error("subtitle_timeline_probe", exc)
            continue

        group_audio_duration = sum(float(task.get("duration") or 0.0) for task in group_tasks)
        group_start = cursor
        group_end = group_start + max(0.0, group_video_duration)
        group_clipped = max(0.0, group_audio_duration - group_video_duration)
        total_audio_duration += group_audio_duration
        total_video_duration += group_video_duration
        total_clipped += group_clipped

        scene_cursor = group_start
        group_scene_entries = []
        for task in group_tasks:
            source_duration = max(0.0, float(task.get("duration") or 0.0))
            scene_start = scene_cursor
            intended_scene_end = scene_start + source_duration
            scene_end = min(intended_scene_end, group_end)
            actual_duration = max(0.0, scene_end - scene_start)
            clipped_seconds = max(0.0, source_duration - actual_duration)
            entry = {
                "idx": int(task.get("idx") or 0),
                "scene_id": str(task.get("scene_id") or ""),
                "group_index": group_idx,
                "start": round(scene_start, 6),
                "end": round(scene_end, 6),
                "duration": round(actual_duration, 6),
                "source_duration": round(source_duration, 6),
                "clipped_seconds": round(clipped_seconds, 6),
            }
            timeline_scenes.append(entry)
            group_scene_entries.append(entry["scene_id"])
            scene_cursor = intended_scene_end

        timeline_groups.append(
            {
                "group_index": group_idx,
                "clip": clip,
                "start": round(group_start, 6),
                "end": round(group_end, 6),
                "video_duration": round(group_video_duration, 6),
                "audio_duration": round(group_audio_duration, 6),
                "clipped_seconds": round(group_clipped, 6),
                "scene_ids": group_scene_entries,
            }
        )
        cursor = group_end

    if not timeline_groups:
        return None

    out_path = os.path.join(ctx.paths.output_dir, f"{os.path.splitext(final_name)[0]}_subtitle_timeline.json")
    payload = {
        "schema": "swordcoming.subtitle_timeline.v1",
        "chapter_name": chapter_name,
        "script_version": SCRIPT_VERSION,
        "run_id": ctx.run_id,
        "final_video": final_name,
        "total_audio_duration": round(total_audio_duration, 6),
        "total_video_duration": round(total_video_duration, 6),
        "total_clipped_seconds": round(total_clipped, 6),
        "groups": timeline_groups,
        "scenes": timeline_scenes,
    }

    os.makedirs(ctx.paths.output_dir, exist_ok=True)
    with open(out_path, "w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)

    log_line(
        f"🧭 字幕时间线: groups={len(timeline_groups)} scenes={len(timeline_scenes)} clipped={total_clipped:.3f}s path={out_path}",
        ctx=ctx,
    )
    return out_path


def _merge_intervals(intervals):
    if not intervals:
        return []
    intervals = sorted(intervals, key=lambda x: (x[0], x[1]))
    merged = [list(intervals[0])]
    for start, end in intervals[1:]:
        last = merged[-1]
        if start <= last[1]:
            last[1] = max(last[1], end)
        else:
            merged.append([start, end])
    return [(float(s), float(e)) for s, e in merged]


def _build_ffmpeg_between_expr(intervals):
    if not intervals:
        return ""
    parts = [f"between(t,{start:.3f},{end:.3f})" for start, end in intervals]
    if len(parts) == 1:
        return parts[0]
    return "(" + "+".join(parts) + ")"


def build_chapter_bgm_duck_plan(ctx, chapter_name, tasks):
    """
    保留整章 BGM，但根据 scene 级混音结果对整章 BGM 做动态 duck / mute。
    - 强场景：mute 到接近静音
    - 轻场景：duck 到较低音量
    """
    try:
        audio_root = getattr(ctx.paths, "audio_search_root", None) or os.path.join(AUDIO_ROOT, chapter_name)
        debug_dir = os.path.join(audio_root, "_mix_debug")
        if not os.path.isdir(debug_dir):
            return {
                "strong_intervals": [],
                "weak_intervals": [],
                "summary": "mix_debug_missing",
            }

        strong_intervals = []
        weak_intervals = []
        cursor = 0.0
        scene_mix_count = 0
        scene_bgm_count = 0

        for task in tasks:
            duration = float(task.get("duration") or 0.0)
            start = cursor
            end = cursor + max(0.0, duration)
            cursor = end

            audio_path = str(task.get("audio_path") or "")
            base = os.path.splitext(os.path.basename(audio_path))[0]
            mix_debug = os.path.join(debug_dir, f"{base}.mix_debug.json")

            if not os.path.exists(mix_debug):
                continue

            try:
                with open(mix_debug, "r", encoding="utf-8") as f:
                    data = json.load(f)
            except Exception:
                continue

            if data.get("mix_skipped") is True:
                continue

            scene_mix_count += 1

            scene_type = str(task.get("scene_type") or "").strip().lower()
            has_bgm = bool(data.get("bgm"))
            has_ambience = bool(data.get("ambience"))
            has_sfx = bool(data.get("sfx"))
            ambience_ids = [str(x).lower() for x in (data.get("ambience") or [])]

            if has_bgm:
                scene_bgm_count += 1

            strong_scene = (
                has_bgm
                or scene_type in {"death", "climax", "final_battle", "reveal"}
                or any(any(k in amb for k in ["thunder", "rain", "tavern", "temple", "storm"]) for amb in ambience_ids)
                or bool(data.get("absolute_silence"))
            )

            weak_scene = (
                (not strong_scene)
                and (
                    has_ambience
                    or has_sfx
                )
            )

            if strong_scene:
                strong_intervals.append((max(0.0, start - 0.25), end + 0.90))
            elif weak_scene:
                weak_intervals.append((max(0.0, start - 0.15), end + 0.50))

        strong_intervals = _merge_intervals(strong_intervals)
        weak_intervals = _merge_intervals(weak_intervals)

        return {
            "strong_intervals": strong_intervals,
            "weak_intervals": weak_intervals,
            "summary": f"scene_mix={scene_mix_count}, scene_bgm={scene_bgm_count}, strong={len(strong_intervals)}, weak={len(weak_intervals)}",
        }
    except Exception as e:
        if ctx:
            ctx.record_error("chapter_bgm_duck_plan", e)
        return {
            "strong_intervals": [],
            "weak_intervals": [],
            "summary": f"duck_plan_error:{e}",
        }

def effective_xfade_duration(transition_profile, durations):
    if not durations or transition_profile.get("name") == "cut":
        return 0.0
    return min(
        float(transition_profile.get("duration") or 0.0),
        max(0.35, min(durations) * 0.10),
    )


def compensate_time_slots_for_xfade(time_slots, transition_profile):
    if not time_slots or len(time_slots) <= 1 or transition_profile.get("name") == "cut":
        return list(time_slots), 0.0
    transition_duration = effective_xfade_duration(transition_profile, time_slots)
    if transition_duration <= 0:
        return list(time_slots), 0.0
    compensated = list(time_slots)
    padding = transition_duration * max(0, len(compensated) - 1)
    compensated[-1] += padding
    return compensated, padding


def merge_video_with_xfade(sub_clips, durations, output_path, transition_profile, ctx=None):
    if len(sub_clips) == 1:
        return sub_clips[0]

    transition_name = transition_profile["name"]
    if transition_name == "cut":
        concat_txt = os.path.join(os.path.dirname(output_path), f"{os.path.splitext(os.path.basename(output_path))[0]}_concat.txt")
        try:
            write_concat_list(sub_clips, concat_txt, "组内硬切拼接")
            run_ffmpeg(
                ["-y", "-f", "concat", "-safe", "0", "-i", concat_txt, "-c", "copy", output_path],
                desc=f"组内硬切合成 {os.path.basename(output_path)}",
                timeout=CONFIG["FFMPEG_TIMEOUT"],
                ctx=ctx,
            )
        finally:
            if os.path.exists(concat_txt):
                try:
                    os.remove(concat_txt)
                except OSError:
                    pass
        if not is_valid_media_file(output_path, expect_video=True, ctx=ctx):
            raise RuntimeError(f"组内硬切输出无效: {output_path}")
        return output_path

    transition_duration = effective_xfade_duration(transition_profile, durations)

    cmd = [FFMPEG_BIN, "-y"]
    for clip in sub_clips:
        cmd.extend(["-i", clip])

    filter_parts = []
    cumulative = float(durations[0])
    current_label = "[0:v]"
    for idx in range(1, len(sub_clips)):
        next_label = f"[{idx}:v]"
        out_label = f"[v{idx}]"
        offset = max(cumulative - transition_duration, 0.0)
        filter_parts.append(
            f"{current_label}{next_label}xfade=transition={transition_name}:duration={transition_duration:.2f}:offset={offset:.2f}{out_label}"
        )
        cumulative += float(durations[idx]) - transition_duration
        current_label = out_label

    filter_parts.append(f"{current_label}format=yuv420p[vout]")
    gpu_cfg = AMD_GPU_CONFIG.get(RENDER_MODE, AMD_GPU_CONFIG["preview"])
    cmd.extend(
        [
            "-filter_complex", ";".join(filter_parts),
            "-map", "[vout]",
            "-r", str(FPS),
            "-c:v", gpu_cfg["encoder"], *gpu_cfg["params"],
            "-pix_fmt", "yuv420p",
            output_path,
        ]
    )

    run_command(
        cmd,
        desc=f"组内软切换合成 {os.path.basename(output_path)}",
        timeout=CONFIG["FFMPEG_TIMEOUT"],
        ctx=ctx,
    )
    if not is_valid_media_file(output_path, expect_video=True, ctx=ctx):
        raise RuntimeError(f"组内软切换输出无效: {output_path}")
    return output_path

# ================= V7.4 核心：渲染引擎 (Intensity + 稳态) =================

def render_single_subclip_v7(
    img_path,
    duration,
    emotion,
    output_path,
    is_first=False,
    is_last=False,
    ctx=None,
    rng=None,
    motion_name=None,
):
    # V7.4 优化：IO 预检查
    if is_valid_media_file(output_path, expect_video=True, ctx=ctx):
        return True

    chooser = rng or (ctx.make_rng("subclip", output_path, img_path, f"{duration:.3f}") if ctx else random)

    effect_name = motion_name or "still_hold"
    effect_cfg = MOTION_EFFECTS.get(effect_name, MOTION_EFFECTS["still_hold"])
    log_effect(effect_name, ctx=ctx)
    
    q_cfg = QUALITY_CONFIG.get(RENDER_MODE, QUALITY_CONFIG["preview"])
    frames = max(int(duration * FPS), 30)
    
    # 滤镜链构建
    vf_parts = []
    max_frames = str(max(frames - 1, 1))
    ease_expr = f"(0.5-0.5*cos(PI*n/{max_frames}))"

    if effect_cfg.get("type") == "crop_motion":
        canvas_zoom = max(1.0, float(effect_cfg.get("canvas_zoom", 1.0)))
        canvas_w = int(math.ceil(WORK_W * canvas_zoom / 2.0) * 2)
        canvas_h = int(math.ceil(WORK_H * canvas_zoom / 2.0) * 2)
        max_x = max(canvas_w - WORK_W, 0)
        max_y = max(canvas_h - WORK_H, 0)
        x0 = float(effect_cfg.get("x0", 0.5))
        x1 = float(effect_cfg.get("x1", x0))
        y0 = float(effect_cfg.get("y0", 0.5))
        y1 = float(effect_cfg.get("y1", y0))
        x_expr = f"({max_x})*({x0:.4f}+({x1 - x0:.4f})*{ease_expr})"
        y_expr = f"({max_y})*({y0:.4f}+({y1 - y0:.4f})*{ease_expr})"
        # Keep animated crop positions on even pixels to avoid chroma/rounding shimmer.
        x_crop_expr = f"floor(({x_expr})/2)*2"
        y_crop_expr = f"floor(({y_expr})/2)*2"
        vf_parts.append(
            f"scale={canvas_w}:{canvas_h}:force_original_aspect_ratio=increase:flags={q_cfg['upscale_flags']}"
        )
        vf_parts.append(f"crop={canvas_w}:{canvas_h}")
        vf_parts.append(f"crop={WORK_W}:{WORK_H}:x='{x_crop_expr}':y='{y_crop_expr}'")
    else:
        vf_parts.append(f"scale={WORK_W}:{WORK_H}:force_original_aspect_ratio=increase:flags={q_cfg['upscale_flags']}")
        vf_parts.append(f"crop={WORK_W}:{WORK_H}")
    
    vf_parts.append(f"scale={TARGET_W}:{TARGET_H}:flags={q_cfg['downscale_flags']},setsar=1")
    vf_parts.append(q_cfg['sharpen'])
    vf_parts.append(EMOTION_COLOR_GRADING["neutral"]["filter"])
    
    # 呼吸转场逻辑 (V7.1 核心)
    actual_fade_dur = min(SCENE_FADE_DURATION, duration * 0.20)
    actual_int_fade = min(INTERNAL_FADE_DURATION, duration * 0.20)
    fade_filters = []
    if is_first:
        fade_filters.append(f"fade=t=in:st=0:d={actual_fade_dur:.2f}")
    if is_last:
        start_out = max(0, duration - actual_fade_dur)
        fade_filters.append(f"fade=t=out:st={start_out:.2f}:d={actual_fade_dur:.2f}")
        
    if not is_first and not is_last and chooser.random() < INTERNAL_FADE_PROBABILITY:
        if ctx:
            ctx.record_fade()
        fade_filters.append(f"fade=t=in:st=0:d={actual_int_fade:.2f}")
        start_out = max(0, duration - actual_int_fade)
        fade_filters.append(f"fade=t=out:st={start_out:.2f}:d={actual_int_fade:.2f}")
        
    vf_str = ",".join(vf_parts)
    if fade_filters: vf_str += "," + ",".join(fade_filters)

    gpu_cfg = AMD_GPU_CONFIG.get(RENDER_MODE, AMD_GPU_CONFIG["preview"])
    cmd = [
        "-y", "-loop", "1", "-i", img_path, "-vf", vf_str,
        "-r", str(FPS), "-vsync", "cfr", "-frames:v", str(frames),
        "-c:v", gpu_cfg["encoder"], *gpu_cfg["params"],
        "-pix_fmt", "yuv420p", output_path
    ]
    
    try:
        if ctx:
            with ctx.gpu_semaphore:
                run_ffmpeg(
                    cmd,
                    desc=f"渲染子片段 {os.path.basename(img_path)}",
                    timeout=CONFIG["FFMPEG_TIMEOUT"],
                    ctx=ctx,
                )
        else:
            run_ffmpeg(
                cmd,
                desc=f"渲染子片段 {os.path.basename(img_path)}",
                timeout=CONFIG["FFMPEG_TIMEOUT"],
            )
        if not is_valid_media_file(output_path, expect_video=True, ctx=ctx):
            raise RuntimeError(f"子片段输出无效: {output_path}")
        return True
    except Exception as e:
        if ctx:
            if isinstance(e, CommandExecutionError):
                ctx.record_error("subclip_render", e, cmd=e.cmd, returncode=e.returncode)
            else:
                ctx.record_error("subclip_render", e)
        log_line(f"❌ 子片段渲染失败: {os.path.basename(img_path)} | {e}", level="error", ctx=ctx)
        return False

# ================= 流程控制 =================

def process_scene_v7(task, ctx=None):
    idx = task["idx"]
    scene_id = task["scene_id"]
    scene_type = task["scene_type"]
    emotion = task["emotion"]
    audio_path = task["audio_path"]
    duration = task["duration"]
    out_path = task["out_path"]
    fixed_image = task.get("fixed_image")
    
    if is_valid_media_file(out_path, expect_video=True, expect_audio=True, ctx=ctx): 
        return out_path
        
    temp_root = ctx.temp_dir if ctx else os.path.dirname(out_path)
    scene_tmp_dir = os.path.join(temp_root, f"scene_{idx}_{scene_id}")
    os.makedirs(scene_tmp_dir, exist_ok=True)
    
    try:
        # 使用回滚后的 V7.1 节奏算法
        log_line(f"▶️ 开始渲染场景 {scene_id} | 时长 {duration:.1f}s | 情绪 {emotion} | 场景 {scene_type}", ctx=ctx)
        scene_rng = ctx.make_rng("scene", scene_id, idx) if ctx else None
        time_slots = calculate_time_slots(duration, rng=scene_rng)
        motion_profile = choose_group_motion_profile(emotion, scene_rng or random, ctx=ctx)
        if fixed_image:
            images = [fixed_image for _ in time_slots]
        else:
            images = get_coherent_image_sequence(scene_type, emotion, len(time_slots), ctx=ctx, rng=scene_rng)
        
        sub_clips = []
        for i, (img, slot_dur) in enumerate(zip(images, time_slots)):
            sub_out = os.path.join(scene_tmp_dir, f"sub_{i:03d}.mp4")
            success = render_single_subclip_v7(
                img, slot_dur, emotion, sub_out,
                is_first=(i==0), is_last=(i==len(time_slots)-1),
                ctx=ctx,
                rng=(ctx.make_rng("scene", scene_id, idx, "subclip", i) if ctx else None),
                motion_name=motion_profile["motion_name"],
            )
            if success: sub_clips.append(sub_out)
                
        if not sub_clips: return None
            
        concat_list = os.path.join(scene_tmp_dir, "concat.txt")
        write_concat_list(sub_clips, concat_list, f"场景 {scene_id} 子片段")
        
        # V7.4 优化：NAS 缓冲延迟，生死攸关
        time.sleep(0.3)
                
        cmd = [
            "-y", "-f", "concat", "-safe", "0", "-i", concat_list,
            "-i", audio_path,
            "-map", "0:v:0",
            "-map", "1:a:0",
            "-c:v", "copy",
            "-c:a", "aac",
            "-ar", "48000",
            "-ac", "2",
            "-shortest",
            out_path
        ]
        run_ffmpeg(
            cmd,
            desc=f"合成场景 {scene_id}",
            timeout=CONFIG["FFMPEG_TIMEOUT"],
            ctx=ctx,
        )
        if not is_valid_media_file(out_path, expect_video=True, expect_audio=True, ctx=ctx):
            raise RuntimeError(f"场景输出无效: {out_path}")
        if ctx:
            ctx.bump_scenes_processed()
        log_line(f"✅ 场景 {scene_id} 完成 | 耗时: {duration:.1f}s", ctx=ctx)
        return out_path
        
    except Exception as e:
        if ctx:
            if isinstance(e, CommandExecutionError):
                ctx.record_error("scene", e, scene_id=scene_id, cmd=e.cmd, returncode=e.returncode)
            else:
                ctx.record_error("scene", e, scene_id=scene_id)
        log_line(f"❌ 场景 {scene_id} 失败: {e}", level="error", ctx=ctx)
        return None
    finally: force_cleanup(scene_tmp_dir)

def process_group_v7(group_idx, group_tasks, ctx=None):
    group_id = f"group_{group_idx:03d}"
    temp_root = ctx.temp_dir if ctx else os.path.dirname(group_tasks[0]["out_path"])
    group_tmp_dir = os.path.join(temp_root, group_id)
    os.makedirs(group_tmp_dir, exist_ok=True)
    out_path = os.path.join(temp_root, f"{group_id}_done.mp4")

    if is_valid_media_file(out_path, expect_video=True, expect_audio=True, ctx=ctx):
        return out_path

    try:
        concat_audio_txt = os.path.join(group_tmp_dir, "audio_concat.txt")
        write_concat_list([t["audio_path"] for t in group_tasks], concat_audio_txt, f"分组 {group_idx:03d} 音频")

        merged_audio = os.path.join(group_tmp_dir, "merged_audio.wav")
        run_ffmpeg(
            [
                "-y", "-f", "concat", "-safe", "0", "-i", concat_audio_txt,
                "-c:a", "pcm_s16le",
                "-ar", "48000",
                "-ac", "2",
                merged_audio
            ],
            desc=f"合并分组 {group_idx:03d} 音频",
            timeout=CONFIG["FFMPEG_TIMEOUT"],
            ctx=ctx,
        )
        if not is_valid_media_file(merged_audio, expect_audio=True, ctx=ctx):
            raise RuntimeError(f"分组音频输出无效: {merged_audio}")
        validate_audio_decodable(merged_audio, ctx=ctx, desc=f"校验分组 {group_idx:03d} 合并音频")

        group_duration = get_audio_duration(merged_audio, ctx=ctx)
        first_task = group_tasks[0]
        scene_type = first_task["scene_type"]
        emotion = first_task["emotion"]
        scene_ids = ",".join(str(t["scene_id"]) for t in group_tasks[:8])
        if len(group_tasks) > 8:
            scene_ids += "..."

        log_line(f"▶️ 开始渲染分组 {group_idx:03d} (场景 {scene_ids}) | 总时长 {group_duration:.1f}s", ctx=ctx)

        group_rng = ctx.make_rng("group", group_idx, scene_ids, f"{group_duration:.3f}") if ctx else None
        time_slots = calculate_time_slots(group_duration, rng=group_rng)
        motion_profile = choose_group_motion_profile(emotion, group_rng or random, ctx=ctx)
        transition_profile = choose_group_transition(group_rng or random, clip_count=len(time_slots), ctx=ctx)
        time_slots, xfade_padding = compensate_time_slots_for_xfade(time_slots, transition_profile)
        if ctx:
            ctx.set_last_group_motion(motion_profile["motion_name"])
            ctx.set_last_group_transition(transition_profile["name"])
        log_line(
            f"🎥 分组 {group_idx:03d} 镜头语言: motion={motion_profile['motion_name']} | transition={transition_profile['name']}({transition_profile['duration']:.2f}s)",
            ctx=ctx,
        )
        if xfade_padding > 0:
            log_line(
                f"🧷 分组 {group_idx:03d} 软转场补偿: +{xfade_padding:.3f}s，避免 -shortest 裁掉尾部音频",
                ctx=ctx,
            )
        fixed_image = None
        for task in group_tasks:
            candidate = task.get("fixed_image") or task.get("visual_group_image")
            if candidate and os.path.exists(candidate):
                fixed_image = candidate
                break

        if fixed_image:
            images = [fixed_image for _ in time_slots]
            log_line(
                f"🖼️ 分组 {group_idx:03d} 使用 visual_groups 组图: {os.path.basename(fixed_image)}",
                ctx=ctx,
            )
        else:
            images = get_coherent_image_sequence(scene_type, emotion, len(time_slots), ctx=ctx, rng=group_rng)

        sub_clips = []
        sub_durations = []
        for i, (img, slot_dur) in enumerate(zip(images, time_slots)):
            sub_out = os.path.join(group_tmp_dir, f"sub_{i:03d}.mp4")
            success = render_single_subclip_v7(
                img, slot_dur, emotion, sub_out,
                is_first=(i == 0), is_last=(i == len(time_slots) - 1),
                ctx=ctx,
                rng=(ctx.make_rng("group", group_idx, "subclip", i, os.path.basename(img), f"{slot_dur:.3f}") if ctx else None),
                motion_name=motion_profile["motion_name"],
            )
            if success:
                sub_clips.append(sub_out)
                sub_durations.append(slot_dur)

        if not sub_clips:
            return None

        video_stream = None
        if len(sub_clips) == 1:
            video_stream = sub_clips[0]
        else:
            video_stream = os.path.join(group_tmp_dir, "video_xfade.mp4")
            merge_video_with_xfade(sub_clips, sub_durations, video_stream, transition_profile, ctx=ctx)

        run_ffmpeg(
            [
                "-y", "-i", video_stream,
                "-i", merged_audio,
                "-map", "0:v:0",
                "-map", "1:a:0",
                "-dn",
                "-sn",
                "-c:v", "copy",
                "-c:a", "aac",
                "-b:a", "128k",
                "-ar", "48000",
                "-ac", "2",
                "-movflags", "+faststart",
                "-shortest",
                out_path
            ],
            desc=f"合成分组 {group_idx:03d}",
            timeout=CONFIG["FFMPEG_TIMEOUT"],
            ctx=ctx,
        )
        if not is_valid_media_file(out_path, expect_video=True, expect_audio=True, ctx=ctx):
            raise RuntimeError(f"分组输出无效: {out_path}")
        validate_audio_decodable(out_path, ctx=ctx, desc=f"校验分组 {group_idx:03d} 输出音频")

        if ctx:
            ctx.bump_scenes_processed(len(group_tasks))
        log_line(f"✅ 分组 {group_idx:03d} 完成", ctx=ctx)
        return out_path

    except Exception as e:
        if ctx:
            if isinstance(e, CommandExecutionError):
                ctx.record_error("group", e, group_idx=group_idx, cmd=e.cmd, returncode=e.returncode)
            else:
                ctx.record_error("group", e, group_idx=group_idx)
        log_line(f"❌ 分组 {group_idx:03d} 失败: {e}", level="error", ctx=ctx)
        return None
    finally:
        force_cleanup(group_tmp_dir)

def get_final_video_encoder_config():
    return CHAPTER_TAG_EXPORT_CONFIG

def render_final_with_chapter_tag(source_video, output_video, chapter_name, bgm_file=None, ctx=None, bgm_duck_plan=None):
    tag_filter = build_chapter_tag_filter(chapter_name)
    encoder_cfg = get_final_video_encoder_config()
    cmd = ["-y", "-i", source_video]

    if bgm_file and os.path.exists(bgm_file):
        video_duration = get_video_stream_duration(source_video, ctx=ctx)
        bgm_duck_plan = bgm_duck_plan or {}
        strong_intervals = bgm_duck_plan.get("strong_intervals") or []
        weak_intervals = bgm_duck_plan.get("weak_intervals") or []

        base_vol = float(BGM_VOLUME)
        weak_vol = max(base_vol * 0.12, 0.0008)
        mute_vol = 0.0001

        volume_expr = f"{base_vol:.6f}"
        weak_expr = _build_ffmpeg_between_expr(weak_intervals)
        strong_expr = _build_ffmpeg_between_expr(strong_intervals)
        if weak_expr:
            volume_expr = f"if({weak_expr},{weak_vol:.6f},{volume_expr})"
        if strong_expr:
            volume_expr = f"if({strong_expr},{mute_vol:.6f},{volume_expr})"

        cmd += [
            "-stream_loop", "-1", "-i", bgm_file,
            "-filter_complex",
            (
                f"[0:v]{tag_filter}[v];"
                f"[0:a]atrim=0:{video_duration:.3f},asetpts=N/SR/TB[maina];"
                f"[1:a]atrim=0:{video_duration:.3f},asetpts=N/SR/TB,volume='{volume_expr}'[bgm];"
                "[maina][bgm]amix=inputs=2:duration=first:dropout_transition=0:normalize=0,"
                "alimiter=limit=0.95[aout]"
            ),
            "-map", "[v]",
            "-map", "[aout]",
            "-c:v", encoder_cfg["encoder"], *encoder_cfg["params"],
            "-c:a", "aac",
            "-pix_fmt", "yuv420p",
            "-movflags", "+faststart",
            "-t", f"{video_duration:.3f}",
            "-shortest",
            output_video,
        ]
        run_ffmpeg(
            cmd,
            desc="输出最终视频（章节标签 + BGM）",
            timeout=CONFIG["FFMPEG_TIMEOUT"],
            ctx=ctx,
        )
        return

    cmd += [
        "-map", "0:v:0",
        "-map", "0:a:0",
        "-dn",
        "-sn",
        "-vf", tag_filter,
        "-c:v", encoder_cfg["encoder"], *encoder_cfg["params"],
        "-c:a", "aac",
        "-b:a", "128k",
        "-ar", "48000",
        "-ac", "2",
        "-pix_fmt", "yuv420p",
        "-movflags", "+faststart",
        output_video,
    ]
    run_ffmpeg(
        cmd,
        desc="输出最终视频（章节标签）",
        timeout=CONFIG["FFMPEG_TIMEOUT"],
        ctx=ctx,
    )

def build_arg_parser():
    parser = argparse.ArgumentParser(description="合成视频脚本")
    parser.add_argument("--project-root", default=PROJECT_ROOT, help="项目根目录")
    parser.add_argument("--run-id", default="", help="总控 run_id")
    parser.add_argument("--chapter-hint", default="auto", help="章节提示名")
    return parser

def main(args):
    global PROJECT_ROOT, MATERIAL_ROOT, JSON_DIR, AUDIO_ROOT, OUTPUT_DIR
    global VISUAL_GROUPS_DIR, GENERATED_IMAGES_DIR, DONE_IMAGES_DIR
    global LOCAL_TEMP_DIR, TEMP_DIR, BGM_FILE, AUDIO_SEARCH_ROOT, DEFAULT_IMG
    PROJECT_ROOT = os.path.abspath(args.project_root or PROJECT_ROOT)
    MATERIAL_ROOT = os.path.join(PROJECT_ROOT, "scene_library")
    JSON_DIR = os.path.join(PROJECT_ROOT, "visual_planning")
    AUDIO_ROOT = os.path.join(PROJECT_ROOT, "tts")
    OUTPUT_DIR = os.path.join(PROJECT_ROOT, "video_output")
    VISUAL_GROUPS_DIR = os.path.join(PROJECT_ROOT, "visual_groups")
    GENERATED_IMAGES_DIR = os.path.join(PROJECT_ROOT, "images")
    DONE_IMAGES_DIR = os.path.join(GENERATED_IMAGES_DIR, "done")
    LOCAL_TEMP_DIR = ""
    TEMP_DIR = LOCAL_TEMP_DIR
    BGM_FILE = os.path.join(PROJECT_ROOT, "bgm.mp3")
    AUDIO_SEARCH_ROOT = AUDIO_ROOT
    DEFAULT_IMG = os.path.join(MATERIAL_ROOT, "通用空镜", "default.png")
    paths = ProjectPaths.from_project_root(args.project_root or PROJECT_ROOT)
    ctx = build_run_context(paths, args.run_id)
    ctx.paths = ctx.paths.with_bgm_file(resolve_bgm_file(ctx.paths.project_root, rng=ctx.make_rng("bgm", ctx.paths.project_root)))
    log_line(f"🎬 === 剑来 AI 导演系统 {SCRIPT_VERSION_LABEL} (收口稳态版 - 自动模式) ===", ctx=ctx)
    validate_runtime(ctx.paths, logger=ctx.logger)
    log_line(f"🧭 RunContext: run_id={ctx.run_id} | seed={ctx.seed} | temp_dir={ctx.temp_dir}", ctx=ctx)
    log_line(f"📝 日志文件: {ctx.log_path}", ctx=ctx)
    log_line(
        "🛡️ 失败策略: missing_audio(strict=fail, non_strict=skip) | scene_failure=fail_group | group_failure=fail_batch | bgm_missing=skip_bgm | bgm_mix_failure=skip_bgm | partial_output=disabled",
        ctx=ctx,
    )

    lock_path = None
    flist = None
    final_raw = None
    try:
        lock_path = acquire_process_lock(ctx.paths.project_root, "shipin_5")
        os.makedirs(ctx.paths.temp_root, exist_ok=True)
        force_cleanup(ctx.temp_dir, required=False)
        os.makedirs(ctx.temp_dir, exist_ok=True)
        ctx.material_index, ctx.all_images_pool = index_materials(ctx.paths.material_root, ctx=ctx)
        strict_chapter = bool(args.chapter_hint and str(args.chapter_hint).strip().lower() != "auto")
        plan_file = get_latest_plan(ctx.paths.json_dir, args.chapter_hint, args.run_id, strict=strict_chapter)
        if not plan_file:
            raise RuntimeError(f"未找到剧本: chapter_hint={args.chapter_hint}")
        log_line(f"📄 锁定剧本: {os.path.basename(plan_file)}", ctx=ctx)

        with open(plan_file, 'r', encoding='utf-8') as f:
            data = json.load(f)
            chapter_name = data.get('chapter_name') or data.get('chapter') or data.get('data', {}).get('chapter', 'output')
            scenes = data.get('scenes', []) or data.get('data', {}).get('scenes', [])

        if not scenes:
            raise RuntimeError("剧本中没有 scenes")

        audio_dir = resolve_tts_dir(ctx.paths.audio_root, chapter_name, args.chapter_hint)
        if strict_chapter and not audio_dir:
            raise RuntimeError(f"未找到对应章节音频目录: chapter={chapter_name}, hint={args.chapter_hint}")
        ctx.paths = ctx.paths.with_audio_search_root(audio_dir or ctx.paths.audio_root)
        ctx.audio_index = build_audio_index(ctx.paths.audio_search_root)
        log_line(f"🎧 音频索引完成: root={ctx.paths.audio_search_root} | files={len(ctx.audio_index['filenames'])}", ctx=ctx)
        scope_tokens = build_scope_tokens(chapter_name, args.chapter_hint, args.run_id)
        auto_fallback = not strict_chapter and not audio_dir
        require_scope_match = auto_fallback and bool(scope_tokens)
        if auto_fallback:
            if not scope_tokens:
                raise RuntimeError("AUTO 模式未锁定章节音频目录，且无法生成章节范围 token，已拒绝继续以避免误命中")
            log_line(
                f"⚠️ AUTO 模式未锁定章节音频目录，启用范围保护: scope_tokens={','.join(scope_tokens) if scope_tokens else 'none'}",
                level="warning",
                ctx=ctx,
            )

        tasks = []
        missing = []
        log_line(f"🚀 渲染队列: {len(scenes)} 场景", ctx=ctx)
        for i, scene in enumerate(scenes):
            scene_type = scene.get('scene_type') or scene.get('tags', {}).get('scene', '通用场景')
            audio = scene.get('audio', {}).get('file') if isinstance(scene.get('audio'), dict) else scene.get('audio')
            scene_id = scene.get('scene_id', str(i))

            a_path = find_audio_smart(
                scene_id,
                i,
                audio,
                audio_index=ctx.audio_index,
                search_root=ctx.paths.audio_search_root,
                scope_tokens=scope_tokens,
                require_scope_match=require_scope_match,
            )
            if a_path:
                out = os.path.join(ctx.temp_dir, f"clip_{i:03d}.mp4")
                a_path = normalize_audio_for_video(a_path, ctx.temp_dir, scene_id=scene_id, ctx=ctx)
                duration = get_audio_duration(a_path, ctx=ctx)
                validate_scene_audio_duration(scene_id, a_path, duration, ctx=ctx)
                tasks.append({
                    "idx": i,
                    "scene_id": scene_id,
                    "scene_type": scene_type,
                    "emotion": scene.get('emotion', 'neutral').lower(),
                    "audio_path": a_path,
                    "duration": duration,
                    "out_path": out,
                })
                ctx.add_input_audio_duration(duration)
            else:
                try:
                    max_placeholder = float(CONFIG.get("MAX_FAILED_PLACEHOLDER_SECONDS") or 0)
                    if max_placeholder > 0 and PLACEHOLDER_SILENCE_SECONDS > max_placeholder:
                        raise RuntimeError(
                            f"scene {scene_id} 缺失音频占位过长: "
                            f"{PLACEHOLDER_SILENCE_SECONDS:.2f}s > {max_placeholder:.2f}s"
                        )
                    a_path = create_silence_placeholder(
                        scene_id=scene_id,
                        search_root=ctx.paths.audio_search_root,
                        chapter_name=chapter_name,
                        ctx=ctx,
                    )
                    out = os.path.join(ctx.temp_dir, f"clip_{i:03d}.mp4")
                    a_path = normalize_audio_for_video(a_path, ctx.temp_dir, scene_id=scene_id, ctx=ctx)
                    duration = get_audio_duration(a_path, ctx=ctx)
                    validate_scene_audio_duration(scene_id, a_path, duration, ctx=ctx)
                    tasks.append({
                        "idx": i,
                        "scene_id": scene_id,
                        "scene_type": scene_type,
                        "emotion": scene.get('emotion', 'neutral').lower(),
                        "audio_path": a_path,
                        "duration": duration,
                        "out_path": out,
                    })
                    ctx.add_input_audio_duration(duration)
                    ctx.record_error(
                        "missing_audio_placeholder",
                        f"scene {scene_id} 缺失原始音频，已生成 {PLACEHOLDER_SILENCE_SECONDS:.1f}s 静音占位",
                        scene_id=scene_id,
                    )
                    log_line(
                        f"⚠️ scene {scene_id} 缺失原始音频，已生成 {PLACEHOLDER_SILENCE_SECONDS:.1f}s 静音占位",
                        level="warning",
                        ctx=ctx,
                    )
                except Exception as placeholder_error:
                    missing.append(str(scene_id))
                    ctx.record_error("missing_audio", f"缺少音频: scene {scene_id}", scene_id=scene_id)
                    ctx.record_error("missing_audio_placeholder_failed", placeholder_error, scene_id=scene_id)
                    if auto_fallback:
                        ctx.record_error("audio_scope_guard", f"scene {scene_id} 未通过 AUTO 范围保护", scene_id=scene_id)

        ctx.set_total_tasks_expected(len(scenes))
        ctx.set_total_missing_audio(len(missing))
        if strict_chapter and missing:
            raise RuntimeError(f"缺少音频场景: {','.join(missing[:20])}")
        if missing:
            log_line(f"⚠️ 以下场景缺少音频，已跳过: {','.join(missing[:20])}", ctx=ctx)
        if not tasks:
            raise RuntimeError("没有可渲染的场景任务")

        visual_groups = load_visual_groups(ctx.paths.project_root, chapter_name, args.chapter_hint, ctx=ctx)
        grouped_tasks = build_groups_from_visual_groups(tasks, visual_groups, ctx=ctx) if visual_groups else None
        if grouped_tasks:
            log_line(f"🖼️ 使用 visual_groups 编排: {len(tasks)} 个碎片音频 -> {len(grouped_tasks)} 个连续渲染组", ctx=ctx)
        else:
            grouped_tasks = build_visual_hold_groups(tasks, MIN_VISUAL_HOLD_SECONDS)
            log_line(f"🖼️ 使用默认时长编排: {len(tasks)} 个碎片音频 -> {len(grouped_tasks)} 个连续渲染组", ctx=ctx)
        ctx.set_total_groups_expected(len(grouped_tasks))

        valid_clips = []
        failed_groups = []
        total_tasks = len(grouped_tasks)
        done_count = 0
        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as exe:
            futures = {exe.submit(process_group_v7, i, group, ctx): i for i, group in enumerate(grouped_tasks)}
            for f in as_completed(futures):
                res = f.result()
                done_count += 1
                if res:
                    valid_clips.append(res)
                    ctx.bump_groups_success()
                else:
                    failed_groups.append(futures[f])
                    ctx.bump_groups_failed()
                if done_count == 1 or done_count % 10 == 0 or done_count == total_tasks:
                    log_line(f"🎞️ 渲染进度: {done_count}/{total_tasks}", ctx=ctx)

        if failed_groups:
            failed_groups.sort()
            if ctx.failure_policy["group_failure"] == "fail_batch":
                raise RuntimeError(f"以下分组渲染失败: {','.join(str(i) for i in failed_groups[:20])}")
            if ctx.failure_policy["allow_partial_output"] is False:
                raise RuntimeError(f"以下分组渲染失败: {','.join(str(i) for i in failed_groups[:20])}")
            log_line(f"⚠️ 分组失败但策略允许继续: {','.join(str(i) for i in failed_groups[:20])}", level="warning", ctx=ctx)

        valid_clips.sort()
        if not valid_clips:
            raise RuntimeError("渲染失败")

        flist = os.path.join(ctx.temp_dir, "flist.txt")
        write_concat_list(valid_clips, flist, "最终视频分组")

        final_name = f"final_{chapter_name}_{SCRIPT_VERSION}.mp4"
        final_raw = os.path.join(ctx.temp_dir, "raw.mp4")
        final_out = os.path.join(ctx.paths.output_dir, final_name)
        chapter_tag_text = normalize_chapter_tag_text(chapter_name)
        write_subtitle_timeline(ctx, chapter_name, grouped_tasks, valid_clips, final_name)

        log_line("🔗 缝合最终视频...", ctx=ctx)
        try:
            run_ffmpeg(
                [
                    "-y", "-f", "concat", "-safe", "0", "-i", flist,
                    "-map", "0:v:0",
                    "-map", "0:a:0",
                    "-dn",
                    "-sn",
                    "-c", "copy",
                    "-movflags", "+faststart",
                    final_raw,
                ],
                desc="缝合最终视频",
                timeout=CONFIG["FFMPEG_TIMEOUT"],
                ctx=ctx,
            )
        except Exception as e:
            ctx.record_error("final_concat", e, cmd=getattr(e, "cmd", None), returncode=getattr(e, "returncode", None))
            if ctx.failure_policy["final_concat_failure"] == "fail_batch":
                raise
            log_line(f"⚠️ 最终 concat 失败但策略允许继续: {e}", level="warning", ctx=ctx)
        if not is_valid_media_file(final_raw, expect_video=True, expect_audio=True, ctx=ctx):
            ctx.record_error("final_concat", f"输出无效: {final_raw}")
            raise RuntimeError(f"最终拼接产物无效: {final_raw}")
        expected_raw_duration = sum(get_video_stream_duration(clip, ctx=ctx) for clip in valid_clips)
        try:
            validate_av_duration_match(final_raw, expected_duration=expected_raw_duration, label="最终拼接 raw", ctx=ctx)
        except Exception as av_error:
            ctx.record_error("final_concat_audio_duration", av_error)
            log_line(
                f"⚠️ 最终拼接 raw 音频时长异常，改用音频重编码方式重拼: {av_error}",
                level="warning",
                ctx=ctx,
            )
            force_cleanup(final_raw, required=False)
            run_ffmpeg(
                [
                    "-y", "-f", "concat", "-safe", "0", "-i", flist,
                    "-map", "0:v:0",
                    "-map", "0:a:0",
                    "-dn",
                    "-sn",
                    "-c:v", "copy",
                    "-c:a", "aac",
                    "-b:a", "128k",
                    "-ar", "48000",
                    "-ac", "2",
                    "-movflags", "+faststart",
                    final_raw,
                ],
                desc="重拼最终视频（修复音频时长）",
                timeout=CONFIG["FFMPEG_TIMEOUT"],
                ctx=ctx,
            )
            if not is_valid_media_file(final_raw, expect_video=True, expect_audio=True, ctx=ctx):
                raise RuntimeError(f"最终重拼产物无效: {final_raw}")
            validate_av_duration_match(final_raw, expected_duration=expected_raw_duration, label="最终重拼 raw", ctx=ctx)
        validate_audio_decodable(final_raw, ctx=ctx, desc="最终拼接 raw 音频完整解码校验")
        ctx.final_reference_duration = get_video_stream_duration(final_raw, ctx=ctx)

        log_line(f"🏷️ 章节小标签: {chapter_tag_text}", ctx=ctx)
        bgm_duck_plan = build_chapter_bgm_duck_plan(ctx, chapter_name, tasks)
        log_line(f"🎵 章节 BGM 动态让位计划: {bgm_duck_plan.get('summary','none')}", ctx=ctx)

        if os.path.exists(ctx.paths.bgm_file):
            log_line(f"🎵 使用 BGM: {os.path.basename(ctx.paths.bgm_file)}", ctx=ctx)
            try:
                render_final_with_chapter_tag(
                    final_raw,
                    final_out,
                    chapter_tag_text,
                    bgm_file=ctx.paths.bgm_file,
                    bgm_duck_plan=bgm_duck_plan,
                    ctx=ctx,
                )
                validate_av_duration_match(
                    final_out,
                    expected_duration=expected_raw_duration,
                    label="BGM 混音输出",
                    ctx=ctx,
                )
            except Exception as e:
                ctx.record_error("bgm_mix", e, cmd=getattr(e, "cmd", None), returncode=getattr(e, "returncode", None))
                if ctx.failure_policy["bgm_mix_failure"] == "skip_bgm":
                    log_line(f"⚠️ BGM 混音失败，按策略回退为无 BGM 但保留章节标签输出: {e}", level="warning", ctx=ctx)
                    render_final_with_chapter_tag(
                        final_raw,
                        final_out,
                        chapter_tag_text,
                        bgm_file=None,
                        ctx=ctx,
                    )
                else:
                    raise
        else:
            ctx.record_error("bgm_missing", f"BGM 缺失: {ctx.paths.bgm_file}")
            if ctx.failure_policy["bgm_missing"] == "skip_bgm":
                log_line(f"⚠️ BGM 文件不存在，按策略跳过 BGM 但保留章节标签: {ctx.paths.bgm_file}", level="warning", ctx=ctx)
                render_final_with_chapter_tag(
                    final_raw,
                    final_out,
                    chapter_tag_text,
                    bgm_file=None,
                    ctx=ctx,
                )
            else:
                raise RuntimeError(f"BGM 文件不存在: {ctx.paths.bgm_file}")
        if not is_valid_media_file(final_out, expect_video=True, expect_audio=True, ctx=ctx):
            ctx.record_error("final_output", f"最终视频无效: {final_out}")
            if ctx.failure_policy["invalid_output"] == "fail_batch":
                raise RuntimeError(f"最终视频无效: {final_out}")
            log_line(f"⚠️ 最终视频无效但策略允许继续: {final_out}", level="warning", ctx=ctx)
        validate_audio_decodable(final_out, ctx=ctx, desc="最终视频音频完整解码校验")
        try:
            validate_final_consistency(ctx, grouped_tasks, valid_clips, final_out)
        except Exception as e:
            ctx.record_error("final_consistency", e)
            if ctx.failure_policy["final_consistency_failure"] == "fail_batch":
                raise
            log_line(f"⚠️ 一致性校验失败但策略允许继续: {e}", level="warning", ctx=ctx)

        stats_summary = ctx.stats
        log_line(
            f"📊 统计: tasks={ctx.total_tasks_expected} | scenes_ok={stats_summary['scenes_processed']} | groups_ok={ctx.total_groups_success}/{ctx.total_groups_expected} | groups_failed={ctx.total_groups_failed} | missing_audio={ctx.total_missing_audio} | effects={len(stats_summary['effects_used'])} | fades={stats_summary['fades_applied']} | errors={len(stats_summary['errors'])}",
            ctx=ctx,
        )
        log_line(f"✅ 任务完成: {final_out}", ctx=ctx)
        log_line(f"VIDEO_OUTPUT={final_out}", ctx=ctx)
        return final_out
    finally:
        for temp_file in [final_raw, flist]:
            if temp_file and os.path.exists(temp_file):
                try:
                    os.remove(temp_file)
                except OSError:
                    pass
        release_process_lock(lock_path)

if __name__ == "__main__":
    args = build_arg_parser().parse_args()
    try:
        result = main(args)
        sys.exit(0 if result else 1)
    except Exception as e:
        log_line(f"[shipin] error: {e}", level="error")
        sys.exit(1)
