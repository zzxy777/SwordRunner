const $ = (id) => document.getElementById(id);

const STEP_ORDER = ["chapter", "segment", "director", "voice", "tts", "post"];
const views = {
  dashboard: ["总览自检", "启动前检查项目、模型、ComfyUI、脚本和素材是否到位。"],
  settings: ["项目设置", "保存路径、输出目录、脚本和生产参数。"],
  models: ["模型与 ComfyUI", "在页面里输入 LLM API、模型名和 ComfyUI 地址，并直接测试。"],
  chapters: ["章节库", "完整章节分页、搜索和每章生产状态。"],
  run: ["生产执行", "按 WF1-WF4 步骤执行，失败后可 resume。"],
  logs: ["任务日志", "实时查看阻塞点、错误和每一步输出。"],
};

let settings = null;
let selectedJob = "";
let selectedQueue = "";
let chapterPage = 1;

async function api(url, options) {
  const response = await fetch(url, options);
  const data = await response.json();
  if (!response.ok) {
    throw new Error(data.error || response.statusText);
  }
  return data;
}

function val(id) {
  return $(id).value.trim();
}

function num(id) {
  return Number($(id).value || 0);
}

function set(id, value) {
  $(id).value = value ?? "";
}

function esc(value) {
  return String(value ?? "").replace(/[&<>"']/g, (char) => ({
    "&": "&amp;",
    "<": "&lt;",
    ">": "&gt;",
    '"': "&quot;",
    "'": "&#39;",
  }[char]));
}

function setStatus(text) {
  $("globalStatus").textContent = text;
}

function showView(name) {
  document.querySelectorAll(".navbtn").forEach((button) => {
    button.classList.toggle("active", button.dataset.view === name);
  });
  document.querySelectorAll(".view").forEach((view) => {
    view.classList.toggle("active", view.id === `view-${name}`);
  });
  $("pageTitle").textContent = views[name][0];
  $("pageDesc").textContent = views[name][1];
}

function fillSteps() {
  const options = STEP_ORDER.map((step) => `<option value="${step}">${step}</option>`).join("");
  $("fromStep").innerHTML = options;
  $("toStep").innerHTML = options;
  $("fromStep").value = "chapter";
  $("toStep").value = "voice";
}

function updateSummaryFromSettings() {
  const primary = settings?.llm?.primary || {};
  const fallback = settings?.llm?.fallback || {};
  const post = settings?.post || {};
  const comfy = settings?.comfyui || {};
  const hasLlm = Boolean(primary.secret?.configured || fallback.secret?.configured);
  $("summaryLlm").textContent = hasLlm ? "已配置" : "缺 Key";
  $("summaryComfy").textContent = comfy.base_url ? "已填写" : "未填写";
  $("summaryScripts").textContent = post.video_script && post.subtitle_script ? "已填写" : "未填写";
}

function updateSummaryFromPreflight(data) {
  $("summaryChapters").textContent = data.chapter_count || 0;
  const llmRows = data.checks.filter((row) => String(row.name).startsWith("LLM ") && row.name.includes("配置"));
  const comfy = data.checks.find((row) => row.name === "ComfyUI 连通");
  const video = data.checks.find((row) => row.name === "视频脚本");
  const subtitle = data.checks.find((row) => row.name === "字幕脚本");
  $("summaryLlm").textContent = llmRows.some((row) => row.ok) ? "已配置" : "缺 Key";
  $("summaryComfy").textContent = comfy?.ok ? "连通" : "异常";
  $("summaryScripts").textContent = video?.ok && subtitle?.ok ? "可用" : "异常";
}

async function loadSettings() {
  settings = await api("/api/settings");
  set("projectRoot", settings.project_root);

  const paths = settings.paths || {};
  set("bookPath", paths.book_path);
  set("chapterIndexPath", paths.chapter_index_path);
  set("visualPlanningDir", paths.visual_planning_dir);
  set("visualGroupsDir", paths.visual_groups_dir);
  set("ttsDir", paths.tts_dir);
  set("windowsJobsDir", paths.windows_jobs_dir);
  set("runsDir", paths.runs_dir);
  set("ffmpegBin", paths.ffmpeg_bin);
  set("scriptsDir", paths.scripts_dir);
  set("comfyTtsTemplatePath", paths.comfyui_tts_template_path);

  const post = settings.post || {};
  set("videoScript", post.video_script);
  set("subtitleScript", post.subtitle_script);

  const pipeline = settings.pipeline || {};
  set("segmentMin", pipeline.segment_min_chars);
  set("segmentTarget", pipeline.segment_target_chars);
  set("segmentMax", pipeline.segment_max_chars);
  set("visualGroupSize", pipeline.visual_group_size);
  set("ttsTail", pipeline.tts_tail_silence_sec);

  const primary = settings.llm?.primary || {};
  const fallback = settings.llm?.fallback || {};
  set("primaryBase", primary.base_url);
  set("primaryModel", primary.model);
  set("primaryEnv", primary.api_key_env);
  set("primaryTimeout", primary.timeout_s);
  $("primarySecret").textContent = primary.secret?.configured ? `已配置: ${primary.secret.masked}` : "未配置 API Key";
  $("primarySecret").classList.toggle("ok", Boolean(primary.secret?.configured));

  set("fallbackBase", fallback.base_url);
  set("fallbackModel", fallback.model);
  set("fallbackEnv", fallback.api_key_env);
  set("fallbackTimeout", fallback.timeout_s);
  $("fallbackSecret").textContent = fallback.secret?.configured ? `已配置: ${fallback.secret.masked}` : "未配置 API Key";
  $("fallbackSecret").classList.toggle("ok", Boolean(fallback.secret?.configured));

  const comfy = settings.comfyui || {};
  set("comfyBase", comfy.base_url);
  set("comfyQueue", comfy.queue_timeout_s);
  set("comfyHistory", comfy.history_timeout_s);

  updateSummaryFromSettings();
  setStatus("配置已加载");
}

function collectSettings() {
  return {
    project_root: val("projectRoot"),
    paths: {
      book_path: val("bookPath"),
      chapter_index_path: val("chapterIndexPath"),
      scripts_dir: val("scriptsDir"),
      runs_dir: val("runsDir"),
      visual_planning_dir: val("visualPlanningDir"),
      visual_groups_dir: val("visualGroupsDir"),
      tts_dir: val("ttsDir"),
      windows_jobs_dir: val("windowsJobsDir"),
      comfyui_tts_template_path: val("comfyTtsTemplatePath"),
      ffmpeg_bin: val("ffmpegBin"),
    },
    pipeline: {
      segment_min_chars: num("segmentMin"),
      segment_target_chars: num("segmentTarget"),
      segment_max_chars: num("segmentMax"),
      visual_group_size: num("visualGroupSize"),
      tts_tail_silence_sec: Number(val("ttsTail") || 0),
    },
    post: {
      video_script: val("videoScript"),
      subtitle_script: val("subtitleScript"),
      callback_url: settings?.post?.callback_url || "",
    },
    llm: {
      primary: {
        name: "deepseek-primary",
        base_url: val("primaryBase"),
        model: val("primaryModel"),
        api_key_env: val("primaryEnv"),
        timeout_s: num("primaryTimeout"),
        temperature: 0.1,
        api_key: val("primaryKey"),
      },
      fallback: {
        name: "gemini-fallback",
        base_url: val("fallbackBase"),
        model: val("fallbackModel"),
        api_key_env: val("fallbackEnv"),
        timeout_s: num("fallbackTimeout"),
        temperature: 0.1,
        api_key: val("fallbackKey"),
      },
    },
    comfyui: {
      base_url: val("comfyBase"),
      client_id_prefix: "sword_runner",
      queue_timeout_s: num("comfyQueue"),
      history_timeout_s: num("comfyHistory"),
      poll_interval_s: 3,
    },
  };
}

async function saveSettings() {
  setStatus("保存中...");
  settings = await api("/api/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(collectSettings()),
  });
  $("primaryKey").value = "";
  $("fallbackKey").value = "";
  await loadSettings();
  setStatus("设置已保存");
}

async function runPreflight(includeLlm = false) {
  $("preflightList").innerHTML = '<div class="hint">自检中...</div>';
  setStatus(includeLlm ? "完整自检中..." : "快速自检中...");
  const data = await api(`/api/preflight?quick_llm=${includeLlm ? "1" : "0"}`);
  updateSummaryFromPreflight(data);
  setStatus(data.ok ? `自检通过，警告 ${data.warnings}` : `自检失败，错误 ${data.errors}`);
  $("preflightList").innerHTML = data.checks.map((row) => {
    const klass = row.ok ? "ok" : (row.severity === "warn" ? "warn" : "fail");
    const label = row.ok ? "OK" : row.severity.toUpperCase();
    return `<div class="check-card"><span class="badge ${klass}">${label}</span><div><strong>${esc(row.name)}</strong><div class="hint">${esc(row.detail)}</div></div></div>`;
  }).join("");
}

async function testLlm(provider) {
  $("testResult").textContent = `测试 ${provider} 中...`;
  const data = await api("/api/test/llm", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ provider }),
  });
  $("testResult").textContent = `${provider} OK: ${data.model} | ${data.elapsed_s}s | ${data.preview || ""}`;
}

async function testComfy() {
  $("testResult").textContent = "测试 ComfyUI 中...";
  const data = await api("/api/test/comfyui", { method: "POST" });
  $("testResult").textContent = `ComfyUI OK: ${data.base_url} | ${data.elapsed_s}s`;
}

function chapterStatusBadge(status) {
  const klass = status.subtitle ? "ok" : (status.video || status.wav_count > 0 ? "warn" : "");
  return `<span class="badge ${klass}">${esc(status.stage)}</span><div class="hint">wav=${status.wav_count}</div>`;
}

async function loadChapters() {
  const params = new URLSearchParams({
    project_root: val("projectRoot"),
    q: val("chapterSearch"),
    page: String(chapterPage),
    page_size: String(num("pageSize") || 200),
  });
  const data = await api(`/api/chapters?${params}`);
  chapterPage = data.page;
  set("pageNo", data.page);
  $("summaryChapters").textContent = data.all_total;
  $("chapterMeta").textContent = `匹配 ${data.total} / 全书 ${data.all_total} 章，第 ${data.page}/${data.pages} 页，本页 ${data.chapters.length} 章`;
  $("chapterRows").innerHTML = data.chapters.map((chapter) => `
    <tr>
      <td>${chapter.index}</td>
      <td><strong>${esc(chapter.safe_name)}</strong><div class="hint">${esc(chapter.full_title)}</div></td>
      <td>${chapterStatusBadge(chapter.status)}</td>
      <td><button class="secondary pick-chapter" data-safe="${esc(chapter.safe_name)}">选择</button></td>
    </tr>
  `).join("");
  document.querySelectorAll(".pick-chapter").forEach((button) => {
    button.onclick = () => {
      set("chapterHint", button.dataset.safe);
      set("queueStartIndex", button.closest("tr").querySelector("td").textContent.trim());
      set("queueEndIndex", button.closest("tr").querySelector("td").textContent.trim());
      showView("run");
    };
  });
}

function runPayload() {
  const postMode = val("postMode");
  return {
    project_root: val("projectRoot"),
    mode: val("mode"),
    chapter_hint: val("chapterHint"),
    run_id: val("runId"),
    from_step: val("fromStep"),
    to_step: val("toStep"),
    dry_run: $("dryRun").checked,
    no_llm: $("noLlm").checked,
    skip_tts: $("skipTts").checked,
    force: $("force").checked,
    run_post: postMode === "direct",
    write_windows_job: postMode === "job",
    max_scenes: num("maxScenes"),
  };
}

async function startJob() {
  setStatus("任务启动中...");
  const data = await api("/api/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(runPayload()),
  });
  selectedJob = data.job.id;
  showView("logs");
  await refreshJobs();
  await loadJob(selectedJob);
  setStatus("任务已启动");
}

function queuePayload() {
  return {
    ...runPayload(),
    queue_start_index: num("queueStartIndex"),
    queue_end_index: num("queueEndIndex"),
    queue_count: num("queueCount"),
  };
}

function queueNeedsConfirm(payload) {
  return payload.to_step !== "post"
    || !payload.run_post
    || payload.dry_run
    || payload.skip_tts
    || payload.max_scenes > 0;
}

async function startQueue() {
  const payload = queuePayload();
  if (queueNeedsConfirm(payload)) {
    const ok = confirm("当前队列参数不是完整生产：可能不会跑完 TTS/视频/字幕，或只跑部分场景。仍然启动队列吗？");
    if (!ok) return;
  }
  setStatus("队列启动中...");
  const data = await api("/api/queue/start", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  selectedQueue = data.queue.id;
  selectedJob = data.queue.current_job_id || selectedJob;
  showView("logs");
  await refreshQueues();
  if (selectedQueue) {
    await loadQueue(selectedQueue);
  }
  await refreshJobs();
  setStatus("队列已启动");
}

async function refreshJobs() {
  const data = await api("/api/jobs");
  if (!selectedJob && data.jobs.length) {
    selectedJob = data.jobs[0].id;
  }
  $("jobList").innerHTML = data.jobs.map((job) => {
    const klass = job.status === "done" ? "ok" : (job.status === "failed" ? "fail" : "warn");
    const queueTag = job.queue_id ? `<div class="job-meta">队列第 ${job.queue_position} 章 · ${esc(job.queue_id)}</div>` : "";
    return `<div class="job ${job.id === selectedJob ? "active" : ""}" data-id="${esc(job.id)}">
      <span class="badge ${klass}">${esc(job.status)}</span>
      <div class="job-title">${esc(job.chapter)}</div>
      <div class="job-meta">${esc(job.id)} · ${esc(job.created_at)}</div>
      ${queueTag}
    </div>`;
  }).join("");
  document.querySelectorAll(".job").forEach((row) => {
    row.onclick = async () => {
      selectedJob = row.dataset.id;
      await refreshJobs();
      await loadJob(selectedJob);
    };
  });
}

function queueStatusClass(status) {
  if (status === "done") return "ok";
  if (status === "failed" || status === "stopped") return "fail";
  return "warn";
}

async function refreshQueues() {
  const data = await api("/api/queues");
  if (!selectedQueue && data.queues.length) {
    selectedQueue = data.queues[0].id;
  }
  $("queueList").innerHTML = data.queues.length ? data.queues.map((queue) => {
    const current = queue.current?.safe_name || "等待开始";
    return `<div class="queue-item ${queue.id === selectedQueue ? "active" : ""}" data-id="${esc(queue.id)}">
      <span class="badge ${queueStatusClass(queue.status)}">${esc(queue.status)}</span>
      <strong>${queue.completed}/${queue.total}</strong>
      <span>${esc(current)}</span>
      <small>${esc(queue.id)}</small>
    </div>`;
  }).join("") : '<div class="hint">暂无队列</div>';
  document.querySelectorAll(".queue-item").forEach((row) => {
    row.onclick = async () => {
      selectedQueue = row.dataset.id;
      await refreshQueues();
      await loadQueue(selectedQueue);
    };
  });
}

function formatQueueBox(queue) {
  const current = queue.current || {};
  const recent = (queue.logs || []).slice(-8).map((line) => `<div>${esc(line)}</div>`).join("");
  const currentText = current.safe_name
    ? `当前：第 ${queue.current_index + 1}/${queue.total} 个，${esc(current.safe_name)}`
    : "当前：等待开始";
  const jobLink = queue.current_job_id
    ? `<button class="secondary tiny-btn" id="openCurrentJobBtn" data-job="${esc(queue.current_job_id)}">打开当前章节日志</button>`
    : "";
  return `<strong>队列 ${esc(queue.status)} · ${queue.completed}/${queue.total}</strong>
    <div>${currentText}</div>
    <div>queue_id: ${esc(queue.id)}</div>
    <div>started: ${esc(queue.started_at || "-")} · finished: ${esc(queue.finished_at || "-")}</div>
    <div class="queue-log-tail">${recent || "暂无队列日志"}</div>
    ${jobLink}`;
}

async function loadQueue(id) {
  if (!id) return;
  const queue = await api(`/api/queues/${encodeURIComponent(id)}`);
  $("queueState").innerHTML = formatQueueBox(queue);
  const button = $("openCurrentJobBtn");
  if (button) {
    button.onclick = async () => {
      selectedJob = button.dataset.job;
      await refreshJobs();
      await loadJob(selectedJob);
    };
  }
  if (queue.current_job_id) {
    selectedJob = queue.current_job_id;
  }
}

function formatStateBox(job) {
  const runnerState = job.runner_state || {};
  const blocked = runnerState.blocked || {};
  const steps = runnerState.steps || {};
  const path = job.state_path
    ? `<div>state: ${esc(job.state_path)}</div>`
    : "<div>state: 等待 runner 输出</div>";
  const rows = STEP_ORDER.map((step) => `${step}:${(steps[step] || {}).status || "pending"}`).join(" · ");
  if (blocked.step) {
    return `<strong>阻塞在 ${esc(blocked.step)}</strong><div>${esc(blocked.error || "")}</div><div>${esc(rows)}</div>${path}`;
  }
  const title = job.status === "done" ? "任务完成" : `当前步骤 ${esc(runnerState.current_step || job.status)}`;
  return `<strong>${title}</strong><div>${esc(rows)}</div>${path}`;
}

async function loadJob(id) {
  if (!id) return;
  const job = await api(`/api/jobs/${encodeURIComponent(id)}`);
  $("jobState").innerHTML = formatStateBox(job);
  $("logs").textContent = (job.logs || []).join("\n") || "暂无日志";
  $("logs").scrollTop = $("logs").scrollHeight;
}

function safePreset() {
  $("fromStep").value = "chapter";
  $("toStep").value = "voice";
  $("dryRun").checked = true;
  $("noLlm").checked = true;
  $("skipTts").checked = true;
  set("maxScenes", 3);
  set("postMode", "none");
  setStatus("已切换安全测试预设");
}

function realPreset() {
  $("fromStep").value = "chapter";
  $("toStep").value = "tts";
  $("dryRun").checked = false;
  $("noLlm").checked = false;
  $("skipTts").checked = false;
  set("maxScenes", 1);
  set("postMode", "none");
  setStatus("已切换真实单场景预设");
}

function queueProductionPreset() {
  $("mode").value = "run";
  $("fromStep").value = "chapter";
  $("toStep").value = "post";
  $("dryRun").checked = false;
  $("noLlm").checked = false;
  $("skipTts").checked = false;
  $("force").checked = false;
  set("maxScenes", 0);
  set("postMode", "direct");
  set("runId", "");
  setStatus("已切换整章队列生产预设");
}

function bindEvents() {
  document.querySelectorAll(".navbtn").forEach((button) => {
    button.onclick = () => showView(button.dataset.view);
  });
  $("gotoModelsBtn").onclick = () => showView("models");
  $("gotoChaptersBtn").onclick = () => showView("chapters");
  $("gotoRunBtn").onclick = () => showView("run");

  $("saveSettingsBtn").onclick = () => saveSettings().catch((error) => alert(error.message));
  $("saveModelsBtn").onclick = () => saveSettings().catch((error) => alert(error.message));
  $("saveComfyBtn").onclick = () => saveSettings().catch((error) => alert(error.message));

  $("preflightBtn").onclick = () => runPreflight(false).catch((error) => alert(error.message));
  $("preflightBtnTop").onclick = () => runPreflight(false).catch((error) => alert(error.message));
  $("preflightLlmBtn").onclick = () => runPreflight(true).catch((error) => alert(error.message));
  $("preflightLlmBtnTop").onclick = () => runPreflight(true).catch((error) => alert(error.message));

  $("testPrimaryBtn").onclick = () => testLlm("primary").catch((error) => $("testResult").textContent = error.message);
  $("testFallbackBtn").onclick = () => testLlm("fallback").catch((error) => $("testResult").textContent = error.message);
  $("testComfyBtn").onclick = () => testComfy().catch((error) => $("testResult").textContent = error.message);

  $("loadChaptersBtn").onclick = () => {
    chapterPage = num("pageNo") || 1;
    loadChapters().catch((error) => alert(error.message));
  };
  $("loadAllChaptersBtn").onclick = () => {
    chapterPage = 1;
    set("pageNo", 1);
    set("pageSize", 5000);
    loadChapters().catch((error) => alert(error.message));
  };
  $("prevPageBtn").onclick = () => {
    chapterPage = Math.max(1, chapterPage - 1);
    loadChapters().catch((error) => alert(error.message));
  };
  $("nextPageBtn").onclick = () => {
    chapterPage += 1;
    loadChapters().catch((error) => alert(error.message));
  };
  $("chapterSearch").addEventListener("keydown", (event) => {
    if (event.key === "Enter") {
      chapterPage = 1;
      loadChapters().catch((error) => alert(error.message));
    }
  });

  $("startBtn").onclick = () => startJob().catch((error) => alert(error.message));
  $("startQueueBtn").onclick = () => startQueue().catch((error) => alert(error.message));
  $("safePresetBtn").onclick = safePreset;
  $("realPresetBtn").onclick = realPreset;
  $("queueProductionPresetBtn").onclick = queueProductionPreset;
  $("refreshQueuesBtn").onclick = () => refreshQueues().catch((error) => alert(error.message));
  $("refreshJobsBtn").onclick = () => refreshJobs().catch((error) => alert(error.message));
  $("stopQueueBtn").onclick = async () => {
    if (selectedQueue) {
      await api(`/api/queues/${encodeURIComponent(selectedQueue)}/stop`, { method: "POST" });
      await loadQueue(selectedQueue).catch(() => {});
    }
  };
  $("stopBtn").onclick = async () => {
    if (selectedJob) {
      await api(`/api/jobs/${encodeURIComponent(selectedJob)}/stop`, { method: "POST" });
      await loadJob(selectedJob).catch(() => {});
    }
  };
}

async function boot() {
  bindEvents();
  fillSteps();
  await loadSettings();
  $("chapterRows").innerHTML = '<tr><td colspan="4" class="hint">章节库加载中...</td></tr>';
  runPreflight(false).catch(() => {});
  loadChapters().catch((error) => {
    $("chapterRows").innerHTML = `<tr><td colspan="4" class="hint">${esc(error.message)}</td></tr>`;
  });
  await refreshJobs();
  await refreshQueues();
  setInterval(async () => {
    await refreshQueues().catch(() => {});
    if (selectedQueue) {
      await loadQueue(selectedQueue).catch(() => {});
    }
    await refreshJobs().catch(() => {});
    if (selectedJob) {
      await loadJob(selectedJob).catch(() => {});
    }
  }, 2500);
}

boot().catch((error) => setStatus(`初始化失败: ${error.message}`));
