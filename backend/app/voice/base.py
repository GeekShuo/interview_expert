"""语音 Provider 抽象层。

设计目标：
- ASR / TTS 双接口解耦，阿里百炼 / 火山引擎 / mock 三个实现可互换（VOICE_PROVIDER 切换）。
- 音频格式约定：输入（候选人麦克风）PCM16 LE mono 16kHz；输出（面试官声音）PCM16 LE mono，
  采样率由 provider 声明（阿里 22050 / 火山 24000 / mock 16000），经 WS 二进制帧传输。
"""
from typing import AsyncIterator, Callable, Protocol

# ASR 回调：partial=流式中间结果（整句快照），final=一句说完的定稿，error=致命错误
OnPartial = Callable[[str], None]
OnFinal = Callable[[str], None]
OnError = Callable[[str], None]


class StreamingASR(Protocol):
    """流式语音识别：持续喂 PCM16/16k 音频，回调产出 partial/final 文本。"""

    async def start(self) -> None: ...
    def feed(self, pcm: bytes) -> None:
        """喂入 PCM16 LE mono 16kHz 音频块。要求线程安全（可能在 WS 接收线程调用）。"""
    async def stop(self) -> None: ...


class StreamingTTS(Protocol):
    """流式语音合成：按句合成，异步产出 PCM16 mono 音频块（采样率见 sample_rate）。"""

    sample_rate: int

    def synth(self, text: str) -> AsyncIterator[bytes]: ...
    async def close(self) -> None: ...


class VoiceProvider(Protocol):
    """语音供应商工厂：一套 ASR+TTS 组合。"""

    name: str
    tts_sample_rate: int

    def create_asr(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError) -> StreamingASR: ...
    def create_tts(self) -> StreamingTTS: ...


# ---------- 文本切句：LLM token 流按句喂给 TTS，边生成边合成 ----------

# 句尾强断点；逗号类弱断点仅在句子已足够长时才切（降低首句延迟，又不至于切得太碎）
_HARD_END = "。！？!?；;\n"
_SOFT_END = "，,：:"
_SOFT_MIN_LEN = 14


def split_speakable(buf: str) -> tuple[list[str], str]:
    """把缓冲文本切成「完整可合成句」+「剩余尾巴」。

    规则：遇到硬断点必切；软断点在已累积 >= _SOFT_MIN_LEN 字时切（让 TTS 更早出声）。
    """
    out: list[str] = []
    start = 0
    i = 0
    n = len(buf)
    while i < n:
        c = buf[i]
        if c in _HARD_END:
            seg = buf[start:i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        elif c in _SOFT_END and i + 1 - start >= _SOFT_MIN_LEN:
            seg = buf[start:i + 1].strip()
            if seg:
                out.append(seg)
            start = i + 1
        i += 1
    return out, buf[start:]


def clean_for_tts(text: str) -> str:
    """把 markdown 文本清理成适合朗读的纯文本（与前端 speakClean 对齐）。"""
    import re
    t = text or ""
    t = re.sub(r"```[\s\S]*?```", "，代码部分略过，", t)   # 代码块不朗读
    t = re.sub(r"`([^`]*)`", r"\1", t)                       # 行内代码保留文字（多为技术术语，应读出）
    t = re.sub(r"!\[.*?\]\(.*?\)", "", t)                    # 图片
    t = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", t)                # 链接保留文字
    t = re.sub(r"^[>#\-\*\+]\s?", "", t, flags=re.M)         # 引用/标题/列表符号
    t = re.sub(r"[*_~`>#]", "", t)                           # 残余 markdown 符号
    t = re.sub(r"\s+", " ", t)
    return t.strip()
