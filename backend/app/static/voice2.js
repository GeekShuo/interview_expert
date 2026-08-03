// ============ 云端语音客户端（ASR + TTS 全双工，WebSocket）============
// 上行：麦克风 PCM16/16k（AudioWorklet 采集，浏览器 AEC 回声消除）
// 下行：面试官 TTS PCM（采样率由服务端 ready 事件声明）+ 对话事件 JSON
// 打断：面试官说话期间本地检测到持续人声能量 → interrupt + 立即停播（barge-in）

class CloudVoiceClient {
  constructor() {
    this.cfg = null;            // /api/voice/config
    this.ws = null;
    this.connected = false;
    this._connecting = null;    // 进行中的 connect Promise（防重入）
    this.muted = false;
    this.paused = false;        // 用户主动暂停：面试冻结（不识别、不自动回复、停播 TTS）

    this.capCtx = null;         // 采集 AudioContext（16k）
    this.playCtx = null;        // 播放 AudioContext（设备默认率，自动重采样）
    this.stream = null;
    this.workletNode = null;

    this._nextT = 0;            // 播放队列下一个可调度时刻
    this._activeSources = new Set();
    this._loudFrames = 0;       // 连续高能量帧计数（打断判定）
    this._playSince = 0;        // 本轮播放起始时刻（起始 500ms 内不做打断判定，AEC 收敛期回声最强）
    this._lastInterrupt = 0;    // 上次打断时刻（打断后 1.2s 冷却，避免抖动连发）

    // 回调（由 app.js 赋值）
    this.onEvent = null;        // (ev) => {}  服务端 JSON 事件
    this.onStateChange = null;  // ("listening" | "speaking" | "off")
  }

  get playing() {
    return !!(this.playCtx && this._nextT > this.playCtx.currentTime + 0.03);
  }

  _setState(s) {
    if (this.onStateChange) this.onStateChange(s);
  }

  async detect() {
    try {
      // apiFetch 定义于 app.js（后加载，但 detect 均在用户交互后调用，全局可用）
      const r = await apiFetch("/api/voice/config");
      this.cfg = await r.json();
      return !!this.cfg.available;
    } catch (_) {
      return false;
    }
  }

  async connect(sessionId) {
    if (this.connected) return;
    if (this._connecting) return this._connecting;
    this._connecting = this._doConnect(sessionId);
    try {
      await this._connecting;
    } finally {
      this._connecting = null;
    }
  }

  async _doConnect(sessionId) {
    if (!navigator.mediaDevices || !navigator.mediaDevices.getUserMedia) {
      throw new Error("麦克风不可用：当前页面非安全上下文（需 https 或 localhost）");
    }
    // 1) WebSocket（token 经 query 传入，服务端建连即校验；WS 无法携带自定义头）
    const proto = location.protocol === "https:" ? "wss" : "ws";
    const token = localStorage.getItem("ie_token") || "";
    const ws = new WebSocket(`${proto}://${location.host}/ws/voice/${sessionId}?token=${encodeURIComponent(token)}`);
    ws.binaryType = "arraybuffer";
    ws.onmessage = (m) => {
      if (typeof m.data === "string") {
        let ev;
        try { ev = JSON.parse(m.data); } catch (_) { return; }
        if (ev.type === "interrupted") this.stopPlayback(); // 服务端判定的打断
        if (this.onEvent) this.onEvent(ev);
      } else {
        this._playChunk(m.data);
      }
    };
    ws.onclose = () => { this._teardown(); };
    await new Promise((resolve, reject) => {
      ws.onopen = resolve;
      ws.onerror = () => reject(new Error("语音通道连接失败"));
    });
    this.ws = ws;

    // 2) 麦克风（AEC 回声消除：面试官声音外放也不会被自己听回去）
    this.stream = await navigator.mediaDevices.getUserMedia({
      audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
    });
    const AC = window.AudioContext || window.webkitAudioContext;
    this.capCtx = new AC({ sampleRate: (this.cfg && this.cfg.asr_sample_rate) || 16000 });
    if (this.capCtx.state === "suspended") await this.capCtx.resume();
    await this.capCtx.audioWorklet.addModule("/static/pcm-worklet.js");
    const src = this.capCtx.createMediaStreamSource(this.stream);
    this.workletNode = new AudioWorkletNode(this.capCtx, "pcm-capture");
    this.workletNode.port.onmessage = (e) => this._onMicFrame(e.data);
    src.connect(this.workletNode);
    const muteGain = this.capCtx.createGain(); // worklet 需接入图才会运行，但不放音
    muteGain.gain.value = 0;
    this.workletNode.connect(muteGain);
    muteGain.connect(this.capCtx.destination);

    this.muted = false;
    this.connected = true;
    this._setState("listening");
  }

  _onMicFrame({ pcm, rms }) {
    // 本地打断判定（barge-in）：面试官音频播放中，检测到持续人声才触发——
    // 门槛：RMS>0.06 且持续 ~160ms（20 帧）以上，且避开播放起始 500ms 与打断后 1.2s 冷却，
    // 咳嗽/键盘声/呼吸声等短促小噪音不会误触发。
    if (this.playing) {
      const now = performance.now();
      const guarded = (this._playSince && now - this._playSince < 500) ||
                      (this._lastInterrupt && now - this._lastInterrupt < 1200);
      if (!guarded && rms > 0.06) {
        if (++this._loudFrames >= 20) {
          this._loudFrames = 0;
          this._lastInterrupt = now;
          this.interrupt();
        }
      } else if (rms <= 0.06) {
        this._loudFrames = 0;
      }
    }
    if (!this.muted && this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(pcm);
    }
  }

  _playChunk(buf) {
    const rate = (this.cfg && this.cfg.tts_sample_rate) || 24000;
    const AC = window.AudioContext || window.webkitAudioContext;
    if (!this.playCtx) this.playCtx = new AC();
    if (this.playCtx.state === "suspended") this.playCtx.resume();
    if (!this.playing) this._playSince = performance.now(); // 新一轮播放起始（打断保护窗口）
    const n = buf.byteLength >> 1;
    if (!n) return;
    const i16 = new Int16Array(buf);
    const f32 = new Float32Array(n);
    for (let i = 0; i < n; i++) f32[i] = i16[i] / 32768;
    const ab = this.playCtx.createBuffer(1, n, rate);
    ab.getChannelData(0).set(f32);
    const src = this.playCtx.createBufferSource();
    src.buffer = ab;
    src.connect(this.playCtx.destination);
    const t = Math.max(this.playCtx.currentTime + 0.02, this._nextT);
    src.start(t);
    this._nextT = t + ab.duration;
    this._activeSources.add(src);
    if (this._activeSources.size === 1) this._setState("speaking");
    src.onended = () => {
      this._activeSources.delete(src);
      if (this._activeSources.size === 0 && this.connected) this._setState("listening");
    };
  }

  stopPlayback() {
    this._activeSources.forEach((s) => { try { s.stop(); } catch (_) {} });
    this._activeSources.clear();
    this._nextT = 0;
    this._playSince = 0;
    if (this.connected) this._setState("listening");
  }

  interrupt() {
    this.stopPlayback(); // 先本地静音（<50ms 体感）
    this.send({ type: "interrupt" });
  }

  send(obj) {
    if (this.ws && this.ws.readyState === WebSocket.OPEN) {
      this.ws.send(JSON.stringify(obj));
    }
  }

  sendText(text) { this.send({ type: "text", text }); }
  sendOpening() { this.send({ type: "opening" }); }
  sendCode(code, language) { this.send({ type: "submit_code", code, language }); }

  toggleMute() {
    this.muted = !this.muted;
    return this.muted;
  }

  // 暂停面试：停麦 + 停播面试官声音 + 通知服务端冻结（已识别内容不发送、打断进行中轮次）
  pause() {
    if (!this.connected || this.paused) return;
    this.paused = true;
    this.muted = true;
    this._loudFrames = 0;
    this.stopPlayback();           // 本地立即静音（不等服务端回包）
    this.send({ type: "pause" });
    this._setState("paused");
  }

  // 继续面试：恢复聆听
  resume() {
    if (!this.connected || !this.paused) return;
    this.paused = false;
    this.muted = false;
    this.send({ type: "resume" });
    this._setState("listening");
  }

  _teardown() {
    this.connected = false;
    this.paused = false;
    this.stopPlayback();
    if (this.workletNode) { try { this.workletNode.disconnect(); } catch (_) {} this.workletNode = null; }
    if (this.stream) { this.stream.getTracks().forEach((t) => t.stop()); this.stream = null; }
    if (this.capCtx) { try { this.capCtx.close(); } catch (_) {} this.capCtx = null; }
    if (this.playCtx) { try { this.playCtx.close(); } catch (_) {} this.playCtx = null; }
    if (this.ws) { try { this.ws.close(); } catch (_) {} this.ws = null; }
    this._setState("off");
  }

  disconnect() {
    this._teardown();
  }
}

window.cloudVoice = new CloudVoiceClient();
