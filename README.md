# Sword Audiobook Studio

Sword Audiobook Studio is a local Python production console for audiobook chapters. It replaces the n8n WF1-WF4 orchestration path while retaining the chapter pipeline, local ComfyUI TTS integration, and the existing video and subtitle post-processing scripts.

## Features

- Local web UI for project settings, chapter browsing, preflight checks, single-chapter runs, queues, and logs.
- Chapter pipeline: `chapter -> segment -> director -> voice -> tts -> post`.
- LLM-backed segmentation and voice-script generation with configurable primary and fallback providers.
- ComfyUI TTS workflow template with `__FINAL_SCRIPT__` as the runtime text placeholder.
- Existing `shipin_5.py` and `zimu_7_text_timed.py` scripts for video and subtitle rendering.

## Requirements

- Windows 10/11 and Python 3.10 or newer.
- `ffmpeg` and `ffprobe` available on `PATH`.
- A novel project folder containing `book.txt` and `chapter_index.json`.
- A reachable ComfyUI server and, for LLM-backed steps, OpenAI-compatible LLM endpoints.

## Start

Double-click `双击启动-SwordRunnerUI.bat`, then enter the novel project folder when prompted. Or run:

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-SwordRunnerUI.ps1 -ProjectRoot "D:\Books\MyAudiobook"
```

The UI is served at `http://127.0.0.1:8765`.

The project folder should contain at least:

```text
MyAudiobook/
  book.txt
  chapter_index.json
```

In the UI, configure LLM endpoints and keys, ComfyUI address, and any project-specific paths before production. API keys are stored locally in `sword_runner/config/.env`; that file is ignored by Git.

## Repository Layout

- `sword_runner/web_ui.py`: local HTTP server and UI APIs.
- `sword_runner/runner.py`: single-chapter pipeline executor.
- `sword_runner/pipeline/`: chapter loading, segmentation, director metadata, LLM, TTS, and post-processing logic.
- `sword_runner/static/`: browser UI.
- `sword_runner/prompts/`: WF2 and WF4 prompts.
- `sword_runner/templates/`: ComfyUI TTS workflow template.
- `shipin_5.py`, `zimu_7_text_timed.py`: retained video and subtitle scripts.

## Local Data and Privacy

Novel manuscripts, chapter indexes, user settings, API keys, logs, generated audio/video, and other production data are intentionally not included. Keep them in a separate project folder. Do not commit credentials or private workflow exports.

The repository currently has no license file; public visibility alone does not grant reuse rights.
