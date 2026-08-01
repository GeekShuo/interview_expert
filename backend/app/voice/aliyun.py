"""阿里百炼语音 Provider（dashscope SDK，懒加载）。

- ASR：Fun-ASR / paraformer-realtime-v2 流式实时识别（WebSocket 由 SDK 维护），
  首字延迟百毫秒级，中文+中英混说效果好，句尾自动判停（sentence_end）。
- TTS：CosyVoice 大模型流式合成，PCM 22050Hz mono 16bit，按句调用、音频流式回调。

依赖：pip install dashscope（仅在选择 aliyun 时才需要，见 pyproject 的 optional extra）。
密钥：DASHSCOPE_API_KEY。
"""
import asyncio
import queue
from typing import AsyncIterator

from ..config import settings
from .base import OnError, OnFinal, OnPartial


def _import_dashscope():
    try:
        import dashscope  # noqa: F401
        return dashscope
    except ImportError as e:
        raise RuntimeError(
            "未安装 dashscope。请在后端虚拟环境执行：uv pip install dashscope "
            "（或 pip install dashscope），或将 VOICE_PROVIDER 改为 volcengine。"
        ) from e


class AliyunASR:
    """paraformer 流式实时识别。SDK 为同步回调模型，事件经 loop.call_soon_threadsafe 桥回异步侧。"""

    def __init__(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._rec = None
        self._loop: asyncio.AbstractEventLoop | None = None

    async def start(self) -> None:
        self._loop = asyncio.get_running_loop()
        dashscope = _import_dashscope()
        dashscope.api_key = settings.DASHSCOPE_API_KEY
        from dashscope.audio.asr import Recognition, RecognitionCallback

        outer = self

        class _CB(RecognitionCallback):
            def on_event(self, result):  # SDK 线程回调
                try:
                    sentence = result.get_sentence() if hasattr(result, "get_sentence") else None
                    if not sentence:
                        return
                    text = (sentence.get("text") or "").strip()
                    if not text:
                        return
                    # paraformer-realtime-v2：sentence_end=True 表示一句判停定稿
                    is_end = bool(sentence.get("sentence_end"))
                    if not is_end and hasattr(result, "is_sentence_end"):
                        try:
                            is_end = bool(result.is_sentence_end(sentence))
                        except Exception:
                            is_end = False
                    if is_end:
                        outer._emit(outer._on_final, text)
                    else:
                        outer._emit(outer._on_partial, text)
                except Exception as e:  # 回调异常不能炸掉 SDK 线程
                    outer._emit(outer._on_error, f"ASR 结果解析失败: {e}")

            def on_error(self, message=None, **_):
                outer._emit(outer._on_error, f"阿里 ASR 错误: {message or 'unknown'}")

        def _start():
            rec = Recognition(
                model=settings.ALIYUN_ASR_MODEL,
                format="pcm",
                sample_rate=16000,
                callback=_CB(),
            )
            rec.start()
            return rec

        self._rec = await asyncio.to_thread(_start)

    def _emit(self, cb, *args):
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(cb, *args)

    def feed(self, pcm: bytes) -> None:
        if self._rec is not None:
            try:
                self._rec.send_audio_frame(pcm)
            except Exception:
                pass  # 连接已断开等情况由 on_error 兜底

    async def stop(self) -> None:
        if self._rec is not None:
            rec, self._rec = self._rec, None
            try:
                await asyncio.to_thread(rec.stop)
            except Exception:
                pass


class AliyunTTS:
    """CosyVoice 流式合成：按句调用，音频经回调流式产出（PCM 22050Hz mono 16bit）。"""

    sample_rate = 22050

    def __init__(self):
        self._closed = False

    async def synth(self, text: str) -> AsyncIterator[bytes]:
        dashscope = _import_dashscope()
        dashscope.api_key = settings.DASHSCOPE_API_KEY
        from dashscope.audio.tts_v2 import SpeechSynthesizer, ResultCallback

        q: "queue.Queue[bytes | Exception | None]" = queue.Queue()

        class _CB(ResultCallback):
            def on_data(self, data: bytes, **_):
                q.put(data)

            def on_complete(self):
                q.put(None)

            def on_error(self, message=None, **_):
                q.put(Exception(f"阿里 TTS 错误: {message or 'unknown'}"))

        def _run():
            kwargs = dict(
                model=settings.ALIYUN_TTS_MODEL,
                voice=settings.ALIYUN_TTS_VOICE,
                callback=_CB(),
            )
            # PCM 输出（前端按裸 PCM 流式播放）；新版 SDK 枚举为 AudioFormat，
            # 旧版为 SpeechSynthesisAudioFormat，做双版本兼容
            fmt = None
            try:
                from dashscope.audio.tts_v2 import AudioFormat
                fmt = getattr(AudioFormat, "PCM_22050HZ_MONO_16BIT", None)
            except ImportError:
                try:
                    from dashscope.audio.tts_v2 import SpeechSynthesisAudioFormat
                    fmt = getattr(SpeechSynthesisAudioFormat, "PCM_22050HZ_MONO_16BIT", None)
                except ImportError:
                    pass
            if fmt is not None:
                kwargs["format"] = fmt
            synth = SpeechSynthesizer(**kwargs)
            synth.call(text)

        task = asyncio.create_task(asyncio.to_thread(_run))
        try:
            while True:
                item = await asyncio.to_thread(q.get)
                if item is None:
                    break
                if isinstance(item, Exception):
                    raise item
                yield item
        finally:
            if not task.done():
                task.cancel()  # 打断时放弃本句剩余合成（SDK 调用无法硬中断，结果丢弃即可）

    async def close(self) -> None:
        self._closed = True


class AliyunProvider:
    name = "aliyun"
    tts_sample_rate = AliyunTTS.sample_rate

    def create_asr(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError) -> AliyunASR:
        return AliyunASR(on_partial, on_final, on_error)

    def create_tts(self) -> AliyunTTS:
        return AliyunTTS()
