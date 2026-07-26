// ============ 语音模块（浏览器原生 Web Speech API）============
// ASR: SpeechRecognition（语音转文字）
// TTS: speechSynthesis（文字转语音）
// 依赖 Chrome / Edge。不支持时自动降级为纯文字。

// ---------- 文本清理：把 markdown 转成适合朗读的纯文本 ----------
function speakClean(s) {
  return (s || "")
    .replace(/```[\s\S]*?```/g, "，代码部分略过，")   // 代码块不朗读
    .replace(/`[^`]*`/g, "")                              // 行内代码
    .replace(/!\[.*?\]\(.*?\)/g, "")                       // 图片
    .replace(/\[(.*?)\]\(.*?\)/g, "$1")                    // 链接保留文字
    .replace(/^[>#\-\*\+]\s?/gm, "")                       // 引用/标题/列表符号
    .replace(/[*_~`>#]/g, "")                              // 残余 markdown 符号
    .replace(/\s+/g, " ")
    .trim();
}

// ---------- TTS：文字转语音（带按句队列）----------
class VoiceOutput {
  constructor() {
    this.supported = "speechSynthesis" in window;
    this.enabled = true;      // 是否朗读
    this.voice = null;
    this._pending = 0;        // 尚未读完的句子数
    this.onAllDone = null;    // 全部读完回调（用于闭环开麦）
    if (this.supported) this._pickVoice();
  }
  _pickVoice() {
    const pick = () => {
      const vs = window.speechSynthesis.getVoices() || [];
      this.voice =
        vs.find((v) => v.lang && v.lang.toLowerCase().startsWith("zh")) ||
        vs.find((v) => /chinese|mandarin|中文|普通话/i.test(v.name)) ||
        null;
    };
    pick();
    window.speechSynthesis.onvoiceschanged = pick;
  }
  speak(text) {
    if (!this.supported || !this.enabled) return;
    const t = (text || "").trim();
    if (!t) return;
    const u = new SpeechSynthesisUtterance(t);
    if (this.voice) u.voice = this.voice;
    u.lang = "zh-CN";
    u.rate = 1.05;
    u.pitch = 1.0;
    this._pending++;
    const done = () => {
      this._pending = Math.max(0, this._pending - 1);
      if (this._pending === 0 && this.onAllDone) {
        const cb = this.onAllDone;
        this.onAllDone = null;
        cb();
      }
    };
    u.onend = done;
    u.onerror = done;
    window.speechSynthesis.speak(u);
  }
  // 停止一切朗读
  cancel() {
    this.onAllDone = null;
    this._pending = 0;
    if (this.supported) window.speechSynthesis.cancel();
  }
}

// ---------- 按句切分朗读器：流式 token 累积时边生成边读 ----------
function makeSpeaker(voiceOut) {
  let spoken = 0;
  let last = "";
  const isEnd = (c) => "。！？；!?;\n".includes(c);
  return {
    // 传入当前累积的完整文本，自动朗读其中新出现的完整句子
    feed(full) {
      last = full;
      let idx = -1;
      for (let i = full.length - 1; i >= spoken; i--) {
        if (isEnd(full[i])) { idx = i; break; }
      }
      if (idx >= spoken) {
        const seg = full.slice(spoken, idx + 1);
        spoken = idx + 1;
        voiceOut.speak(speakClean(seg));
      }
    },
    // 流式结束：朗读剩余不足一句的尾巴，并在全部读完后回调
    finish(onDone) {
      if (spoken < last.length) {
        voiceOut.speak(speakClean(last.slice(spoken)));
        spoken = last.length;
      }
      if (onDone) {
        if (!voiceOut.supported || !voiceOut.enabled || voiceOut._pending <= 0) onDone();
        else voiceOut.onAllDone = onDone;
      }
    },
  };
}

// ---------- ASR：语音转文字（连续识别 + 静音自动停止）----------
class VoiceInput {
  constructor() {
    const SR = window.SpeechRecognition || window.webkitSpeechRecognition;
    this.supported = !!SR;
    this.listening = false;
    this.finalText = "";
    this.onUpdate = null;     // (finalText, interimText)
    this.onAutoStop = null;   // (finalText) 静音超时后自动回调
    this.onStart = null;
    this.onStop = null;
    this.silenceMs = 1600;    // 静音多久算说完
    this._timer = null;
    if (!this.supported) return;
    this.rec = new SR();
    this.rec.lang = "zh-CN";
    this.rec.continuous = true;
    this.rec.interimResults = true;
    this._bind();
  }
  _bind() {
    this.rec.onresult = (e) => {
      let interim = "";
      for (let i = e.resultIndex; i < e.results.length; i++) {
        const r = e.results[i];
        if (r.isFinal) this.finalText += r[0].transcript;
        else interim += r[0].transcript;
      }
      this.onUpdate && this.onUpdate(this.finalText, interim);
      this._resetSilence();
    };
    // Chrome 会不定时自动结束，若仍在听则续上
    this.rec.onend = () => {
      if (this.listening) {
        try { this.rec.start(); } catch (_) {}
      }
    };
    this.rec.onerror = () => { /* 忽略非致命错误，onend 会处理续接 */ };
  }
  start() {
    if (!this.supported || this.listening) return;
    this.finalText = "";
    this.listening = true;
    this.onStart && this.onStart();
    try { this.rec.start(); } catch (_) {}
  }
  stop() {
    if (!this.supported) return;
    this.listening = false;
    clearTimeout(this._timer);
    try { this.rec.stop(); } catch (_) {}
    this.onStop && this.onStop();
  }
  _resetSilence() {
    clearTimeout(this._timer);
    this._timer = setTimeout(() => {
      if (this.listening && this.finalText.trim()) {
        const txt = this.finalText.trim();
        this.stop();
        this.onAutoStop && this.onAutoStop(txt);
      }
    }, this.silenceMs);
  }
}

window.VoiceOutput = VoiceOutput;
window.VoiceInput = VoiceInput;
window.makeSpeaker = makeSpeaker;
