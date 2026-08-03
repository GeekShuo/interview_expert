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


def _safe_err(obj) -> str:
    """安全地格式化 SDK 错误对象。

    dashscope 部分错误结果（如 RecognitionResult）的 __str__ 自身会抛
    AttributeError；在 SDK 回调线程里直接 f-string 会炸掉接收线程，
    导致 ASR 静默死亡（真实事故）。因此任何字符串化都必须兜底。
    """
    if obj is None:
        return "unknown"
    try:
        msg = getattr(obj, "message", None)
        if msg:
            return str(msg)
    except Exception:
        pass
    try:
        return str(obj)
    except Exception:
        return f"<{type(obj).__name__}>"


class AliyunASR:
    """paraformer 流式实时识别。SDK 为同步回调模型，事件经 loop.call_soon_threadsafe 桥回异步侧。"""

    def __init__(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._rec = None
        self._loop: asyncio.AbstractEventLoop | None = None
        self._stopping = False   # 主动停止中：区分正常关闭与异常断连
        self._dead = False       # 已上报过断连，避免重复

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
                # 主动停止过程中的报错（如静音导致的 NO_VALID_AUDIO_ERROR）属预期，不上报
                if not outer._stopping:
                    outer._report_dead(f"阿里 ASR 错误: {_safe_err(message)}")

            def on_close(self, *_, **__):
                if not outer._stopping:
                    outer._report_dead("阿里 ASR 连接被关闭")

            def on_complete(self, *_, **__):
                # 识别任务被服务端结束（时长上限/长时间静音等）
                if not outer._stopping:
                    outer._report_dead("阿里 ASR 任务已结束")

        def _start():
            rec = Recognition(
                model=settings.ALIYUN_ASR_MODEL,
                format="pcm",
                sample_rate=16000,
                callback=_CB(),
            )
            rec.start()
            return rec

        # 有界等待：SDK 的 start 阻塞式等服务端 task-started，异常时可能长时间挂起
        self._rec = await asyncio.wait_for(asyncio.to_thread(_start), timeout=10)

    def _emit(self, cb, *args):
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(cb, *args)

    def _report_dead(self, reason: str):
        """断连只上报一次（错误回调/关闭回调/发送失败可能同时触发）。"""
        if not self._dead:
            self._dead = True
            self._emit(self._on_error, reason)

    def feed(self, pcm: bytes) -> None:
        if self._rec is not None and not self._dead:
            try:
                self._rec.send_audio_frame(pcm)
            except Exception:
                # 连接已断开：上报让管道层重连，而不是静默丢音频
                self._report_dead("阿里 ASR 音频发送失败")

    async def stop(self) -> None:
        self._stopping = True
        if self._rec is not None:
            rec, self._rec = self._rec, None
            try:
                # 有界等待：SDK 的 stop 会等服务端收尾，连接异常时可能长时间阻塞
                await asyncio.wait_for(asyncio.to_thread(rec.stop), timeout=5)
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
                q.put(Exception(f"阿里 TTS 错误: {_safe_err(message)}"))

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
