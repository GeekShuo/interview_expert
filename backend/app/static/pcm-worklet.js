// 麦克风采集 AudioWorklet：Float32 → PCM16 LE，附带 RMS 能量（供本地打断检测）
class PCMCapture extends AudioWorkletProcessor {
  process(inputs) {
    const ch = inputs[0] && inputs[0][0];
    if (ch && ch.length) {
      const pcm = new Int16Array(ch.length);
      let sum = 0;
      for (let i = 0; i < ch.length; i++) {
        let s = Math.max(-1, Math.min(1, ch[i]));
        pcm[i] = s < 0 ? s * 32768 : s * 32767;
        sum += s * s;
      }
      const rms = Math.sqrt(sum / ch.length);
      this.port.postMessage({ pcm: pcm.buffer, rms }, [pcm.buffer]);
    }
    return true;
  }
}
registerProcessor("pcm-capture", PCMCapture);
