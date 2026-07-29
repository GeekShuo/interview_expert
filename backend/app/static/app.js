// ============ 全局状态 ============
const state = {
  sessionId: null,
  stages: [],
  currentStage: null,
  editor: null,
  streaming: false,
};

const $ = (id) => document.getElementById(id);

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
$("voiceModeToggle").addEventListener("change", (e) => {
  voiceMode = e.target.checked;
  voiceOut.enabled = true;
  if (!voiceIn.supported && voiceMode) {
    showToast("当前浏览器不支持语音识别，请使用 Chrome 或 Edge");
  }
  if (!voiceMode) {
    voiceOut.cancel();
    voiceIn.stop();
  }
});

// 麦克风按钮：手动开/关（关闭时若已识别到内容则发送）
$("micBtn").addEventListener("click", () => {
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

// 语音模式下：面试官回复读完后自动开麦听用户
function startListeningTurn() {
  if (!voiceMode || !voiceIn.supported) return;
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
    const res = await fetch("/api/upload_resume", { method: "POST", body: fd });
    const data = await res.json();
    const text = data.text || "";
    if (!text || text.startsWith("[") ) {
      // 解析失败或未提取到文本（如畸形 PDF / 扫描件）
      $("resumeFileName").textContent = "⚠️ 未提取到文本，请改用粘贴";
      if (text) $("resumeText").value = text; // 保留错误信息供参考
    } else {
      $("resumeText").value = text;
      $("resumeFileName").textContent = "✓ " + file.name;
    }
  } catch {
    $("resumeFileName").textContent = "解析失败，请改用粘贴";
  }
});

$("startBtn").addEventListener("click", startInterview);

async function startInterview() {
  const resumeText = $("resumeText").value.trim();
  const jdText = $("jdText").value.trim();
  if (!jdText) {
    showHint("请至少填写目标岗位 JD");
    return;
  }
  $("startBtn").disabled = true;
  $("startBtn").textContent = "正在匹配面试官…";

  const fd = new FormData();
  fd.append("resume_text", resumeText);
  fd.append("jd_text", jdText);
  // 若之前有进行中的会话，作为 previous 传回，后端会将其标记为「未完成」并落盘
  const prev = sessionStorage.getItem("lastSessionId");
  if (prev) fd.append("previous_session_id", prev);

  try {
    const res = await fetch("/api/start", { method: "POST", body: fd });
    const data = await res.json();
    state.sessionId = data.session_id;
    state.stages = data.stages;
    state.currentStage = data.stage;
    sessionStorage.setItem("lastSessionId", data.session_id);

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
    openReport();
  }
}

// ============ 消息渲染 ============
function addMessage(role) {
  const wrap = document.createElement("div");
  wrap.className = "flex " + (role === "user" ? "justify-end" : "justify-start");
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
  const html = looksLikeCode ? marked.parse("```\n" + text + "\n```") : marked.parse(text);
  addMessage("user").innerHTML = html;
}

function scrollBottom() {
  const m = $("messages");
  m.scrollTop = m.scrollHeight;
}

// ============ SSE 流式核心 ============
async function streamSSE(url, options, { onToken, onReport, onStage, onProblem }) {
  const res = await fetch(url, options);
  const reader = res.body.getReader();
  const decoder = new TextDecoder();
  let buf = "";
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
      } else if (ev.type === "report_start") {
        onReport && onReport("", true);
      } else if (ev.type === "error") {
        onToken && onToken("\n\n[出错] " + ev.message);
      }
    }
  }
}

// 开场白
async function streamOpening() {
  const inner = addMessage("assistant");
  inner.parentElement.classList.add("cursor-blink");
  let acc = "";
  const spk = makeSpeaker(voiceOut);
  state.streaming = true;
  try {
    await streamSSE("/api/opening?session_id=" + state.sessionId, { method: "GET" }, {
      onToken: (t) => {
        acc += t;
        inner.innerHTML = marked.parse(acc);
        if (voiceMode) spk.feed(acc);
        scrollBottom();
      },
      onStage: (ev) => setStage(ev.stage),
      onProblem: (p) => renderProblem(p),
      onReport: handleReportToken,
    });
  } catch (e) {
    inner.innerHTML = marked.parse(acc + "\n\n[连接出错] " + (e && e.message ? e.message : e));
  } finally {
    inner.parentElement.classList.remove("cursor-blink");
    state.streaming = false;
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

async function sendMessage() {
  const text = $("userInput").value.trim();
  if (!text || state.streaming) return;
  if (state.currentStage === "finished") {
    addSystemNote("面试已结束，点击「重新开始」可再开一场");
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
  $("sendBtn").disabled = true;
  try {
    await streamSSE("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ session_id: state.sessionId, message: text }),
    }, {
      onToken: (t) => { acc += t; inner.innerHTML = marked.parse(acc); if (voiceMode) spk.feed(acc); scrollBottom(); },
      onStage: (ev) => { addSystemNote("进入环节：" + ev.label); setStage(ev.stage); },
      onProblem: (p) => renderProblem(p),
      onReport: handleReportToken,
    });
  } catch (e) {
    inner.innerHTML = marked.parse(acc + "\n\n[连接出错] " + (e && e.message ? e.message : e));
  } finally {
    inner.parentElement.classList.remove("cursor-blink");
    state.streaming = false;
    $("sendBtn").disabled = false;
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
        value: "# 在这里编写你的解法\n",
        language: "python",
        theme: "vs-dark",
        fontSize: 14,
        minimap: { enabled: false },
        automaticLayout: true,
        scrollBeyondLastLine: false,
      });
      focusEditor();
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
  if (state.editor) monaco.editor.setModelLanguage(state.editor.getModel(), e.target.value);
});

function renderProblem(p) {
  $("problemTitle").textContent = `${p.title}（${p.difficulty}）`;
  showCodePane();
  const starter = {
    python: "# 在这里编写你的解法\n",
    cpp: "// 在这里编写你的解法\n",
    java: "// 在这里编写你的解法\n",
    javascript: "// 在这里编写你的解法\n",
  };
  const lang = $("langSelect").value;
  if (state.editor) state.editor.setValue(starter[lang] || "");
}

$("submitCodeBtn").addEventListener("click", async () => {
  if (!state.editor || state.streaming) return;
  const code = state.editor.getValue();
  const lang = $("langSelect").value;
  addMessage("user").innerHTML = marked.parse("已提交代码：\n```" + lang + "\n" + code + "\n```");

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
      onToken: (t) => { acc += t; inner.innerHTML = marked.parse(acc); if (voiceMode) spk.feed(acc); scrollBottom(); },
      onStage: (ev) => { addSystemNote("进入环节：" + ev.label); setStage(ev.stage); },
      onProblem: (p) => renderProblem(p),
      onReport: handleReportToken,
    });
  } catch (e) {
    inner.innerHTML = marked.parse(acc + "\n\n[连接出错] " + (e && e.message ? e.message : e));
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
  $("reportContent").innerHTML = marked.parse(reportAcc);
  renderReportScore(reportAcc);
  const rc = $("reportContent");
  rc.scrollTop = rc.scrollHeight;
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
  $("reportModal").classList.remove("hidden");
  $("reportModal").classList.add("flex");
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
    const res = await fetch("/api/history");
    const data = await res.json();
    const records = data.records || [];
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
          <div class="font-medium text-sm truncate">${r.persona_name || "面试官"} · ${r.persona_title || ""}</div>
          <div class="text-xs text-slate-400 truncate">${r.jd_title || "算法岗"}　|　${fmtDate(r.finished_at)}</div>
        </div>
        ${badge}
        <div class="flex items-center gap-2 shrink-0">
          <button class="hist-view text-xs px-3 py-1.5 rounded-lg bg-brand-500/90 hover:bg-brand-600 font-medium" data-id="${r.id}">查看</button>
          <button class="hist-del text-xs px-2.5 py-1.5 rounded-lg border border-white/10 hover:border-red-400 hover:text-red-300" data-id="${r.id}">删除</button>
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

async function viewHistoryDetail(id) {
  try {
    const res = await fetch("/api/history/" + id);
    const rec = await res.json();
    renderReportScore(rec.report || "");
    const transcriptHtml = rec.transcript
      ? `<details class="mt-4 pt-3 border-t border-white/5"><summary class="cursor-pointer text-slate-400 text-xs">查看完整对话记录</summary><pre class="mt-2 whitespace-pre-wrap text-xs text-slate-300 bg-ink-900/50 rounded-lg p-3">${escapeHtml(rec.transcript)}</pre></details>`
      : "";
    $("reportContent").innerHTML = marked.parse(rec.report || "（无报告）") + transcriptHtml;
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
    await fetch("/api/history/" + id, { method: "DELETE" });
    loadHistory();
  } catch (e) {
    alert("删除失败：" + e.message);
  }
}

function escapeHtml(s) {
  return (s || "").replace(/[&<>]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
}
