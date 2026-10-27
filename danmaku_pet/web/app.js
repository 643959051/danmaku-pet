/* global window, document, fetch, WebSocket, Image, performance, navigator */

const $ = (id) => document.getElementById(id);
const state = {
  ws: null,
  snapshot: null,
  manifest: null,
  frames: new Map(),
  currentFrames: [],
  currentFps: 24,
  actionStarted: 0,
  holdMs: 0,
  transform: "none",
  mode: "idle",
  speaking: false,
  lastImage: null,
  entries: new Map(),
  lastPlan: null,
  activeEventId: null,
  toastTimer: 0,
  reconnectTimer: 0,
  simRunning: false,
  clockTimer: 0,
  settingsDirty: false,
  personaDirty: false,
  autoReplyBusy: false,
  readAloud: false,
};

const SOURCE_LABEL = {
  manual: "手动",
  danmaku_sim: "弹幕",
  gift_sim: "礼物",
};

const STATUS_LABEL = {
  queued: "排队中",
  processing: "处理中",
  completed: "完成",
  interrupted: "已打断",
  error: "错误",
};

function fmtTime(ts) {
  if (!ts) return "--:--:--";
  const d = new Date(ts * 1000);
  return d.toLocaleTimeString("zh-CN", { hour12: false });
}

function fmtMs(ms) {
  if (!ms) return "—";
  return `${(ms / 1000).toFixed(1)}s`;
}

function setChip(id, value, kind = "") {
  const el = $(id);
  if (!el) return;
  el.classList.remove("ok", "warn", "err");
  if (kind) el.classList.add(kind);
  const b = el.querySelector("b");
  if (b) b.textContent = value;
}

function syncSettingsFields(cfg) {
  if (!cfg) return;
  const modal = $("settingsPanel");
  if (modal?.classList.contains("open") && state.settingsDirty) return;
  $("settingsMode").value = cfg.llm_requested_mode || cfg.llm_mode || "auto";
  $("settingsModel").value = cfg.deepseek_model || "deepseek-chat";
  $("settingsBaseUrl").value = cfg.deepseek_base_url || "https://api.deepseek.com";
  $("settingsKey").placeholder = cfg.llm_key_configured
    ? "已配置 Key，留空保持当前值"
    : "sk-...（仅保存在工作区 .env）";
  syncPersonaFields(cfg.persona);
}

function syncPersonaFields(persona) {
  if (!persona) return;
  const modal = $("settingsPanel");
  if (modal?.classList.contains("open") && state.personaDirty) return;
  $("personaName").value = persona.name || "小鱼";
  $("personaIdentity").value = persona.identity || "";
  $("personaTone").value = persona.tone || "";
  $("personaCatchphrase").value = persona.catchphrase || "";
  $("personaLength").value = persona.reply_length || "short";
  $("personaEmoji").checked = Boolean(persona.emoji);
  $("personaRules").value = persona.rules || "";
}

function toast(message, level = "info") {
  const el = $("toast");
  el.textContent = message;
  el.className = "toast on" + (level === "warning" ? " warn" : level === "error" ? " err" : "");
  clearTimeout(state.toastTimer);
  state.toastTimer = window.setTimeout(() => {
    el.className = "toast";
  }, 4200);
}

function setBubble(user, text) {
  $("bubbleUser").textContent = user || "小鱼";
  $("bubbleText").textContent = text || "…";
  const bubble = $("speechBubble");
  bubble.classList.remove("pop");
  void bubble.offsetWidth;
  bubble.classList.add("pop");
}

function setSpeaking(on, label = "正在说话") {
  state.speaking = on;
  $("speakingPill").classList.toggle("on", on);
  $("speakingLabel").textContent = label;
}

function setBadges(plan, source) {
  if (!plan) return;
  $("badgeEmotion").textContent = `情绪 · ${plan.emotion}`;
  $("badgeMotion").textContent = `动作 · ${plan.motion}`;
  $("badgePriority").textContent = `优先级 · ${plan.priority}`;
  $("badgePriority").classList.toggle("high", plan.priority === "high");
  $("badgeSource").textContent = `决策 · ${source || "—"}`;
}

function updatePlan(plan, source, warnings = []) {
  if (!plan) return;
  state.lastPlan = plan;
  $("planJson").textContent = JSON.stringify(plan, null, 2);
  $("decisionSource").textContent = source || "waiting";
  if (warnings && warnings.length) {
    $("warningLine").textContent = warnings.join(" · ");
  } else {
    $("warningLine").textContent = "严格 JSON · 非法动作自动回退 idle / nod";
  }
  setBadges(plan, source);
}

function applyStatus(data) {
  const cfg = state.snapshot?.config;
  if (data?.queue) {
    setChip("chipQueue", String(data.queue.pending), data.queue.pending > 0 ? "ok" : "");
  }
  if (Object.prototype.hasOwnProperty.call(data || {}, "last_danmaku")) {
    $("replyLastBtn").disabled = !data.last_danmaku;
  }
  const auto = Object.prototype.hasOwnProperty.call(data || {}, "auto_reply")
    ? data.auto_reply
    : cfg?.auto_reply;
  if (typeof auto === "boolean" && !state.autoReplyBusy) {
    renderAutoReply(auto);
  }
  if (Array.isArray(data?.pending)) {
    renderPending(data.pending);
  }
  if (cfg) {
    const llm = cfg.llm_mode === "deepseek" ? "DeepSeek" : "本地模拟";
    setChip("chipLlm", llm, cfg.llm_mode === "deepseek" ? "ok" : "warn");
    setChip("chipTts", "EdgeTTS", "ok");
    syncSettingsFields(cfg);
  }
  const pet = data?.backend?.desktop_pet;
  if (pet) {
    const labels = {
      ready: "已就绪",
      starting: "启动中",
      idle: "待启动",
      disabled: "已关闭",
      missing: "缺少文件",
      error: "异常",
      stopped: "已停止",
    };
    setChip(
      "chipPet",
      labels[pet.state] || pet.state,
      pet.state === "ready" ? "ok" : pet.state === "error" || pet.state === "missing" ? "err" : "warn",
    );
  }
  if (data?.stats) {
    $("metricDone").textContent = String(data.stats.completed ?? 0);
    $("metricLatency").textContent =
      data.stats.avg_latency_ms != null ? `${data.stats.avg_latency_ms}ms` : "—";
    $("metricSpeech").textContent =
      data.stats.avg_speech_ms != null ? `${(data.stats.avg_speech_ms / 1000).toFixed(1)}s` : "—";
    $("metricDropped").textContent = String(data.stats.dropped ?? 0);
  }
}

function renderSimulator(sim) {
  if (!sim) return;
  state.simRunning = Boolean(sim.running);
  $("simState").textContent = sim.running
    ? `运行中 · 每 ${sim.interval_seconds}s · 已发 ${sim.sent}`
    : "已停止";
  $("simState").classList.toggle("on", Boolean(sim.running));
  $("simToggle").textContent = sim.running ? "停止循环" : "开始循环";
  if (sim.running) {
    $("simInterval").value = String(sim.interval_seconds);
    $("simScenario").value = sim.scenario;
  }
}

function renderAutoReply(enabled) {
  $("autoReplyToggle").checked = Boolean(enabled);
  $("autoReplyState").textContent = enabled ? "开启" : "关闭";
  $("autoReplyHint").textContent = enabled
    ? "开启：新弹幕会先被念出来，再执行回复"
    : "关闭：新弹幕进入待回复列表，不自动说话";
  $("autoReplyToggle").closest(".auto-reply-row")?.classList.toggle("on", Boolean(enabled));
}

function renderPending(items) {
  const list = $("pendingList");
  const safeItems = Array.isArray(items) ? items : [];
  $("pendingCount").textContent = `${safeItems.length} 条`;
  if (!safeItems.length) {
    list.innerHTML = '<div class="pending-empty">暂无。关闭自动回复后，模拟弹幕会出现在这里。</div>';
    return;
  }
  list.replaceChildren();
  for (const item of safeItems) {
    const event = item.event || {};
    const row = document.createElement("div");
    row.className = "pending-item";
    const main = document.createElement("div");
    main.className = "pending-main";
    const user = document.createElement("div");
    user.className = "pending-user";
    user.textContent = `@${event.user || "观众"}`;
    const text = document.createElement("div");
    text.className = "pending-text";
    text.textContent = event.text || "";
    main.append(user, text);
    const time = document.createElement("span");
    time.className = "pending-time";
    time.textContent = item.waiting_seconds != null ? `${item.waiting_seconds}s` : "";
    const button = document.createElement("button");
    button.type = "button";
    button.className = "link";
    button.dataset.pendingReply = event.event_id || "";
    button.textContent = "回复";
    row.append(main, time, button);
    list.append(row);
  }
}

function sourceLabel(source) {
  return SOURCE_LABEL[source] || source || "event";
}

function logStatusClass(status) {
  if (status === "completed") return "ok";
  if (status === "error") return "err";
  if (status === "interrupted") return "interrupted";
  return "warn";
}

function renderLog() {
  const list = $("logList");
  const entries = [...state.entries.values()]
    .sort((a, b) => (b.started_at || 0) - (a.started_at || 0))
    .slice(0, 20);
  $("logCount").textContent = `${state.entries.size} 条`;
  if (!entries.length) {
    list.innerHTML = '<div class="empty-log">还没有事件。发送一条弹幕，或者启动模拟循环。</div>';
    return;
  }
  list.replaceChildren();
  for (const entry of entries) {
    const event = entry.event || {};
    const card = document.createElement("div");
    card.className = "log-entry";

    const badge = document.createElement("span");
    badge.className = `log-source ${event.source === "gift_sim" ? "gift" : event.source === "manual" ? "manual" : ""}`;
    badge.textContent = sourceLabel(event.source);

    const main = document.createElement("div");
    main.className = "log-main";
    const line = document.createElement("div");
    line.className = "log-event";
    const who = document.createElement("b");
    who.textContent = `@${event.user || "观众"}`;
    line.append(who, document.createTextNode(` ${event.text || ""}`));
    const reply = document.createElement("div");
    reply.className = "log-reply";
    reply.textContent = entry.plan?.reply ? `↳ ${entry.plan.reply}` : "等待决策…";
    main.append(line, reply);

    const meta = document.createElement("div");
    meta.className = "log-meta";
    const time = document.createElement("span");
    time.textContent = fmtTime(entry.started_at);
    const status = document.createElement("span");
    status.className = logStatusClass(entry.status);
    status.textContent = STATUS_LABEL[entry.status] || entry.status;
    const extra = document.createElement("span");
    const parts = [];
    if (entry.plan) parts.push(`${entry.plan.motion}/${entry.plan.emotion}`);
    if (entry.decision_source) parts.push(entry.decision_source);
    extra.textContent = parts.join(" · ");
    meta.append(time, status, extra);
    if (event.event_id && event.source !== "gift_sim") {
      const replyButton = document.createElement("button");
      replyButton.type = "button";
      replyButton.className = "link";
      replyButton.dataset.replyEvent = event.event_id;
      replyButton.textContent = "回复这条";
      meta.append(replyButton);
    }

    card.append(badge, main, meta);
    list.append(card);
  }
}

function ingestRecent(recent) {
  if (!Array.isArray(recent)) return;
  for (const entry of recent) {
    if (entry?.event?.event_id) state.entries.set(entry.event.event_id, entry);
  }
  renderLog();
}

function renderSnapshot(snapshot) {
  if (!snapshot) return;
  state.snapshot = snapshot;
  updatePlan(
    snapshot.orchestrator?.current?.plan || snapshot.orchestrator?.recent?.[0]?.plan,
    snapshot.orchestrator?.recent?.[0]?.decision_source || "—",
  );
  applyStatus({
    queue: snapshot.orchestrator?.queue,
    stats: snapshot.orchestrator?.stats,
    backend: snapshot.backend || snapshot.orchestrator?.backend,
    last_danmaku: snapshot.orchestrator?.last_danmaku,
    pending: snapshot.orchestrator?.pending,
    auto_reply: snapshot.orchestrator?.auto_reply ?? snapshot.config?.auto_reply,
  });
  renderSimulator(snapshot.simulator);
  ingestRecent(snapshot.orchestrator?.recent);
  const warning = snapshot.startup_warning;
  if (warning) toast(warning, "warning");
  if (!state.currentFrames.length) startIdle();
}

function handleMessage(message) {
  const { type, data } = message || {};
  if (type === "snapshot") {
    renderSnapshot(data);
    return;
  }
  if (type === "event") {
    return;
  }
  if (type === "plan") {
    state.readAloud = Boolean(data.read_aloud);
    updatePlan(data.plan, data.decision_source, data.warnings);
    return;
  }
  if (type === "entry") {
    if (data?.entry?.event?.event_id) {
      state.entries.set(data.entry.event.event_id, data.entry);
      renderLog();
      if (data.entry.plan) {
        updatePlan(data.entry.plan, data.entry.decision_source);
      }
    }
    return;
  }
  if (type === "visual") {
    state.lastPlan = data.plan;
    state.activeEventId = data.event_id;
    updatePlan(data.plan, "visual");
    setBubble(`@${data.user || "观众"}`, data.reply);
    startPerformance(data.cue);
    $("stageHint").classList.add("hide");
    return;
  }
  if (type === "performance") {
    if (data.event_id && data.event_id !== state.activeEventId) {
      return;
    }
    if (data.phase === "start") {
      const label = state.readAloud
        ? "念弹幕 + 回复"
        : data.mode === "pulse"
          ? "动作 + 说话"
          : "正在说话";
      setSpeaking(true, label);
    } else if (data.phase === "end") {
      setSpeaking(false);
      state.activeEventId = null;
      window.setTimeout(startIdle, data.reason === "interrupted" ? 40 : 220);
    }
    return;
  }
  if (type === "status") {
    applyStatus(data);
    return;
  }
  if (type === "simulator") {
    renderSimulator(data);
    return;
  }
  if (type === "pending") {
    renderPending(data.items);
    if (typeof data.auto_reply === "boolean") renderAutoReply(data.auto_reply);
    return;
  }
  if (type === "auto_reply") {
    renderAutoReply(Boolean(data.enabled));
    return;
  }
  if (type === "settings") {
    if (state.snapshot) state.snapshot.config = data;
    applyStatus({});
    if (!$("settingsPanel").classList.contains("open")) {
      toast(`LLM 设置已更新：${data.llm_mode === "deepseek" ? "DeepSeek" : "本地模拟"}`);
    }
    return;
  }
  if (type === "persona") {
    if (state.snapshot) state.snapshot.config.persona = data;
    syncPersonaFields(data);
    if (!$("settingsPanel").classList.contains("open")) {
      toast(`角色语气已更新：${data.name}`);
    }
    return;
  }
  if (type === "dropped") {
    toast(`丢弃旧消息：${data.reason}`, "warning");
    return;
  }
  if (type === "notice") {
    toast(data.message, data.level || "info");
  }
}

async function api(path, body) {
  const response = await fetch(path, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
  });
  let payload = null;
  try {
    payload = await response.json();
  } catch {
    payload = null;
  }
  if (!response.ok) {
    const detail = payload?.detail || `HTTP ${response.status}`;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return payload;
}

async function sendManual(event) {
  event.preventDefault();
  const text = $("textInput").value.trim();
  if (!text) {
    $("textInput").focus();
    return;
  }
  const button = $("sendBtn");
  button.disabled = true;
  try {
    await api("/api/events/manual", {
      text,
      user: $("userInput").value.trim() || "观众",
    });
    $("textInput").value = "";
    $("stageHint").classList.add("hide");
    toast("已进入队列，等待小鱼回应…");
  } catch (error) {
    toast(`发送失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
    $("textInput").focus();
  }
}

async function sendGift(name, count) {
  try {
    await api("/api/events/gift", {
      gift_name: name,
      gift_count: count,
      user: $("userInput").value.trim() || "礼物观众",
    });
    $("stageHint").classList.add("hide");
    toast(`礼物 ${name} 已发送，将打断当前表演`);
  } catch (error) {
    toast(`礼物发送失败：${error.message}`, "error");
  }
}

async function replyTo(eventId = null) {
  try {
    const data = await api("/api/chat/reply", eventId ? { event_id: eventId } : {});
    $("stageHint").classList.add("hide");
    toast(`已让小鱼回复 @${data.event.user}`);
  } catch (error) {
    toast(`主动回复失败：${error.message}`, "error");
  }
}

function openSettings() {
  const cfg = state.snapshot?.config || {};
  $("settingsPanel").classList.add("open");
  $("settingsPanel").setAttribute("aria-hidden", "false");
  $("settingsKey").value = "";
  $("settingsKey").disabled = false;
  $("settingsClearKey").checked = false;
  $("settingsFeedback").textContent = "";
  $("settingsFeedback").className = "settings-feedback";
  $("personaFeedback").textContent = "";
  $("personaFeedback").className = "settings-feedback";
  state.settingsDirty = false;
  state.personaDirty = false;
  switchSettingsTab("llm");
  syncSettingsFields(cfg);
  window.setTimeout(() => $("settingsKey").focus(), 30);
}

function closeSettings() {
  $("settingsPanel").classList.remove("open");
  $("settingsPanel").setAttribute("aria-hidden", "true");
  $("settingsKey").value = "";
  $("settingsKey").disabled = false;
  $("settingsClearKey").checked = false;
  state.settingsDirty = false;
  state.personaDirty = false;
}

function switchSettingsTab(name) {
  const target = name === "persona" ? "persona" : "llm";
  for (const button of document.querySelectorAll("[data-settings-tab]")) {
    button.classList.toggle("active", button.dataset.settingsTab === target);
  }
  $("settingsTabLlm").classList.toggle("hidden", target !== "llm");
  $("settingsTabPersona").classList.toggle("hidden", target !== "persona");
}

async function saveSettings(event) {
  event.preventDefault();
  const mode = $("settingsMode").value;
  const key = $("settingsKey").value.trim();
  const clearKey = $("settingsClearKey").checked;
  const model = $("settingsModel").value.trim();
  const baseUrl = $("settingsBaseUrl").value.trim();
  const feedback = $("settingsFeedback");
  const button = $("settingsSave");
  button.disabled = true;
  feedback.className = "settings-feedback";
  feedback.textContent = "正在保存并应用…";
  try {
    const data = await api("/api/settings/llm", {
      mode,
      api_key: key || null,
      model: model || null,
      base_url: baseUrl || null,
      clear_key: clearKey,
    });
    if (state.snapshot) state.snapshot.config = data.config;
    state.settingsDirty = false;
    applyStatus({});
    feedback.className = "settings-feedback ok";
    feedback.textContent = `已应用：${data.config.llm_mode} / ${data.config.model}`;
    toast(`设置已保存：${data.config.llm_mode === "deepseek" ? "DeepSeek" : "本地模拟"}`);
    window.setTimeout(closeSettings, 700);
  } catch (error) {
    feedback.className = "settings-feedback err";
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function savePersona(event) {
  event.preventDefault();
  const payload = {
    name: $("personaName").value.trim() || "小鱼",
    identity: $("personaIdentity").value.trim(),
    tone: $("personaTone").value.trim(),
    catchphrase: $("personaCatchphrase").value.trim(),
    reply_length: $("personaLength").value,
    emoji: $("personaEmoji").checked,
    rules: $("personaRules").value.trim(),
  };
  const feedback = $("personaFeedback");
  const button = $("personaSave");
  button.disabled = true;
  feedback.className = "settings-feedback";
  feedback.textContent = "正在保存角色语气…";
  try {
    const data = await api("/api/settings/persona", payload);
    if (state.snapshot) state.snapshot.config.persona = data.persona;
    state.personaDirty = false;
    syncPersonaFields(data.persona);
    feedback.className = "settings-feedback ok";
    feedback.textContent = `已应用：${data.persona.name} · ${data.persona.tone}`;
    toast(`角色语气已更新：${data.persona.name}`);
  } catch (error) {
    feedback.className = "settings-feedback err";
    feedback.textContent = error.message;
  } finally {
    button.disabled = false;
  }
}

async function toggleSimulator() {
  const button = $("simToggle");
  button.disabled = true;
  try {
    if (state.simRunning) {
      const data = await api("/api/simulator/stop", {});
      renderSimulator(data.simulator);
    } else {
      const data = await api("/api/simulator/start", {
        interval_seconds: Number($("simInterval").value) || 4,
        scenario: $("simScenario").value,
      });
      renderSimulator(data.simulator);
    }
  } catch (error) {
    toast(`模拟器操作失败：${error.message}`, "error");
  } finally {
    button.disabled = false;
  }
}

async function toggleAutoReply() {
  const toggle = $("autoReplyToggle");
  const enabled = toggle.checked;
  state.autoReplyBusy = true;
  toggle.disabled = true;
  try {
    const data = await api("/api/settings/auto-reply", { enabled });
    if (state.snapshot) state.snapshot.config.auto_reply = data.enabled;
    renderAutoReply(data.enabled);
    applyStatus({ auto_reply: data.enabled });
    toast(
      data.enabled
        ? "自动回复已开启：新弹幕会先念出来再回复"
        : "自动回复已关闭：新弹幕进入待回复列表",
    );
  } catch (error) {
    toggle.checked = !enabled;
    renderAutoReply(!enabled);
    toast(`自动回复设置失败：${error.message}`, "error");
  } finally {
    state.autoReplyBusy = false;
    toggle.disabled = false;
  }
}

function connectWs() {
  const protocol = location.protocol === "https:" ? "wss" : "ws";
  const ws = new WebSocket(`${protocol}://${location.host}/ws`);
  state.ws = ws;
  ws.onopen = () => {
    setChip("chipLlm", state.snapshot?.config?.llm_mode === "deepseek" ? "DeepSeek" : "本地模拟",
      state.snapshot?.config?.llm_mode === "deepseek" ? "ok" : "warn");
  };
  ws.onmessage = (event) => {
    try {
      handleMessage(JSON.parse(event.data));
    } catch (error) {
      console.warn("bad ws message", error);
    }
  };
  ws.onclose = () => {
    clearTimeout(state.reconnectTimer);
    state.reconnectTimer = window.setTimeout(connectWs, 1500);
  };
}

/* ---------------------------------------------------------------- visualizer */

function frameUrls(clip, start, end, step = 1) {
  const definition = state.manifest?.clips?.[clip];
  if (!definition) return [];
  const frames = definition.frames || [];
  const from = Math.max(0, start || 0);
  const to = Math.min(frames.length, end > 0 ? end : frames.length);
  const urls = [];
  for (let index = from; index < to; index += step) {
    urls.push(`/assets/pet/${frames[index]}`);
  }
  return urls;
}

function ensureImages(urls) {
  for (const url of urls) {
    if (state.frames.has(url)) continue;
    const image = new Image();
    image.decoding = "async";
    image.src = url;
    state.frames.set(url, image);
  }
}

function startIdle() {
  if (!state.manifest) return;
  const urls = frameUrls("idle", 0, 241, 2);
  ensureImages(urls);
  state.currentFrames = urls;
  state.currentFps = 12;
  state.actionStarted = performance.now();
  state.holdMs = Number.POSITIVE_INFINITY;
  state.transform = "none";
  state.mode = "idle";
  state.lastImage = null;
  setSpeaking(false);
}

function startPerformance(cue) {
  if (!cue || !state.manifest) return;
  const urls = frameUrls(
    cue.browser_clip,
    cue.browser_start,
    cue.browser_end,
    cue.browser_loop ? 1 : 1,
  );
  ensureImages(urls);
  if (!urls.length) {
    startIdle();
    return;
  }
  state.currentFrames = urls;
  state.currentFps = cue.browser_fps || 24;
  state.actionStarted = performance.now();
  state.holdMs = cue.hold_ms || 2200;
  state.transform = cue.transform || "none";
  state.mode = cue.mode || "pulse";
  state.lastImage = null;
  setSpeaking(true, cue.mode === "pulse" ? "动作 + 说话" : "正在说话");
}

function drawPlaceholder(ctx, width, height) {
  ctx.save();
  ctx.textAlign = "center";
  ctx.fillStyle = "rgba(109, 231, 255, 0.2)";
  ctx.font = "600 12px 'Microsoft YaHei UI', sans-serif";
  ctx.fillText("DAFEIYU VISUAL", width / 2, height / 2 - 12);
  ctx.fillStyle = "rgba(237, 243, 255, 0.12)";
  ctx.font = "600 54px 'Microsoft YaHei UI', sans-serif";
  ctx.fillText("鱼", width / 2, height / 2 + 30);
  ctx.restore();
}

function animate(now) {
  const canvas = $("petCanvas");
  const rect = canvas.getBoundingClientRect();
  const dpr = Math.min(2, window.devicePixelRatio || 1);
  const width = Math.max(1, Math.round(rect.width));
  const height = Math.max(1, Math.round(rect.height));
  if (canvas.width !== width * dpr || canvas.height !== height * dpr) {
    canvas.width = width * dpr;
    canvas.height = height * dpr;
  }
  const ctx = canvas.getContext("2d");
  if (!ctx) {
    requestAnimationFrame(animate);
    return;
  }
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, width, height);

  const floorY = height - 26;
  const shadow = ctx.createRadialGradient(width / 2, floorY, 8, width / 2, floorY, 150);
  shadow.addColorStop(0, "rgba(70, 120, 200, 0.26)");
  shadow.addColorStop(1, "rgba(70, 120, 200, 0)");
  ctx.fillStyle = shadow;
  ctx.beginPath();
  ctx.ellipse(width / 2, floorY, 150, 22, 0, 0, Math.PI * 2);
  ctx.fill();

  const elapsed = now - state.actionStarted;
  const frames = state.currentFrames;
  if (!frames.length) {
    drawPlaceholder(ctx, width, height);
    requestAnimationFrame(animate);
    return;
  }
  let index = Math.floor((elapsed / 1000) * state.currentFps);
  if (state.mode === "idle") {
    index %= frames.length;
  } else {
    index = Math.min(frames.length - 1, Math.max(0, index));
  }
  const image = state.frames.get(frames[index]);
  if (!image || !image.complete || !image.naturalWidth) {
    if (state.lastImage) {
      drawSprite(ctx, state.lastImage, width, height, elapsed);
    } else {
      drawPlaceholder(ctx, width, height);
    }
    requestAnimationFrame(animate);
    return;
  }
  state.lastImage = image;
  drawSprite(ctx, image, width, height, elapsed);
  requestAnimationFrame(animate);
}

function drawSprite(ctx, image, width, height, elapsed) {
  const base = Math.min(width / 500, height / 400);
  const scale = base * 0.94;
  const imageWidth = 412 * scale;
  const imageHeight = 344 * scale;
  const centerX = width / 2;
  const centerY = height - 22 - imageHeight / 2;
  const t = elapsed / 1000;
  const progress = Number.isFinite(state.holdMs) && state.holdMs > 0
    ? Math.min(1, elapsed / Math.max(400, state.holdMs))
    : 0;

  let offsetX = 0;
  let offsetY = Math.sin(t * 1.8) * 2.4;
  let rotation = 0;
  let scaleX = 1;
  let scaleY = 1;

  switch (state.transform) {
    case "bob":
      offsetY += Math.sin(t * 6.2) * 4;
      break;
    case "sway":
      rotation = Math.sin(t * 2.4) * 0.035;
      offsetY += Math.sin(t * 2.1) * 2;
      break;
    case "bounce":
      offsetY -= Math.abs(Math.sin(t * 5.2)) * 30;
      scaleY = 1 + Math.sin(t * 10.4) * 0.012;
      break;
    case "spin":
      // Yaw-style turn: squash horizontally, then mirror, instead of a
      // screen-plane cartwheel. This reads as the character turning around.
      scaleX = Math.cos((elapsed % 1400) / 1400 * Math.PI * 2);
      if (Math.abs(scaleX) < 0.06) scaleX = scaleX >= 0 ? 0.06 : -0.06;
      offsetY -= Math.sin(progress * Math.PI) * 8;
      break;
    case "shake":
      offsetX = Math.sin(t * 34) * 7;
      rotation = Math.sin(t * 18) * 0.02;
      break;
    case "tilt":
      rotation = Math.sin(t * 1.8) * 0.07 + 0.03;
      offsetY += 3;
      break;
    case "sink":
      offsetY += 7 + Math.sin(t * 1.2) * 1.5;
      rotation = -0.025;
      break;
    case "pop":
      offsetY -= Math.sin(progress * Math.PI * 2) * 12;
      scaleX = 1 + Math.sin(t * 18) * 0.015;
      scaleY = 1 + Math.sin(t * 18 + 1) * 0.02;
      break;
    default:
      break;
  }

  ctx.save();
  ctx.translate(centerX + offsetX, centerY + offsetY);
  ctx.rotate(rotation);
  ctx.scale(scaleX, scaleY);
  ctx.drawImage(image, -imageWidth / 2, -imageHeight / 2, imageWidth, imageHeight);
  ctx.restore();
}

/* -------------------------------------------------------------------- boot */

async function loadManifest() {
  try {
    const response = await fetch("/assets/pet-manifest.json");
    if (!response.ok) throw new Error(`HTTP ${response.status}`);
    state.manifest = await response.json();
    startIdle();
  } catch (error) {
    console.warn("manifest load failed", error);
    drawPlaceholder($("petCanvas").getContext("2d"), 560, 430);
  }
}

function tickClock() {
  $("clock").textContent = new Date().toLocaleString("zh-CN", {
    month: "2-digit",
    day: "2-digit",
    hour: "2-digit",
    minute: "2-digit",
    second: "2-digit",
    hour12: false,
  });
}

function bind() {
  $("manualForm").addEventListener("submit", sendManual);
  $("textInput").addEventListener("keydown", (event) => {
    if ((event.ctrlKey || event.metaKey) && event.key === "Enter") {
      event.preventDefault();
      $("manualForm").requestSubmit();
    }
  });
  $("giftGrid").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-gift]");
    if (!button) return;
    sendGift(button.dataset.gift, Number(button.dataset.count) || 1);
  });
  $("simToggle").addEventListener("click", toggleSimulator);
  $("autoReplyToggle").addEventListener("change", toggleAutoReply);
  $("pendingList").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-pending-reply]");
    if (!button || !button.dataset.pendingReply) return;
    replyTo(button.dataset.pendingReply);
  });
  $("replyLastBtn").addEventListener("click", () => replyTo());
  $("logList").addEventListener("click", (event) => {
    const button = event.target.closest("button[data-reply-event]");
    if (!button) return;
    replyTo(button.dataset.replyEvent);
  });
  $("interruptBtn").addEventListener("click", async () => {
    try {
      await api("/api/interrupt", {});
      toast("已请求打断当前表演");
    } catch (error) {
      toast(`打断失败：${error.message}`, "error");
    }
  });
  $("settingsBtn").addEventListener("click", openSettings);
  $("settingsClose").addEventListener("click", closeSettings);
  $("settingsCancel").addEventListener("click", closeSettings);
  $("settingsForm").addEventListener("submit", saveSettings);
  $("personaCancel").addEventListener("click", closeSettings);
  $("personaForm").addEventListener("submit", savePersona);
  for (const button of document.querySelectorAll("[data-settings-tab]")) {
    button.addEventListener("click", () => switchSettingsTab(button.dataset.settingsTab));
  }
  $("settingsPanel").addEventListener("click", (event) => {
    if (event.target === $("settingsPanel")) closeSettings();
  });
  document.addEventListener("keydown", (event) => {
    if (event.key === "Escape" && $("settingsPanel").classList.contains("open")) {
      closeSettings();
    }
  });
  for (const id of ["settingsMode", "settingsModel", "settingsBaseUrl", "settingsKey"]) {
    $(id).addEventListener("input", () => {
      state.settingsDirty = true;
    });
    $(id).addEventListener("change", () => {
      state.settingsDirty = true;
    });
  }
  for (const id of [
    "personaName",
    "personaIdentity",
    "personaTone",
    "personaCatchphrase",
    "personaLength",
    "personaEmoji",
    "personaRules",
  ]) {
    $(id).addEventListener("input", () => {
      state.personaDirty = true;
    });
    $(id).addEventListener("change", () => {
      state.personaDirty = true;
    });
  }
  $("settingsClearKey").addEventListener("change", () => {
    state.settingsDirty = true;
    const clear = $("settingsClearKey").checked;
    $("settingsKey").disabled = clear;
    if (clear) $("settingsKey").value = "";
    $("settingsKey").placeholder = clear
      ? "保存后将清除当前 Key"
      : state.snapshot?.config?.llm_key_configured
        ? "已配置 Key，留空保持当前值"
        : "sk-...（仅保存在工作区 .env）";
  });
  $("clearContext").addEventListener("click", async () => {
    try {
      await api("/api/context/clear", {});
      state.entries.clear();
      renderLog();
      toast("上下文已清空");
    } catch (error) {
      toast(`清空失败：${error.message}`, "error");
    }
  });
  $("copyJson").addEventListener("click", async () => {
    try {
      await navigator.clipboard.writeText($("planJson").textContent || "");
      toast("JSON 已复制");
    } catch {
      toast("复制失败，请手动选择文本", "warning");
    }
  });
}

async function boot() {
  bind();
  tickClock();
  state.clockTimer = window.setInterval(tickClock, 1000);
  await loadManifest();
  connectWs();
  requestAnimationFrame(animate);
}

boot();
