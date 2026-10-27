# Sword Audiobook Studio

本项目用于逐步替换 n8n 总控的 WF1-WF4，把有声小说生产流程收口到本地可执行的前端控制台中，同时保留当前稳定的 `shipin_5.py` 视频合成和 `zimu_7_text_timed.py` 字幕合成。

## 启动方式

双击或执行：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File .\Start-SwordRunnerUI.ps1 -ProjectRoot "D:\Books\MyAudiobook"
```

默认打开：

```text
http://127.0.0.1:8765
```

## 页面能力

- `总览自检`：启动后自动检查项目根目录、`book.txt`、`chapter_index.json`、后处理脚本、备用图库、ComfyUI 和 LLM 配置。
- `项目设置`：配置小说项目路径、章节索引、输出目录、ffmpeg、本地 ComfyUI TTS 模板、视频脚本和字幕脚本。
- `模型与 ComfyUI`：输入 Primary/Fallback LLM 的 `base_url`、`model`、`API Key`，以及 ComfyUI 地址，并可直接测试连通。
- `章节库`：读取完整章节索引，支持搜索、分页和“加载全部”，不会只限制在前几十章。
- `生产执行`：按 `chapter -> segment -> director -> voice -> tts -> post` 执行，支持 dry-run、单场景真实测试、续跑和写入 windows_jobs。
- `任务日志`：实时显示日志，并读取每章 `state.json`，展示当前 WF 步骤和阻塞错误。

## 前端结构

- `web_ui.py`：本地 HTTP 服务、API、静态文件服务。
- `static/index.html`：页面结构。
- `static/app.css`：界面视觉、布局、响应式样式。
- `static/app.js`：设置保存、自检、章节库、任务执行、日志轮询。

## WF1-WF4 映射

- `WF1 取章节`：从 `book.txt` 和 `chapter_index.json` 锁定章节文本。
- `WF2 清洗分段`：调用 LLM 做章节清洗、分段、质检；安全测试时可使用规则分段。
- `WF3 导演文件`：生成 `visual_planning/*_director_v2.json` 和 `visual_groups/*_visual_groups.json`。
- `WF4 配音脚本/TTS`：生成逐场景配音脚本，并调用 ComfyUI 生成 wav。
- `Post 视频字幕`：沿用 `shipin_5.py` 和 `zimu_7_text_timed.py`。

## ComfyUI TTS 模板

- TTS 请求模板已经迁移到 `templates/comfyui_tts_prompt.json`。
- 运行时不再读取旧的 n8n workflow 导出 JSON。
- 模板里只有 `__FINAL_SCRIPT__` 是动态占位符，runner 会在每个场景执行时替换成当前配音脚本。

## 本地配置文件

- 普通配置保存到 `config/user_settings.json`。
- API Key 保存到 `config/.env`，页面只显示脱敏状态，不回显明文。
- 默认配置在 `config/default.json`，换小说项目时优先在页面里改项目根目录和索引路径。

## 生产前检查

真实跑整章前建议先按这个顺序：

1. 在 `模型与 ComfyUI` 填好 LLM API、模型名和 ComfyUI 地址。
2. 在 `总览自检` 点 `完整自检含 LLM`。
3. 在 `章节库` 搜索目标章节并点 `选择`。
4. 在 `生产执行` 先用 `真实单场景预设` 跑 1 个场景。
5. 单场景通过后，把 `最多场景` 改成 `0` 再跑整章。

## 当前边界

这已经不是单纯命令壳，但仍处在本地生产系统的早期版本：视频和字幕链路已复用稳定脚本；WF1-WF4 已合并到 runner；后续还需要继续把提示词、重试策略、批量章节队列、失败重试按钮、TTS 完整性校验做得更细。
