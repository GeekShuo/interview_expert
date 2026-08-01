// ============ 全局状态 ============
const state = {
  sessionId: null,
  stages: [],
  currentStage: null,
  editor: null,
  streaming: false,
  mode: "full", // 完整面试 | coding/quiz/project 定向练习
  style: "strict", // strict 专业严谨 | warm 温和鼓励 | pressure 高压实战
  report: "", // 当前/查看中的报告 markdown（用于导出）
  abort: null, // 当前进行中流式请求的 AbortController（用于「停止」按钮中断）
};

// 是否移动端（小屏或触摸设备）：用于决定是否自动聚焦编辑器、避免键盘遮挡
const isTouch = window.matchMedia("(max-width: 768px), (pointer: coarse)").matches;

const $ = (id) => document.getElementById(id);

// 安全渲染 markdown：marked 解析后用 DOMPurify 消毒，防 XSS（LLM/用户/历史内容皆不可信）
const safeMd = (t) => (window.DOMPurify ? window.DOMPurify.sanitize(marked["parse"](t)) : marked["parse"](t));

// ============ 语音（ASR + TTS）============
const voiceOut = new VoiceOutput();
const voiceIn = new VoiceInput();
let voiceMode = false; // 语音模式：自动朗读 + 读完开麦 + 静音自动发送

// 麦克风识别实时更新输入框
voiceIn.onUpdate = (finalText, interim) => {
  $("userInput").value = finalText + interim;
  autoGrow();
};
voiceIn.onStart = () => setMicUI(true);
voiceIn.onStop = () => setMicUI(false);
// 静音超时自动停止并发送
voiceIn.onAutoStop = (txt) => {
  $("userInput").value = txt;
  autoGrow();
  sendMessage();
};

// ============ 云端语音（WebSocket ASR+TTS 全双工，优先于浏览器原生语音）============
let cloudAvail = false;   // 服务端是否配置了语音供应商
function cloudActive() { return voiceMode && cloudAvail && cloudVoice.connected; }

// 探测云端语音可用性（/api/voice/config 需鉴权；可重复调用，用于失败重试）
async function detectCloudVoice() {
  const ok = await cloudVoice.detect();
  cloudAvail = ok;
  const label = $("voiceModeLabel");
  if (label) {
    const names = { aliyun: "阿里", volcengine: "火山", mock: "本地调试" };
    label.title = ok
      ? `云端语音（${names[cloudVoice.cfg.provider] || cloudVoice.cfg.provider}）：全双工对话，面试官说话时可随时开口打断`
      : "未配置云端语音（浏览器原生语音兜底）：面试官自动朗读，读完自动开麦，静音自动发送";
  }
  return ok;
}
// 页面加载后的首次探测在 initLogin 内进行（此处 TOKEN 尚未初始化，提前调用会 TDZ 报错）

// 云端语音状态 → 麦克风/状态条 UI
cloudVoice.onStateChange = (s) => {
  const st = $("voiceStatus");
  if (s === "listening") {
    setMicUI(true);
    st.textContent = "🎧 聆听中，直接说话…";
    st.classList.remove("hidden");
  } else if (s === "speaking") {
    st.textContent = "🔊 面试官说话中（可随时开口打断）";
    st.classList.remove("hidden");
  } else {
    setMicUI(false);
    st.classList.add("hidden");
  }
};

// 云端语音事件 → 复用现有对话渲染（与 SSE 事件同一套处理）
let cloudInner = null;
let cloudAcc = "";
cloudVoice.onEvent = (ev) => {
  switch (ev.type) {
    case "asr_partial":  // 语音实时转写预览
      $("userInput").value = ev.text;
      autoGrow();
      break;
    case "asr_final":    // 一句说完：上屏为用户消息（回复由服务端自动发起）
      $("userInput").value = "";
      autoGrow();
      renderUserMessage(ev.text);
      break;
    case "turn_start":
      cloudInner = addMessage("assistant");
      cloudInner.parentElement.classList.add("cursor-blink");
      cloudAcc = "";
      state.streaming = true;
      $("stopBtn").classList.remove("hidden");
      break;
    case "token":
      if (ev.channel === "report") { handleReportToken(ev.text); break; }
      if (cloudInner) {
        cloudAcc += ev.text;
        cloudInner.innerHTML = safeMd(cloudAcc);
        scrollBottom();
      }
      break;
    case "stage": addSystemNote("进入环节：" + ev.label); setStage(ev.stage); break;
    case "problem": renderProblem(ev.problem); break;
    case "judge": renderJudgeResult(ev.result); break;
    case "report_start": handleReportToken("", true); break;
    case "turn_end":
      if (cloudInner) cloudInner.parentElement.classList.remove("cursor-blink");
      cloudInner = null;
      cloudAcc = "";
      state.streaming = false;
      $("stopBtn").classList.add("hidden");
      break;
    case "error":
      showToast(ev.message || "语音服务异常");
      break;
  }
};

// 恢复语音模式偏好（开始/恢复面试时调用）：开启并按需连接云端语音
async function ensureVoiceMode() {
  if (localStorage.getItem("ie_voice_mode") !== "1") return;
  $("voiceModeToggle").checked = true;
  voiceMode = true;
  voiceOut.enabled = true;
  if (!cloudAvail) await detectCloudVoice(); // 初次探测可能因 token 未就绪失败，此处重试
  if (cloudAvail && state.sessionId && !cloudVoice.connected) {
    try {
      await cloudVoice.connect(state.sessionId);
    } catch (e) {
      showToast("云端语音连接失败：" + (e.message || e) + "，回退浏览器原生语音");
    }
  }
}

function setMicUI(active) {
  const btn = $("micBtn");
  const status = $("voiceStatus");
  if (active) {
    btn.classList.add("mic-active");
    btn.textContent = "⏹";
    btn.title = "正在聆听…点击停止并发送";
    status.textContent = "🎧 正在聆听，说完停顿一下会自动发送…";
    status.classList.remove("hidden");
  } else {
    btn.classList.remove("mic-active");
    btn.textContent = "🎤";
    btn.title = "点击说话（再次点击停止并发送）";
    status.classList.add("hidden");
  }
}

// 语音模式开关
$("voiceModeToggle").addEventListener("change", async (e) => {
  voiceMode = e.target.checked;
  voiceOut.enabled = true;
  localStorage.setItem("ie_voice_mode", voiceMode ? "1" : "0");
  if (voiceMode) {
    if (!cloudAvail) await detectCloudVoice(); // 初次探测失败时给第二次机会
    if (cloudAvail && state.sessionId && !cloudVoice.connected) {
      try {
        await cloudVoice.connect(state.sessionId);
      } catch (err) {
        showToast("云端语音连接失败：" + (err.message || err) + "，回退浏览器原生语音");
      }
    } else if (!cloudAvail && !voiceIn.supported) {
      showToast("当前浏览器不支持语音识别，请使用 Chrome 或 Edge");
    }
  }
  if (!voiceMode) {
    voiceOut.cancel();
    voiceIn.stop();
    cloudVoice.disconnect();
    setMicUI(false);
  }
});

// 麦克风按钮：云端模式=静音/恢复；浏览器模式=手动开/关（关闭时若已识别到内容则发送）
$("micBtn").addEventListener("click", () => {
  if (cloudActive()) {
    const muted = cloudVoice.toggleMute();
    const st = $("voiceStatus");
    if (muted) {
      setMicUI(false);
      st.textContent = "🔇 已静音，点击麦克风恢复";
      st.classList.remove("hidden");
    } else {
      setMicUI(true);
      st.textContent = "🎧 聆听中，直接说话…";
      st.classList.remove("hidden");
    }
    return;
  }
  if (!voiceIn.supported) {
    showToast("当前浏览器不支持语音识别，请使用 Chrome 或 Edge");
    return;
  }
  if (voiceIn.listening) {
    const txt = voiceIn.finalText.trim() || $("userInput").value.trim();
    voiceIn.stop();
    if (txt) { $("userInput").value = txt; sendMessage(); }
  } else {
    voiceOut.cancel(); // 说话前先停掉朗读，避免回声
    voiceIn.start();
  }
});

// 语音模式下：面试官回复读完后自动开麦听用户（仅浏览器原生语音路径；云端为全双工常听）
function startListeningTurn() {
  if (!voiceMode || !voiceIn.supported || cloudActive()) return;
  if (state.streaming || voiceIn.listening) return;
  voiceIn.start();
}

function showToast(msg) {
  const el = $("voiceStatus");
  el.textContent = "⚠️ " + msg;
  el.classList.remove("hidden");
  setTimeout(() => el.classList.add("hidden"), 4000);
}

// ============ 启动面板逻辑 ============
$("resumeFile").addEventListener("change", async (e) => {
  const file = e.target.files[0];
  if (!file) return;
  $("resumeFileName").textContent = "解析中… " + file.name;
  const fd = new FormData();
  fd.append("file", file);
  try {
    const res = await apiFetch("/api/upload_resume", { method: "POST", body: fd });
    const data = await res.json();
    const text = data.text || "";
    if (!text || text.startsWith("[") ) {
      // 解析失败或未提取到文本（如畸形 PDF / 扫描件）
      $("resumeFileName").textContent = "⚠️ 未提取到文本，请改用粘贴";
      if (text) $("resumeText").value = text; // 保留错误信息供参考
    } else {
      $("resumeText").value = text;
      $("resumeFileName").textContent = `✓ ${file.name} · 已提取 ${text.length} 字，已解析要点可核对`;
      showResumePreview(data.parsed);
    }
  } catch {
    $("resumeFileName").textContent = "解析失败，请改用粘贴";
  }
});

// 上传后展示简历结构化解析预览（summary + 可编辑的追问点）
function showResumePreview(parsed) {
  if (!parsed) return;
  $("resumeSummary").textContent = parsed.summary || "（无）";
  $("resumeProbe").value = parsed.probe_points || "";
  $("resumePreview").classList.remove("hidden");
}

$("startBtn").addEventListener("click", startInterview);

// ============ 练习模式选择（完整 / 定向练习）============
function selectMode(mode) {
  state.mode = mode;
  document.querySelectorAll(".mode-pill").forEach((b) => {
    const on = b.dataset.mode === mode;
    b.classList.toggle("border-brand-500", on);
    b.classList.toggle("bg-brand-500/20", on);
    b.classList.toggle("text-brand-100", on);
    b.classList.toggle("border-white/10", !on);
    b.classList.toggle("text-slate-400", !on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
  $("difficultyRow").classList.toggle("hidden", mode !== "coding");
  // 「可不填简历/JD」提示仅对定向练习（coding/quiz/project）显示；full 与 no_code 都按完整面试填简历/JD
  $("directedNote").classList.toggle("hidden", !["coding", "quiz", "project"].includes(mode));
}
document.querySelectorAll(".mode-pill").forEach((btn) => {
  btn.addEventListener("click", () => selectMode(btn.dataset.mode));
});

// ============ 面试方向选择（影响面试官人设：CV/NLP/推荐/LLM…）============
function selectDir(dir) {
  document.querySelectorAll(".dir-pill").forEach((b) => {
    const on = b.dataset.dir === dir;
    b.classList.toggle("border-brand-500", on);
    b.classList.toggle("bg-brand-500/20", on);
    b.classList.toggle("text-brand-100", on);
    b.classList.toggle("text-slate-400", !on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
}
document.querySelectorAll(".dir-pill").forEach((btn) => {
  btn.addEventListener("click", () => selectDir(btn.dataset.dir));
});

// ============ 面试官风格选择 ============
function selectStyle(style) {
  state.style = style;
  document.querySelectorAll(".style-pill").forEach((b) => {
    const on = b.dataset.style === style;
    b.classList.toggle("border-brand-500", on);
    b.classList.toggle("bg-brand-500/20", on);
    b.classList.toggle("text-brand-100", on);
    b.classList.toggle("border-white/10", !on);
    b.classList.toggle("text-slate-400", !on);
    b.setAttribute("aria-pressed", on ? "true" : "false");
  });
}
document.querySelectorAll(".style-pill").forEach((btn) => {
  btn.addEventListener("click", () => selectStyle(btn.dataset.style));
});

// ============ 版本切换（普通 / Pro）：localStorage 持久化，影响新建会话 ============
// 每个浏览器分配一个稳定匿名 user_id，用于「成长曲线 / 错题本」按用户隔离（多开互不串数据）
const UID = (() => {
  let id = localStorage.getItem("ie_uid");
  if (!id) { id = "u_" + Math.random().toString(36).slice(2, 10); localStorage.setItem("ie_uid", id); }
  return id;
})();
// 登录账户：登录后以账户名作为稳定 user_id（历史/错题跨设备一致）；未登录回退匿名 UID
let ACCOUNT = (() => { try { return JSON.parse(localStorage.getItem("ie_user")); } catch { return null; } })();
function userId() { return (ACCOUNT && ACCOUNT.username) || UID; }

// ============ 认证 token（JWT）：受保护 API 一律经 apiFetch 自动携带 ============
let TOKEN = localStorage.getItem("ie_token") || "";
function setToken(t) {
  TOKEN = t || "";
  if (t) localStorage.setItem("ie_token", t); else localStorage.removeItem("ie_token");
}

// 游客身份换取匿名 token：保证未登录访客也有可用凭证（数据隔离沿用旧行为）
async function ensureAnonToken() {
  if (TOKEN) return;
  try {
    const res = await fetch("/api/anon_token", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ uid: UID }),
    });
    const d = await res.json();
    if (d.token) setToken(d.token);
  } catch (e) { /* 网络异常：后续请求 401 时会引导重新登录 */ }
}

// 统一请求封装：自动带 Bearer token；401 时清登录态并弹登录层
async function apiFetch(url, options = {}) {
  options.headers = Object.assign({}, options.headers);
  if (TOKEN) options.headers["Authorization"] = "Bearer " + TOKEN;
  const res = await fetch(url, options);
  if (res.status === 401) {
    ACCOUNT = null;
    localStorage.removeItem("ie_user");
    setToken("");
    renderUser();
    document.getElementById("loginModal").classList.remove("hidden");
    const d = await res.json().catch(() => ({}));
    throw new Error(d.detail || "登录已过期，请重新登录");
  }
  return res;
}
let currentTier = localStorage.getItem("ie_tier") || "normal";

function renderTierToggle() {
  document.querySelectorAll(".tier-opt").forEach((b) => {
    const on = b.dataset.tier === currentTier;
    b.classList.toggle("bg-amber-400/20", on);
    b.classList.toggle("text-amber-200", on);
    b.classList.toggle("text-slate-400", !on);
  });
}
function setTier(t) {
  currentTier = t === "pro" ? "pro" : "normal";
  localStorage.setItem("ie_tier", currentTier);
  renderTierToggle();
  showHint(currentTier === "pro" ? "已切换到 Pro 版（更强模型 + 更深点评）" : "已切换到普通版");
}
document.querySelectorAll(".tier-opt").forEach((btn) => {
  btn.addEventListener("click", () => setTier(btn.dataset.tier));
});
renderTierToggle();

// ============ 登录 / 账户（多用户隔离入口）============
function renderUser() {
  const label = ACCOUNT ? (ACCOUNT.name + " · @" + ACCOUNT.username) : "游客";
  const setupChip = document.getElementById("setupUserChip");
  const chip = document.getElementById("userChip");
  if (ACCOUNT) {
    if (setupChip) { setupChip.textContent = "👤 " + label + "（退出）"; setupChip.classList.remove("hidden"); setupChip.onclick = logout; }
    if (chip) { chip.textContent = "👤 " + label; chip.classList.remove("hidden"); chip.onclick = logout; }
  } else {
    if (setupChip) setupChip.classList.add("hidden");
    if (chip) chip.classList.add("hidden");
  }
}

function logout() {
  ACCOUNT = null;
  localStorage.removeItem("ie_user");
  setToken("");
  ensureAnonToken(); // 退出后回退为游客匿名凭证
  document.getElementById("loginModal").classList.remove("hidden");
  renderUser();
}

async function doLogin(username, password) {
  try {
    const res = await fetch("/api/login", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username, password }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || "登录失败");
    }
    const d = await res.json();
    setToken(d.token);
    ACCOUNT = { username: d.user_id, name: d.name };
    localStorage.setItem("ie_user", JSON.stringify(ACCOUNT));
    document.getElementById("loginModal").classList.add("hidden");
    renderUser();
    showHint("已登录：" + d.name + "，历史与错题将按账户隔离保存");
  } catch (e) {
    const err = document.getElementById("loginErr");
    err.textContent = e.message;
    err.classList.remove("hidden");
  }
}

async function doRegister(username, password, name) {
  try {
    const res = await fetch("/api/register", {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: new URLSearchParams({ username, password, name }),
    });
    if (!res.ok) {
      const d = await res.json().catch(() => ({}));
      throw new Error(d.detail || "注册失败");
    }
    const d = await res.json();
    setToken(d.token);
    ACCOUNT = { username: d.user_id, name: d.name };
    localStorage.setItem("ie_user", JSON.stringify(ACCOUNT));
    document.getElementById("loginModal").classList.add("hidden");
    renderUser();
    showHint("注册成功，已登录：" + d.name);
  } catch (e) {
    const err = document.getElementById("loginErr");
    err.textContent = e.message;
    err.classList.remove("hidden");
  }
}

async function initLogin() {
  await ensureAnonToken(); // 保证游客也持有可用凭证
  detectCloudVoice(); // 凭证就绪后探测云端语音（fire-and-forget）
  // 拉取预置账户，渲染「一键登录」按钮（演示账户密码统一 pass123，直接登录）
  try {
    const res = await fetch("/api/accounts");
    const data = await res.json();
    const box = document.getElementById("accountQuick");
    const accounts = data.accounts || [];
    // 后端关闭演示账户时（生产），隐藏一键登录与演示提示
    if (!accounts.length) {
      box.classList.add("hidden");
      const hint = document.getElementById("demoHint");
      if (hint) hint.classList.add("hidden");
    }
    if (accounts.length) {
      box.innerHTML = accounts.map((a) =>
        `<button type="button" data-user="${escapeHtml(a.username)}" class="quick-login text-sm px-3 py-2.5 rounded-lg border border-white/10 hover:border-brand-500 bg-ink-700/50 flex items-center justify-between">
           <span>${escapeHtml(a.name)}</span><span class="text-xs text-slate-500">@${escapeHtml(a.username)}</span>
         </button>`).join("");
      box.querySelectorAll(".quick-login").forEach((b) => {
        b.addEventListener("click", () => doLogin(b.dataset.user, "pass123"));
      });
    } else {
      box.innerHTML = '<div class="text-xs text-slate-500">暂无预置账户，请用上方账号密码登录</div>';
    }
  } catch (e) {
    document.getElementById("accountQuick").innerHTML = '<div class="text-xs text-slate-500">加载账户列表失败</div>';
  }

  document.getElementById("loginBtn").addEventListener("click", () => {
    document.getElementById("loginErr").classList.add("hidden");
    doLogin(document.getElementById("loginUser").value.trim(), document.getElementById("loginPass").value);
  });
  document.getElementById("loginPass").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("loginBtn").click();
  });
  document.getElementById("regToggle").addEventListener("click", () => {
    document.getElementById("regArea").classList.toggle("hidden");
  });
  document.getElementById("regBtn").addEventListener("click", () => {
    document.getElementById("loginErr").classList.add("hidden");
    doRegister(
      document.getElementById("regUser").value.trim(),
      document.getElementById("regPass").value,
      document.getElementById("regName").value.trim()
    );
  });
  document.getElementById("regPass").addEventListener("keydown", (e) => {
    if (e.key === "Enter") document.getElementById("regBtn").click();
  });
  document.getElementById("guestBtn").addEventListener("click", () => {
    ACCOUNT = null;
    localStorage.removeItem("ie_user");
    ensureAnonToken();
    document.getElementById("loginModal").classList.add("hidden");
    renderUser();
  });

  if (ACCOUNT) {
    document.getElementById("loginModal").classList.add("hidden");
  } else {
    document.getElementById("loginModal").classList.remove("hidden");
  }
  renderUser();
}

initLogin();

async function startInterview(overrides = {}) {
  const resumeText = $("resumeText").value.trim();
  const jdText = $("jdText").value.trim();
  const mode = overrides.mode || state.mode || "full";
  const difficulty = overrides.difficulty || $("difficultySelect")?.value || "";
  const problemId = overrides.problemId || "";
  if ((mode === "full" || mode === "no_code") && !jdText) {
    showHint("请至少填写目标岗位 JD");
    return;
  }
  $("startBtn").disabled = true;
  $("startBtn").textContent = "正在匹配面试官…";

  const fd = new FormData();
  fd.append("resume_text", resumeText);
  fd.append("jd_text", jdText);
  fd.append("mode", mode);
  const dirBtn = document.querySelector('.dir-pill[aria-pressed="true"]');
  const uiDir = dirBtn ? dirBtn.dataset.dir : "auto";
  const direction = overrides.direction || (uiDir !== "auto" ? uiDir : "");
  if (direction) fd.append("direction", direction);
  const probeEl = $("resumeProbe");
  const probe = probeEl && !$("resumePreview").classList.contains("hidden")
    ? probeEl.value.trim() : "";
  if (probe) fd.append("resume_probe_points", probe);
  fd.append("style", state.style || "strict");
  fd.append("tier", currentTier);
  if (difficulty) fd.append("difficulty", difficulty);
  if (problemId) fd.append("problem_id", problemId);
  if (direction) fd.append("direction", direction);
  // 若之前有进行中的会话，作为 previous 传回，后端会将其标记为「未完成」并落盘
  const prev = sessionStorage.getItem("lastSessionId");
  if (prev) fd.append("previous_session_id", prev);

  try {
    const res = await apiFetch("/api/start", { method: "POST", body: fd });
    const data = await res.json();
    state.sessionId = data.session_id;
    state.stages = data.stages;
    state.currentStage = data.stage;
    state.persona = data.persona;
    state.tier = data.tier;
    sessionStorage.setItem("lastSessionId", data.session_id);
    localStorage.setItem("iv_session_id", data.session_id); // 供刷新/重开页面后恢复

    if (!data.llm_ready) {
      showHint("⚠️ 后端未配置有效的 LLM API Key，面试官将无法回复。请在 backend/.env 中填写。");
    }

    // 切换界面
    $("setup").classList.add("hidden");
    $("interview").classList.remove("hidden");
    $("interview").classList.add("flex");

    // 面试官信息
    $("personaName").textContent = data.persona.name + " · " + data.persona.direction;
    $("personaTitle").textContent = data.persona.title + "　|　" + (data.jd.title || "算法岗");
    renderStageBar();

    // 简历解析结果可见：让用户知道面试官"读到了什么"
    if (data.resume_summary) {
      addSystemNote("📄 面试官已读简历，理解为：" + truncate(data.resume_summary, 100));
    }

    // 恢复语音模式偏好（开启时开场白直接走语音通道）
    await ensureVoiceMode();

    // 拉开场白
    streamOpening();
  } catch (e) {
    showHint("启动失败：" + e.message);
    $("startBtn").disabled = false;
    $("startBtn").textContent = "开始面试";
  }
}

function showHint(msg) {
  const h = $("setupHint");
  h.textContent = msg;
  h.classList.remove("hidden");
}

function truncate(s, n) {
  s = s || "";
  return s.length > n ? s.slice(0, n) + "…" : s;
}

// ============ 会话恢复（刷新/重开页面后继续进行中的面试）============
async function checkResumable() {
  const sid = localStorage.getItem("iv_session_id");
  if (!sid) return;
  try {
    const res = await apiFetch("/api/state?session_id=" + encodeURIComponent(sid));
    if (!res.ok) { localStorage.removeItem("iv_session_id"); return; }
    const st = await res.json();
    if (st.stage === "finished" || st.stage === "report" || st.abandoned) {
      localStorage.removeItem("iv_session_id");
      return;
    }
    const banner = $("resumeBanner");
    banner.classList.remove("hidden");
    $("resumeInfo").textContent =
      `${st.persona.name} · ${st.jd.title || "算法岗"} · 进行到「${st.stage_label}」环节`;
    $("resumeBtn").onclick = () => resumeInterview(st);
  } catch { /* 服务不可用时静默 */ }
}

function resumeInterview(st) {
  state.sessionId = st.session_id;
  state.stages = st.stages;
  state.currentStage = st.stage;
  state.persona = st.persona;
  sessionStorage.setItem("lastSessionId", st.session_id);

  $("setup").classList.add("hidden");
  $("interview").classList.remove("hidden");
  $("interview").classList.add("flex");
  $("personaName").textContent = st.persona.name + " · " + st.persona.direction;
  $("personaTitle").textContent = st.persona.title + "　|　" + (st.jd.title || "算法岗");
  renderStageBar();

  // 重建对话
  addSystemNote("已恢复上次面试，继续加油！");
  (st.history || []).forEach((m) => {
    if (m.role === "user") renderUserMessage(m.content);
    else addMessage("assistant", false).innerHTML = safeMd(m.content);
  });
  // 恢复代码面板（不重复插入判题提示）
  if (st.stage === "coding" && st.current_problem) {
    state.currentProblem = st.current_problem;
    $("problemTitle").textContent = `${st.current_problem.title}（${st.current_problem.difficulty}）`;
    $("problemStatement").innerHTML = safeMd(st.current_problem.statement || "");
    setStage("coding");
  } else {
    setStage(st.stage);
  }
  scrollBottom();
  ensureVoiceMode(); // 恢复语音模式（fire-and-forget）
}

checkResumable();

// ============ 环节进度条 ============
function renderStageBar() {
  const bar = $("stageBar");
  // finished 视为全部完成
  const curIdx =
    state.currentStage === "finished"
      ? state.stages.length
      : state.stages.findIndex((s) => s.key === state.currentStage);
  bar.innerHTML = state.stages
    .map((s, i) => {
      const done = i < curIdx;
      const active = i === curIdx;
      const cls = active
        ? "bg-brand-500 text-white"
        : done
        ? "bg-emerald-500/20 text-emerald-300"
        : "bg-white/5 text-slate-500";
      const dot = done ? "✓" : i + 1;
      return `<span class="px-2.5 py-1 rounded-full ${cls} flex items-center gap-1">
        <span class="w-4 h-4 rounded-full ${active ? "bg-white/20" : "bg-white/10"} inline-flex items-center justify-center text-[10px]">${dot}</span>${s.label}</span>`;
    })
    .join('<span class="text-slate-600">›</span>');
}

function setStage(stageKey) {
  state.currentStage = stageKey;
  renderStageBar();
  // 进入/离开算法环节切换代码面板
  if (stageKey === "coding") {
    showCodePane();
    focusEditor();
    // 进入算法环节：引导用编辑器写代码，避免直接在聊天框贴代码
    $("userInput").placeholder = "在此与面试官交流思路（代码请写在右侧编辑器，写完点「提交代码」）";
    $("expandEditorBtn").classList.remove("hidden");
    $("submitCodeBtn").classList.add("pulse-attn");
  } else {
    $("codePane").classList.add("hidden");
    $("codePane").classList.remove("flex");
    $("userInput").placeholder = "输入你的回答…（Enter 发送，Shift+Enter 换行）";
    $("expandEditorBtn").classList.add("hidden");
    $("expandEditorBtn").textContent = "⤢ 展开";
    $("chatPane").classList.remove("hidden");
    $("codePane").classList.remove("flex-1");
    $("codePane").classList.add("w-[46%]");
    $("submitCodeBtn").classList.remove("pulse-attn");
  }
  if (stageKey === "report" || stageKey === "finished") {
    localStorage.removeItem("iv_session_id"); // 面试已收尾，不再提供恢复
    openReport();
  }
}

// ============ 消息渲染 ============
function addMessage(role, animate = true) {
  const wrap = document.createElement("div");
  wrap.className = "flex " + (role === "user" ? "justify-end" : "justify-start") + (animate ? " msg-anim" : "");
  const bubble = document.createElement("div");
  bubble.className =
    "bubble max-w-[85%] rounded-2xl px-4 py-3 text-sm leading-relaxed " +
    (role === "user"
      ? "bg-brand-500 text-white rounded-br-sm"
      : "bg-ink-700/70 border border-white/5 rounded-bl-sm");
  const inner = document.createElement("div");
  inner.className = "markdown";
  bubble.appendChild(inner);
  wrap.appendChild(bubble);
  // 八股环节：每条面试官提问可一键自评入错题本（八股无客观判题，用户自评更准）
  if (role === "assistant" && state.currentStage === "quiz") {
    wrap.classList.add("flex-col", "items-start");
    const btn = document.createElement("button");
    btn.className = "mt-1 text-[11px] text-red-300/80 hover:text-red-200 hover:underline";
    btn.textContent = "📕 没答好？加入错题本";
    btn.addEventListener("click", () => {
      const q = (inner.innerText || "").trim();
      if (!q) return;
      const dir = (state.persona && state.persona.direction) || "";
      btn.disabled = true;
      btn.textContent = "提交中…";
      apiFetch("/api/mistakes/quiz", {
        method: "POST",
        headers: { "Content-Type": "application/x-www-form-urlencoded" },
        body: new URLSearchParams({ direction: dir, question: q }),
      }).then(() => { btn.textContent = "✅ 已加入错题本"; })
        .catch(() => { btn.disabled = false; btn.textContent = "📕 没答好？加入错题本"; showHint("加入失败，请重试"); });
    });
    wrap.appendChild(btn);
  }
  $("messages").appendChild(wrap);
  scrollBottom();
  return inner;
}

function addSystemNote(text) {
  const d = document.createElement("div");
  d.className = "text-center text-xs text-slate-500 my-2";
  d.textContent = "— " + text + " —";
  $("messages").appendChild(d);
  scrollBottom();
}

// 渲染用户消息：若内容疑似代码，则包裹为代码块，避免聊天框里渲染错乱
function renderUserMessage(text) {
  const looksLikeCode =
    /[\n;{}]/.test(text) &&
    /(def |class |void\s+main|public |private |import |function |=>|console\.|print\(|return |#include|using )/.test(text);
  const html = looksLikeCode ? safeMd("```\n" + text + "\n```") : safeMd(text);
  addMessage("user").innerHTML = html;
}

function scrollBottom() {
  const m = $("messages");
  m.scrollTop = m.scrollHeight;
}

// ============ SSE 流式核心 ============
async function streamSSE(url, options, { onToken, onReport, onStage, onProblem, onJudge }, signal) {
  let res;
  try {
    res = await apiFetch(url, { ...options, signal });
  } catch (e) {
    if (e && e.name === "AbortError") return; // 用户主动停止，正常结束
    throw e;
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
  try {
    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      buf += decoder.decode(value, { stream: true });
      let idx;
      while ((idx = buf.indexOf("\n\n")) !== -1) {
        const chunk = buf.slice(0, idx).trim();
        buf = buf.slice(idx + 2);
        if (!chunk.startsWith("data:")) continue;
        let ev;
        try { ev = JSON.parse(chunk.slice(5).trim()); }
        catch { continue; } // 跳过无法解析的片段，避免整条流崩溃
        if (ev.type === "token") {
          if (ev.channel === "report") onReport && onReport(ev.text);
          else onToken && onToken(ev.text);
        } else if (ev.type === "stage") {
          onStage && onStage(ev);
        } else if (ev.type === "problem") {
          onProblem && onProblem(ev.problem);
        } else if (ev.type === "judge") {
          onJudge && onJudge(ev.result);
        } else if (ev.type === "report_start") {
          onReport && onReport("", true);
        } else if (ev.type === "error") {
          onToken && onToken("\n\n[出错] " + ev.message);
        }
      }
    }
  } catch (e) {
    if (e && e.name === "AbortError") { try { reader.cancel().catch(() => {}); } catch (_) {} return; } // 流读取中被中断，正常结束
    throw e;
  }
}

// 开场白
async function streamOpening() {
  if (cloudActive()) {
    // 云端语音：开场白经 WS 走 LLM+TTS，气泡由 turn_start/token 事件驱动
    state.streaming = true;
    cloudVoice.sendOpening();
    return;
  }
  const inner = addMessage("assistant");
  inner.parentElement.classList.add("cursor-blink");
  let acc = "";
  const spk = makeSpeaker(voiceOut);
  state.streaming = true;
  state.abort = new AbortController();
  $("stopBtn").classList.remove("hidden");
  try {
    await streamSSE("/api/opening?session_id=" + state.sessionId, { method: "GET" }, {
      onToken: (t) => {
        acc += t;
        inner.innerHTML = safeMd(acc);
        if (voiceMode) spk.feed(acc);
        scrollBottom();
      },
      onStage: (ev) => setStage(ev.stage),
      onProblem: (p) => renderProblem(p),
      onReport: handleReportToken,
    }, state.abort.signal);
  } catch (e) {
    if (!(e && e.name === "AbortError")) {
      inner.innerHTML = safeMd(acc + "\n\n[连接出错] " + (e && e.message ? e.message : e));
    }
  } finally {
    inner.parentElement.classList.remove("cursor-blink");
    state.streaming = false;
    $("stopBtn").classList.add("hidden");
    state.abort = null;
  }
  if (voiceMode) spk.finish(startListeningTurn);
}

// 用户发送
$("sendBtn").addEventListener("click", sendMessage);
$("userInput").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    sendMessage();
  }
});

// 停止当前正在进行的流式生成（开场白 / 面试官回复）
$("stopBtn").addEventListener("click", () => {
  if (cloudActive() && state.streaming) {
    cloudVoice.interrupt(); // 打断：停播 + 取消服务端 LLM/TTS 轮次
    addSystemNote("⏹ 已打断面试官");
    return;
  }
  if (state.abort) {
    state.abort.abort();
    voiceOut.cancel(); // 停掉残留朗读
    addSystemNote("⏹ 已停止生成");
  }
});

async function sendMessage() {
  const text = $("userInput").value.trim();
  if (!text) return;
  if (state.streaming && !cloudActive()) return; // 云端语音下打字也算打断，不阻塞
  if (state.currentStage === "finished") {
    addSystemNote("面试已结束，点击「重新开始」可再开一场");
    return;
  }
  if (cloudActive()) {
    // 云端语音：文字消息走 WS（面试官语音回复），服务端自动打断当前轮次
    cloudVoice.interrupt();
    $("userInput").value = "";
    autoGrow();
    renderUserMessage(text);
    state.streaming = true;
    cloudVoice.sendText(text);
    return;
  }
  if (voiceIn.listening) voiceIn.stop();
  voiceOut.cancel(); // 停掉残留朗读
  $("userInput").value = "";
  autoGrow();
  renderUserMessage(text);

  const inner = addMessage("assistant");
  inner.parentElement.classList.add("cursor-blink");
  let acc = "";
  const spk = makeSpeaker(voiceOut);
  state.streaming = true;
  state.abort = new AbortController();
  $("stopBtn").classList.remove("hidden");
  $("sendBtn").disabled = true;
  try {
    await streamSSE("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, message: text }),
    }, {
      onToken: (t) => { acc += t; inner.innerHTML = safeMd(acc); if (voiceMode) spk.feed(acc); scrollBottom(); },
      onStage: (ev) => { addSystemNote("进入环节：" + ev.label); setStage(ev.stage); },
      onProblem: (p) => renderProblem(p),
      onReport: handleReportToken,
    }, state.abort.signal);
  } catch (e) {
    if (!(e && e.name === "AbortError")) {
      inner.innerHTML = safeMd(acc + "\n\n[连接出错] " + (e && e.message ? e.message : e));
    }
  } finally {
    inner.parentElement.classList.remove("cursor-blink");
    state.streaming = false;
    $("stopBtn").classList.add("hidden");
    $("sendBtn").disabled = false;
    state.abort = null;
  }
  if (voiceMode) spk.finish(startListeningTurn);
}

// 自适应输入框高度
$("userInput").addEventListener("input", autoGrow);
function autoGrow() {
  const el = $("userInput");
  el.style.height = "auto";
  el.style.height = Math.min(el.scrollHeight, 160) + "px";
}

// ============ 代码面板 / Monaco ============
require.config({ paths: { vs: "https://cdn.jsdelivr.net/npm/monaco-editor@0.45.0/min/vs" } });

function showCodePane() {
  $("codePane").classList.remove("hidden");
  $("codePane").classList.add("flex");
  if (!state.editor) {
    require(["vs/editor/editor.main"], () => {
      state.editor = monaco.editor.create($("editor"), {
        value: starterCode($("langSelect").value),
        language: "python",
        theme: "vs-dark",
        fontSize: 14,
        minimap: { enabled: false },
        automaticLayout: true,
        scrollBeyondLastLine: false,
        fixedOverflowWidgets: true,
        wordWrap: "on",
        scrollbar: { useShadows: false, verticalScrollbarSize: 8, horizontalScrollbarSize: 8 },
      });
      if (!isTouch) focusEditor(); // 移动端不自动聚焦，避免键盘弹出遮挡代码区
    });
  }
}

// 聚焦代码编辑器（创建完成后）
function focusEditor() {
  if (state.editor) {
    try { state.editor.focus(); } catch (e) {}
  }
}

// 展开/收起代码编辑器（全屏写代码，隐藏对话区）
function toggleEditorExpand() {
  const expanded = $("chatPane").classList.contains("hidden");
  if (expanded) {
    $("chatPane").classList.remove("hidden");
    $("codePane").classList.remove("flex-1");
    $("codePane").classList.add("w-[46%]");
    $("expandEditorBtn").textContent = "⤢ 展开";
  } else {
    $("chatPane").classList.add("hidden");
    $("codePane").classList.remove("w-[46%]");
    $("codePane").classList.add("flex-1");
    $("expandEditorBtn").textContent = "⤡ 收起";
  }
  if (state.editor) setTimeout(() => state.editor.layout(), 60);
}

$("langSelect").addEventListener("change", (e) => {
  if (!state.editor) return;
  monaco.editor.setModelLanguage(state.editor.getModel(), e.target.value);
  // 编辑器还是初始骨架时，切语言同步刷新起始代码（避免覆盖用户已写内容）
  const cur = state.editor.getValue().trim();
  const isStarter = !cur || /在这里编写你的解法|请保持函数\/类名不变/.test(cur);
  if (isStarter) state.editor.setValue(starterCode(e.target.value));
});

function renderProblem(p) {
  state.currentProblem = p;
  $("problemTitle").textContent = `${p.title}（${p.difficulty}）`;
  $("problemStatement").innerHTML = safeMd(p.statement || "");
  showCodePane();
  const lang = $("langSelect").value;
  if (state.editor) state.editor.setValue(starterCode(lang));
  if (p.judgeable) {
    addSystemNote("🧪 本题提交代码后，系统会在沙箱中用测试用例真实运行并自动判题（Python），判题结果面试官同样可见。可多次提交修正。");
  }
}

// 生成编辑器起始代码：Python 且题目带签名时，预填函数/类骨架（保证判题入口命名正确）
function starterCode(lang) {
  const p = state.currentProblem;
  if (lang === "python" && p && p.signature) {
    const sig = p.signature;
    const head = `# ${p.title}（${p.difficulty}）\n# 请保持函数/类名不变，提交后系统会用测试用例自动运行你的代码\n`;
    if (sig.trim().startsWith("class ")) return head + sig + "\n";
    return head + sig + "\n    # 在这里编写你的解法\n    pass\n";
  }
  const starter = {
    python: "# 在这里编写你的解法\n",
    cpp: "// 在这里编写你的解法（注意：自动判题仅支持 Python，其他语言由面试官人工评判）\n",
    java: "// 在这里编写你的解法（注意：自动判题仅支持 Python，其他语言由面试官人工评判）\n",
    javascript: "// 在这里编写你的解法（注意：自动判题仅支持 Python，其他语言由面试官人工评判）\n",
  };
  return starter[lang] || "";
}

// ============ 沙箱判题结果卡片 ============
function renderJudgeResult(res) {
  const wrap = document.createElement("div");
  wrap.className = "flex justify-start";
  const card = document.createElement("div");
  let inner = "";
  if (!res || res.supported === false) {
    card.className = "max-w-[85%] rounded-2xl px-4 py-3 text-xs border border-white/10 bg-ink-700/50 text-slate-400";
    inner = `<div class="font-medium mb-0.5">🧪 自动判题</div><div>${escapeHtml((res && res.message) || "本次未自动判题")}</div>`;
  } else if (res.error) {
    card.className = "max-w-[85%] rounded-2xl px-4 py-3 text-xs border border-red-400/30 bg-red-500/10";
    inner = `<div class="font-medium text-red-300 mb-1">🧪 自动判题 · 运行失败（0/${res.total ?? "?"}）</div>
      <div class="text-red-200/80 mb-1.5">代码没能成功运行——通常是语法/缩进错误，或函数、类名与题目给出的签名不一致。修正后可再次提交。</div>
      <details><summary class="cursor-pointer text-slate-400 hover:text-slate-200">查看错误详情</summary>
      <pre class="whitespace-pre-wrap text-red-200/90 bg-ink-900/60 rounded-lg p-2 mt-1 max-h-40 overflow-y-auto">${escapeHtml(res.error)}</pre></details>`;
  } else {
    const allPass = res.passed === res.total;
    card.className = "max-w-[85%] rounded-2xl px-4 py-3 text-xs border " +
      (allPass ? "border-emerald-400/30 bg-emerald-500/10" : "border-amber-400/30 bg-amber-500/10");
    inner = `<div class="font-medium mb-1 ${allPass ? "text-emerald-300" : "text-amber-300"}">
      🧪 自动判题 · 通过 ${res.passed}/${res.total} 组用例 ${allPass ? "✅" : ""}</div>`;
    const fails = (res.results || []).map((r, i) => ({ ...r, idx: i + 1 })).filter((r) => !r.ok).slice(0, 3);
    if (fails.length) {
      // 面试模式：默认只给"你的输出"，输入/期望折叠——鼓励像真实面试一样先自己排查
      inner += fails.map((r) => `
        <div class="mt-1.5 bg-ink-900/60 rounded-lg p-2 space-y-0.5">
          <div class="text-slate-400">用例 ${r.idx} 未通过${r.error ? `　<span class="text-red-300">${escapeHtml(r.error)}</span>` : ""}</div>
          <div>你的输出：<code class="text-red-300">${escapeHtml(JSON.stringify(r.got))}</code></div>
          <details><summary class="cursor-pointer text-slate-500 hover:text-slate-300">展开用例详情（建议先自己排查，更接近真实面试）</summary>
            <div class="mt-1">输入：<code class="text-slate-300">${escapeHtml(JSON.stringify(r.input))}</code></div>
            <div>期望：<code class="text-emerald-300">${escapeHtml(JSON.stringify(r.expected))}</code></div>
          </details>
        </div>`).join("");
      inner += `<div class="mt-1.5 text-slate-500">修正代码后可再次提交。</div>`;
    }
  }
  card.innerHTML = inner;
  wrap.appendChild(card);
  $("messages").appendChild(wrap);
  scrollBottom();
}

$("submitCodeBtn").addEventListener("click", async () => {
  if (!state.editor) return;
  if (state.streaming && !cloudActive()) return;
  const code = state.editor.getValue();
  const lang = $("langSelect").value;
  addMessage("user").innerHTML = safeMd("已提交代码：\n```" + lang + "\n" + code + "\n```");
  if (cloudActive()) {
    // 云端语音：判题+点评走 WS，面试官语音反馈
    state.streaming = true;
    cloudVoice.sendCode(code, lang);
    return;
  }

  const inner = addMessage("assistant");
  inner.parentElement.classList.add("cursor-blink");
  let acc = "";
  const spk = makeSpeaker(voiceOut);
  voiceOut.cancel();
  state.streaming = true;
  try {
    await streamSSE("/api/submit_code", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, code, language: lang }),
    }, {
      onToken: (t) => { acc += t; inner.innerHTML = safeMd(acc); if (voiceMode) spk.feed(acc); scrollBottom(); },
      onStage: (ev) => { addSystemNote("进入环节：" + ev.label); setStage(ev.stage); },
      onProblem: (p) => renderProblem(p),
      onJudge: (r) => renderJudgeResult(r),
      onReport: handleReportToken,
    });
  } catch (e) {
    inner.innerHTML = safeMd(acc + "\n\n[连接出错] " + (e && e.message ? e.message : e));
  } finally {
    inner.parentElement.classList.remove("cursor-blink");
    state.streaming = false;
  }
  // 若离开算法环节回到对话，则读完开麦；否则仅朗读
  if (voiceMode) spk.finish(state.currentStage === "coding" ? null : startListeningTurn);
});

// ============ 报告 ============
let reportAcc = "";
function handleReportToken(text, start) {
  if (start) {
    reportAcc = "";
    renderReportScore(""); // 先隐藏分数条
    openReport();
    return;
  }
  reportAcc += text;
  $("reportContent").innerHTML = safeMd(reportAcc);
  state.report = reportAcc;
  renderReportScore(reportAcc);
  const rc = $("reportContent");
  rc.scrollTop = rc.scrollHeight;
}

// 报告导出：Markdown / PDF（可留存与分享）
function exportMarkdown() {
  const md = state.report || "";
  if (!md.trim()) { showHint("暂无可导出的报告"); return; }
  const blob = new Blob(["# 模拟面试评估报告\n\n" + md], { type: "text/markdown;charset=utf-8" });
  const a = document.createElement("a");
  a.href = URL.createObjectURL(blob);
  a.download = "面试评估报告.md";
  document.body.appendChild(a); a.click(); a.remove();
  URL.revokeObjectURL(a.href);
}

function exportPDF() {
  const md = state.report || "";
  if (!md.trim()) { showHint("暂无可导出的报告"); return; }
  const html = '<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>面试评估报告</title>'
    + '<style>body{font-family:system-ui,"Microsoft YaHei",sans-serif;max-width:820px;margin:32px auto;padding:0 20px;color:#1a1a1a;line-height:1.7}'
    + 'h1,h2,h3{border-bottom:1px solid #eee;padding-bottom:4px;color:#1f2a6e}'
    + 'code{background:#f3f4f6;padding:2px 6px;border-radius:4px;font-size:13px}'
    + 'pre{background:#0f172a;color:#e2e8f0;padding:12px;border-radius:8px;overflow:auto}'
    + 'table{border-collapse:collapse}td,th{border:1px solid #ddd;padding:6px 10px}'
    + 'blockquote{border-left:3px solid #6366f1;margin:0;padding-left:12px;color:#555}</style></head><body>'
    + safeMd(md) + '<scr' + 'ipt>window.onload=function(){window.print();}<\/scr' + 'ipt></body></html>';
  const w = window.open("", "_blank");
  if (!w) { showHint("请允许弹出窗口以导出 PDF"); return; }
  w.document.write(html); w.document.close();
}

// 从报告文本中解析总分与结论
function parseScore(text) {
  const m = text.match(/总分[：:]\s*(\d{1,3})\s*\/\s*100/);
  const total = m ? parseInt(m[1], 10) : null;
  const v = text.match(/推荐结论[：:]\s*(通过|待定|不通过)/) || text.match(/(通过|待定|不通过)/);
  const verdict = v ? v[1] : null;
  return { total, verdict };
}

function scoreColor(total, verdict) {
  if (verdict === "不通过") return "bg-red-500/20 text-red-300";
  if (verdict === "待定") return "bg-amber-500/20 text-amber-300";
  if (verdict === "通过") return "bg-emerald-500/20 text-emerald-300";
  if (total != null) {
    if (total >= 80) return "bg-emerald-500/20 text-emerald-300";
    if (total >= 60) return "bg-amber-500/20 text-amber-300";
  }
  return "bg-white/10 text-slate-300";
}

// 在报告弹窗顶栏展示总分
function renderReportScore(text) {
  const { total, verdict } = parseScore(text);
  const bar = $("reportScoreBar");
  if (total == null) {
    bar.classList.add("hidden");
    return;
  }
  bar.classList.remove("hidden");
  const circle = $("scoreCircle");
  circle.textContent = total;
  circle.className =
    "w-16 h-16 rounded-2xl flex items-center justify-center text-2xl font-bold " + scoreColor(total, verdict);
  $("scoreVerdict").textContent = verdict ? "推荐：" + verdict : "";
}

function openReport() {
  const modal = $("reportModal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  const card = modal.querySelector(".modal-card");
  card.classList.remove("modal-anim"); void card.offsetWidth; card.classList.add("modal-anim");
}
$("closeReport").addEventListener("click", () => {
  $("reportModal").classList.add("hidden");
  $("reportModal").classList.remove("flex");
});

$("restartBtn").addEventListener("click", () => location.reload());

// ============ 历史面试 ============
function openHistory() {
  const modal = $("historyModal");
  modal.classList.remove("hidden");
  modal.classList.add("flex");
  const card = modal.querySelector(".modal-card");
  card.classList.remove("modal-anim"); void card.offsetWidth; card.classList.add("modal-anim");
  loadHistory();
}
function closeHistory() {
  const modal = $("historyModal");
  modal.classList.add("hidden");
  modal.classList.remove("flex");
}
$("historyBtn").addEventListener("click", openHistory);
$("historyBtnSetup").addEventListener("click", openHistory);
$("closeHistory").addEventListener("click", closeHistory);

// 报告导出
$("exportMdBtn").addEventListener("click", exportMarkdown);
$("exportPdfBtn").addEventListener("click", exportPDF);
$("closeReport2").addEventListener("click", () => {
  $("reportModal").classList.add("hidden");
  $("reportModal").classList.remove("flex");
});

$("expandEditorBtn").addEventListener("click", toggleEditorExpand);

function fmtDate(ts) {
  if (!ts) return "—";
  const d = new Date(ts * 1000);
  const p = (n) => String(n).padStart(2, "0");
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())} ${p(d.getHours())}:${p(d.getMinutes())}`;
}

async function loadHistory() {
  const list = $("historyList");
  list.innerHTML = '<div class="text-slate-500 text-sm">加载中…</div>';
  try {
    const res = await apiFetch("/api/history");
    const data = await res.json();
    const records = data.records || [];
    renderHistoryStats(records);
    renderMistakes();
    if (records.length === 0) {
      list.innerHTML = '<div class="text-slate-500 text-sm">暂无历史面试记录。完成一次面试后会出现在这里。</div>';
      return;
    }
    list.innerHTML = records.map((r) => {
      const badge = r.abandoned
        ? '<span class="text-xs px-2 py-0.5 rounded-full bg-white/10 text-slate-400">未完成</span>'
        : `<span class="text-xs px-2 py-0.5 rounded-full ${scoreColor(r.score, r.verdict)}">${r.score != null ? r.score + "分" : "—"}${r.verdict ? " · " + r.verdict : ""}</span>`;
      return `<div class="rounded-xl border border-white/5 bg-ink-700/40 p-4 flex items-center justify-between gap-3">
        <div class="min-w-0">
          <div class="font-medium text-sm truncate">${escapeHtml(r.persona_name || "面试官")} · ${escapeHtml(r.persona_title || "")}</div>
          <div class="text-xs text-slate-400 truncate">${escapeHtml(r.jd_title || "算法岗")}　|　${fmtDate(r.finished_at)}</div>
        </div>
        ${badge}
        <div class="flex items-center gap-2 shrink-0">
          <button class="hist-view text-xs px-3 py-1.5 rounded-lg bg-brand-500/90 hover:bg-brand-600 font-medium" data-id="${escapeHtml(r.id)}">查看</button>
          <button class="hist-del text-xs px-2.5 py-1.5 rounded-lg border border-white/10 hover:border-red-400 hover:text-red-300" data-id="${escapeHtml(r.id)}">删除</button>
        </div>
      </div>`;
    }).join("");
    list.querySelectorAll(".hist-view").forEach((b) =>
      b.addEventListener("click", () => viewHistoryDetail(b.dataset.id))
    );
    list.querySelectorAll(".hist-del").forEach((b) =>
      b.addEventListener("click", () => deleteHistory(b.dataset.id))
    );
  } catch (e) {
    list.innerHTML = '<div class="text-red-400 text-sm">加载失败：' + e.message + "</div>";
  }
}

// ============ 成长曲线与薄弱点画像 ============

// 纯 SVG 雷达图（零外部依赖，适配本机受限网络）：dims=[{name, avg}]，avg 取 0–5
function renderRadar(dims) {
  if (!dims.length) return '<div class="text-xs text-slate-500">暂无分项数据</div>';
  const n = dims.length;
  const size = 210, cx = size / 2, cy = size / 2, R = size / 2 - 30;
  const ang = (i) => -Math.PI / 2 + (i * 2 * Math.PI) / n;
  const pt = (i, r) => [cx + r * Math.cos(ang(i)), cy + r * Math.sin(ang(i))];
  let grid = "";
  [0.25, 0.5, 0.75, 1].forEach((f) => {
    const poly = dims.map((_, i) => pt(i, R * f).map((v) => v.toFixed(1)).join(",")).join(" ");
    grid += `<polygon points="${poly}" fill="none" stroke="#ffffff" stroke-opacity="0.10"/>`;
  });
  let axes = "";
  dims.forEach((d, i) => {
    const [x, y] = pt(i, R);
    axes += `<line x1="${cx}" y1="${cy}" x2="${x.toFixed(1)}" y2="${y.toFixed(1)}" stroke="#ffffff" stroke-opacity="0.14"/>`;
    const [lx, ly] = pt(i, R + 15);
    const anchor = Math.abs(lx - cx) < 6 ? "middle" : lx > cx ? "start" : "end";
    axes += `<text x="${lx.toFixed(1)}" y="${(ly + 3).toFixed(1)}" fill="#94a3b8" font-size="9" text-anchor="${anchor}">${escapeHtml(d.name)}</text>`;
  });
  const dataPts = dims.map((d, i) => pt(i, R * (d.avg / 5)).map((v) => v.toFixed(1)).join(",")).join(" ");
  const dots = dims.map((d, i) => {
    const [x, y] = pt(i, R * (d.avg / 5));
    return `<circle cx="${x.toFixed(1)}" cy="${y.toFixed(1)}" r="2.2" fill="#8ea2ff"/>`;
  }).join("");
  return `<svg width="${size}" height="${size}" viewBox="0 0 ${size} ${size}" class="block mx-auto">${grid}${axes}<polygon points="${dataPts}" fill="#4f6ef7" fill-opacity="0.22" stroke="#4f6ef7" stroke-width="1.5"/>${dots}</svg>`;
}

function renderHistoryStats(records) {
  const box = $("historyStats");
  const done = records.filter((r) => !r.abandoned && r.score != null).reverse(); // 按时间正序
  if (done.length < 1) { box.classList.add("hidden"); return; } // 首场即展示画像
  box.classList.remove("hidden");

  const scores = done.map((r) => r.score);
  const avg = Math.round(scores.reduce((a, b) => a + b, 0) / scores.length);
  const best = Math.max(...scores);
  const delta = scores[scores.length - 1] - scores[0];
  const deltaHtml = done.length < 2 || delta === 0 ? "—" : delta > 0
    ? `<span class="text-emerald-300">↑${delta}</span>`
    : `<span class="text-red-300">↓${-delta}</span>`;

  // 分数趋势折线（SVG sparkline，≥2 场才有趋势）
  let spark;
  if (done.length >= 2) {
    const W = 240, H = 44, P = 4;
    const lo = Math.min(...scores), hi = Math.max(...scores);
    const span = hi - lo || 1;
    const pts = scores.map((s, i) => {
      const x = P + (i * (W - 2 * P)) / Math.max(scores.length - 1, 1);
      const y = H - P - ((s - lo) * (H - 2 * P)) / span;
      return `${x.toFixed(1)},${y.toFixed(1)}`;
    });
    spark = `<svg width="${W}" height="${H}" class="block">
      <polyline points="${pts.join(" ")}" fill="none" stroke="#4f6ef7" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
      ${pts.map((p) => `<circle cx="${p.split(",")[0]}" cy="${p.split(",")[1]}" r="2.5" fill="#8ea2ff"/>`).join("")}
    </svg>`;
  } else {
    spark = `<div class="text-xs text-slate-500 h-[44px] flex items-center">再完成 1 场即可看到分数趋势</div>`;
  }

  // 各维度均分（来自报告分项评分）
  const dimSum = {}, dimCnt = {};
  done.forEach((r) => Object.entries(r.dimensions || {}).forEach(([k, v]) => {
    dimSum[k] = (dimSum[k] || 0) + v;
    dimCnt[k] = (dimCnt[k] || 0) + 1;
  }));
  const dims = Object.keys(dimSum).map((k) => ({ name: k, avg: dimSum[k] / dimCnt[k] }));
  dims.sort((a, b) => a.avg - b.avg);
  const radarSvg = renderRadar(dims);
  const dimHtml = dims.length
    ? `<div class="flex justify-center mb-2">${radarSvg}</div>` + dims.map((d) => `
    <div class="flex items-center gap-2 text-xs">
      <span class="w-24 truncate text-slate-400">${escapeHtml(d.name)}</span>
      <div class="flex-1 h-1.5 rounded-full bg-white/5 overflow-hidden">
        <div class="h-full rounded-full ${d.avg < 3 ? "bg-red-400/80" : d.avg < 4 ? "bg-amber-400/80" : "bg-emerald-400/80"}" style="width:${(d.avg / 5 * 100).toFixed(0)}%"></div>
      </div>
      <span class="w-8 text-right ${d.avg < 3 ? "text-red-300" : "text-slate-300"}">${d.avg.toFixed(1)}</span>
    </div>`).join("")
    : '<div class="text-xs text-slate-500">暂无分项数据</div>';

  // 薄弱标签（判题未通过题目的考察点）
  const tagCnt = {};
  done.forEach((r) => (r.weak_tags || []).forEach((t) => { tagCnt[t] = (tagCnt[t] || 0) + 1; }));
  const weak = Object.entries(tagCnt).sort((a, b) => b[1] - a[1]).slice(0, 5);
  const weakHtml = weak.length
    ? weak.map(([t, c]) => `<span class="px-2 py-0.5 rounded-full bg-red-500/10 text-red-300 border border-red-400/20">${escapeHtml(t)}${c > 1 ? " ×" + c : ""}</span>`).join(" ")
    : '<span class="text-slate-500">暂无（代码题全通过或未判题）</span>';

  box.innerHTML = `
    <div class="rounded-xl border border-white/5 bg-ink-700/40 p-4">
      <div class="flex items-center justify-between flex-wrap gap-4">
        <div class="flex items-center gap-5 text-sm">
          <div><div class="text-2xl font-bold">${done.length}</div><div class="text-xs text-slate-500">完成场次</div></div>
          <div><div class="text-2xl font-bold">${avg}</div><div class="text-xs text-slate-500">平均分</div></div>
          <div><div class="text-2xl font-bold">${best}</div><div class="text-xs text-slate-500">最高分</div></div>
          <div><div class="text-2xl font-bold">${deltaHtml || "—"}</div><div class="text-xs text-slate-500">首末场变化</div></div>
        </div>
        <div><div class="text-xs text-slate-500 mb-1">分数趋势</div>${spark}</div>
      </div>
      <div class="grid md:grid-cols-2 gap-4 mt-4 pt-3 border-t border-white/5">
        <div><div class="text-xs text-slate-500 mb-2">能力维度均分（低分靠前）</div><div class="space-y-1.5">${dimHtml}</div></div>
        <div><div class="text-xs text-slate-500 mb-2">代码薄弱点（判题未通过的考察标签）</div><div class="flex flex-wrap gap-1.5 text-xs">${weakHtml}</div></div>
      </div>
    </div>`;
}

async function viewHistoryDetail(id) {
  try {
    const res = await apiFetch("/api/history/" + id);
    const rec = await res.json();
    renderReportScore(rec.report || "");
    const transcriptHtml = rec.transcript
      ? `<details class="mt-4 pt-3 border-t border-white/5"><summary class="cursor-pointer text-slate-400 text-xs">查看完整对话记录</summary><pre class="mt-2 whitespace-pre-wrap text-xs text-slate-300 bg-ink-900/50 rounded-lg p-3">${escapeHtml(rec.transcript)}</pre></details>`
      : "";
    $("reportContent").innerHTML = safeMd(rec.report || "（无报告）") + transcriptHtml;
    state.report = rec.report || "";
    $("reportContent").scrollTop = 0;
    closeHistory();
    openReport();
  } catch (e) {
    alert("加载详情失败：" + e.message);
  }
}

async function deleteHistory(id) {
  if (!confirm("确定删除这条历史记录？")) return;
  try {
    await apiFetch("/api/history/" + id, { method: "DELETE" });
    loadHistory();
  } catch (e) {
    alert("删除失败：" + e.message);
  }
}

// ============ 错题本（定向练习入口）============
async function renderMistakes() {
  const box = $("mistakesBox");
  if (!box) return;
  // 始终展示容器：区分「空态 / 加载失败 / 列表」三种状态
  box.classList.remove("hidden");
  box.innerHTML = `
    <div class="rounded-xl border border-red-400/20 bg-red-500/5 p-4">
      <div class="flex items-center justify-between mb-2">
        <div class="text-sm font-medium text-red-200">📕 错题本（代码题未全通过，重练可消灭）</div>
        <span id="mistakeCount" class="text-xs text-slate-500"></span>
      </div>
      <div id="mistakeBody"><div class="text-xs text-slate-500">加载中…</div></div>
    </div>`;
  try {
    const res = await apiFetch("/api/mistakes");
    const data = await res.json();
    const ms = data.mistakes || [];
    const body = box.querySelector("#mistakeBody");
    const count = box.querySelector("#mistakeCount");
    count.textContent = ms.length ? ms.length + " 题待巩固" : "";
    if (ms.length === 0) {
      body.innerHTML = `<div class="mt-1 text-xs text-emerald-300/90">🎉 暂无错题，所有算法题都拿下了，继续保持！</div>`;
      return;
    }
    body.innerHTML = `<div class="space-y-2">${ms.map((m) => {
      if (m.type === "quiz" || !m.problem_id) {
        return `<div class="flex items-start justify-between gap-3 rounded-lg border border-white/5 bg-ink-700/40 px-3 py-2">
          <div class="min-w-0">
            <div class="text-[11px] px-1.5 py-0.5 rounded bg-amber-500/15 text-amber-200 w-fit mb-1">八股 · ${escapeHtml(m.direction || "通用")}</div>
            <div class="text-sm text-slate-200 leading-snug">${escapeHtml(m.question)}</div>
            <div class="text-xs text-slate-500 mt-0.5">累计错 ${m.wrong_times || 1} 次</div>
          </div>
          <div class="flex items-center gap-2 shrink-0">
            <button class="mistake-practice-quiz text-xs px-3 py-1.5 rounded-lg bg-brand-500/90 hover:bg-brand-600 font-medium" data-dir="${escapeHtml(m.direction || "")}">重练</button>
            <button class="mistake-del text-xs px-2.5 py-1.5 rounded-lg border border-white/10 hover:border-red-400 hover:text-red-300" data-id="${escapeHtml(m.question)}">移除</button>
          </div>
        </div>`;
      }
      return `<div class="flex items-center justify-between gap-3 rounded-lg border border-white/5 bg-ink-700/40 px-3 py-2">
        <div class="min-w-0">
          <div class="text-sm truncate">${escapeHtml(m.title)} <span class="text-xs text-slate-500">（${escapeHtml(m.difficulty)}）</span></div>
          <div class="text-xs text-slate-500">上次 ${m.last_passed}/${m.last_total} 通过　·　累计错 ${m.wrong_times} 次${m.tags && m.tags.length ? "　·　" + m.tags.map(escapeHtml).join("、") : ""}</div>
        </div>
        <div class="flex items-center gap-2 shrink-0">
          <button class="mistake-practice text-xs px-3 py-1.5 rounded-lg bg-brand-500/90 hover:bg-brand-600 font-medium" data-id="${escapeHtml(m.problem_id)}">重练</button>
          <button class="mistake-del text-xs px-2.5 py-1.5 rounded-lg border border-white/10 hover:border-red-400 hover:text-red-300" data-id="${escapeHtml(m.problem_id)}">移除</button>
        </div>
      </div>`;
    }).join("")}</div>`;
    body.querySelectorAll(".mistake-practice").forEach((b) =>
      b.addEventListener("click", () => startPractice(b.dataset.id)));
    body.querySelectorAll(".mistake-practice-quiz").forEach((b) =>
      b.addEventListener("click", () => startPracticeQuiz(b.dataset.dir)));
    body.querySelectorAll(".mistake-del").forEach((b) =>
      b.addEventListener("click", () => deleteMistake(b.dataset.id)));
  } catch (e) {
    const body = box.querySelector("#mistakeBody");
    body.innerHTML = `<button id="mistakeRetry" class="text-xs text-red-300 hover:text-red-200">⚠️ 错题本加载失败，点此重试</button>`;
    box.querySelector("#mistakeRetry").addEventListener("click", renderMistakes);
  }
}

async function startPractice(problemId) {
  closeHistory();
  selectMode("coding");
  $("difficultySelect").value = "";
  // 重练前校验该题仍在错题本，避免「已消灭/不存在」却静默开随机题
  let useId = problemId;
  try {
    const res = await apiFetch("/api/mistakes");
    const data = await res.json();
    const exists = (data.mistakes || []).some((m) => m.problem_id === problemId);
    if (!exists) {
      showHint("该题已消灭 / 不存在，已为你随机抽一道算法题");
      useId = "";
    }
  } catch (e) { /* 校验失败则按原 id 尝试，后端会兜底随机 */ }
  startInterview({ mode: "coding", problemId: useId });
}

async function startPracticeQuiz(direction) {
  closeHistory();
  selectMode("quiz");
  startInterview({ mode: "quiz", direction: direction || "" });
}

async function deleteMistake(problemId) {
  try {
    const res = await apiFetch("/api/mistakes/" + encodeURIComponent(problemId), { method: "DELETE" });
    if (!res.ok) throw new Error("bad");
    renderMistakes();
  } catch (e) {
    showHint("移除失败，请重试");
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}
