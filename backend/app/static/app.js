// ============ 全局状态 ============
const state = {
  sessionId: null,
  stages: [],
  currentStage: null,
  editor: null,
  streaming: false,
};

const $ = (id) => document.getElementById(id);

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
    $("resumeText").value = data.text || "";
    $("resumeFileName").textContent = "✓ " + file.name;
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

  try {
    const res = await fetch("/api/start", { method: "POST", body: fd });
    const data = await res.json();
    state.sessionId = data.session_id;
    state.stages = data.stages;
    state.currentStage = data.stage;

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
  const curIdx = state.stages.findIndex((s) => s.key === state.currentStage);
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
  } else {
    $("codePane").classList.add("hidden");
    $("codePane").classList.remove("flex");
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
      const ev = JSON.parse(chunk.slice(5).trim());
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
  state.streaming = true;
  await streamSSE("/api/opening?session_id=" + state.sessionId, { method: "GET" }, {
    onToken: (t) => {
      acc += t;
      inner.innerHTML = marked.parse(acc);
      scrollBottom();
    },
    onStage: (ev) => setStage(ev.stage),
    onProblem: (p) => renderProblem(p),
    onReport: handleReportToken,
  });
  inner.parentElement.classList.remove("cursor-blink");
  state.streaming = false;
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
  $("userInput").value = "";
  autoGrow();
  addMessage("user").innerHTML = marked.parse(text);

  const inner = addMessage("assistant");
  inner.parentElement.classList.add("cursor-blink");
  let acc = "";
  state.streaming = true;
  $("sendBtn").disabled = true;
  await streamSSE("/api/chat", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, message: text }),
  }, {
    onToken: (t) => { acc += t; inner.innerHTML = marked.parse(acc); scrollBottom(); },
    onStage: (ev) => { addSystemNote("进入环节：" + ev.label); setStage(ev.stage); },
    onProblem: (p) => renderProblem(p),
    onReport: handleReportToken,
  });
  inner.parentElement.classList.remove("cursor-blink");
  state.streaming = false;
  $("sendBtn").disabled = false;
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
    });
  }
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
  state.streaming = true;
  await streamSSE("/api/submit_code", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ session_id: state.sessionId, code, language: lang }),
  }, {
    onToken: (t) => { acc += t; inner.innerHTML = marked.parse(acc); scrollBottom(); },
    onStage: (ev) => { addSystemNote("进入环节：" + ev.label); setStage(ev.stage); },
    onProblem: (p) => renderProblem(p),
    onReport: handleReportToken,
  });
  inner.parentElement.classList.remove("cursor-blink");
  state.streaming = false;
});

// ============ 报告 ============
let reportAcc = "";
function handleReportToken(text, start) {
  if (start) {
    reportAcc = "";
    openReport();
    return;
  }
  reportAcc += text;
  $("reportContent").innerHTML = marked.parse(reportAcc);
  const rc = $("reportContent");
  rc.scrollTop = rc.scrollHeight;
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
