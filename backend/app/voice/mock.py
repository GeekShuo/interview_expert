"""Mock 语音 Provider：无需任何密钥，用于本地联调语音管道。

- TTS：生成 440Hz 正弦波 PCM（时长随文本长度），验证「分句合成→音频流→前端播放」全链路。
- ASR：不识别音频（本地无声学模型）；语音输入请用 WS 的 {"type":"text"} 指令旁路，
  或配合浏览器原生语音识别（前端 voice.js 兜底）验证流程。
"""
import asyncio
import math
import struct
from typing import AsyncIterator

from .base import OnError, OnFinal, OnPartial


class MockASR:
    async def start(self) -> None:
        return None

    def feed(self, pcm: bytes) -> None:
        return None  # 丢弃音频，不产生识别结果

    async def stop(self) -> None:
        return None


class MockTTS:
    sample_rate = 16000

    async def synth(self, text: str) -> AsyncIterator[bytes]:
        # 时长：基础 0.5s + 每字 60ms，封顶 3s，模拟真实 TTS 的分句时长
        dur = min(0.5 + 0.06 * len(text), 3.0)
        total = int(self.sample_rate * dur)
        chunk = self.sample_rate // 10  # 每块 100ms
        produced = 0
        while produced < total:
            n = min(chunk, total - produced)
            frames = bytearray()
            for i in range(n):
                t = (produced + i) / self.sample_rate
                # 叠加一点包络，避免爆音
                env = min(1.0, (total - (produced + i)) / (self.sample_rate * 0.05))
                v = int(12000 * env * math.sin(2 * math.pi * 440 * t))
                frames += struct.pack("<h", v)
            produced += n
            yield bytes(frames)
            await asyncio.sleep(0.01)  # 模拟流式产出

    async def close(self) -> None:
        return None


class MockProvider:
    name = "mock"
    tts_sample_rate = MockTTS.sample_rate

    def create_asr(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError) -> MockASR:
        return MockASR()

    def create_tts(self) -> MockTTS:
        return MockTTS()
