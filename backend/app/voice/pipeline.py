"""语音面试管道：单条 WS 连接的全双工编排。

数据流：
  浏览器麦克风 PCM ──WS二进制──▶ ASR ──partial/final──▶ 协调器
  协调器 ──文本轮次──▶ 现有 Session.stream_reply（复用状态机/判题/报告，全程不动）
       ──可见 token──▶ 前端渲染；同时分句 ─▶ TTS ──PCM──WS二进制──▶ 前端播放

打断（barge-in）：
  - 前端本地 VAD 检测到用户开口 → 发 {"type":"interrupt"}（播放端立即静音）；
  - 服务端 ASR 在面试官说话期间产出 final，同样视为打断；
  - 打断 = 取消当前 LLM 流（session 级 cancel 回调）+ 丢弃未合成句 + 通知前端清空播放队列。

协议（WS /ws/voice/{session_id}）：
  客户端 → 服务端：二进制=PCM16/16k 麦克风；文本 JSON=
      {"type":"opening"} 开场白 / {"type":"text","text":...} 文字走语音通道（TTS 回复）
      {"type":"submit_code","code":...,"language":...} / {"type":"interrupt"}
  服务端 → 客户端：二进制=TTS PCM（采样率见 ready 事件）；文本 JSON=
      ready / asr_partial / asr_final / turn_start / token(同SSE，含 report channel)
      stage / problem / judge / report_start / speak_start / speak_end
      interrupted / turn_end / error
"""
import asyncio
import json
import queue
import threading
from typing import Optional

from .. import session as sess
from .base import VoiceProvider, clean_for_tts, split_speakable


class VoiceSession:
    def __init__(self, ws, session: "sess.Session", provider: VoiceProvider):
        self.ws = ws
        self.sess = session
        self.provider = provider
        self.loop: Optional[asyncio.AbstractEventLoop] = None
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.outbox: asyncio.Queue = asyncio.Queue()
        self.asr = provider.create_asr(self._cb_partial, self._cb_final, self._cb_error)
        self.tts = provider.create_tts()
        self.cancel = threading.Event()
        self.turn_task: Optional[asyncio.Task] = None
        self._speaking = False
        self.closed = False

    # ---------- 入口 ----------
    async def run(self):
        self.loop = asyncio.get_running_loop()
        await self._send({
            "type": "ready",
            "provider": self.provider.name,
            "tts_rate": self.provider.tts_sample_rate,
            "asr_rate": 16000,
        })
        try:
            await self.asr.start()
        except Exception as e:
            # ASR 不可用不阻断连接：文字指令 + TTS 仍可用
            await self._send({"type": "error", "message": f"语音识别启动失败：{e}（仍可打字交流）"})
        sender = asyncio.create_task(self._sender())
        receiver = asyncio.create_task(self._recv_loop())
        try:
            await self._coordinate()
        finally:
            self.closed = True
            self.cancel.set()
            receiver.cancel()
            if self.turn_task and not self.turn_task.done():
                try:
                    await asyncio.wait_for(asyncio.shield(self.turn_task), timeout=2)
                except (asyncio.TimeoutError, asyncio.CancelledError):
                    pass
            try:
                await self.asr.stop()
            except Exception:
                pass
            try:
                await self.tts.close()
            except Exception:
                pass
            self.outbox.put_nowait(None)  # 让 sender 退出
            sender.cancel()

    # ---------- 发送（单 sender 串行化，保证音频帧与事件有序）----------
    async def _send(self, item):
        self.outbox.put_nowait(item)

    async def _sender(self):
        while True:
            item = await self.outbox.get()
            if item is None:
                return
            try:
                if isinstance(item, (bytes, bytearray)):
                    await self.ws.send_bytes(bytes(item))
                else:
                    await self.ws.send_text(json.dumps(item, ensure_ascii=False))
            except Exception:
                self.closed = True
                self.cancel.set()
                return

    # ---------- 接收 ----------
    async def _recv_loop(self):
        try:
            while not self.closed:
                msg = await self.ws.receive()
                if msg.get("type") == "websocket.disconnect":
                    break
                data = msg.get("bytes")
                if data is not None:
                    self.asr.feed(data)  # 协议要求 feed 线程安全
                    continue
                text = msg.get("text")
                if text:
                    try:
                        cmd = json.loads(text)
                    except ValueError:
                        continue
                    self.inbox.put_nowait(("cmd", cmd))
        except Exception:
            pass
        self.inbox.put_nowait(("cmd", {"type": "disconnect"}))

    # ---------- ASR 回调（可能被 provider 线程调用）----------
    def _cb_partial(self, text: str):
        self._ts_put(("asr_partial", text))

    def _cb_final(self, text: str):
        self._ts_put(("asr_final", text))

    def _cb_error(self, message: str):
        self._ts_put(("asr_error", message))

    def _ts_put(self, item):
        if self.loop and not self.loop.is_closed() and not self.closed:
            self.loop.call_soon_threadsafe(self.inbox.put_nowait, item)

    # ---------- 协调器 ----------
    async def _coordinate(self):
        while not self.closed:
            kind, payload = await self.inbox.get()
            if kind == "asr_partial":
                await self._send({"type": "asr_partial", "text": payload})
            elif kind == "asr_final":
                await self._send({"type": "asr_final", "text": payload})
                await self._start_turn("chat", payload)
            elif kind == "asr_error":
                await self._send({"type": "error", "message": payload})
            elif kind == "cmd":
                t = payload.get("type")
                if t == "disconnect":
                    break
                elif t == "interrupt":
                    await self._interrupt()
                elif t == "text":
                    text = (payload.get("text") or "").strip()
                    if text:
                        await self._start_turn("chat", text)
                elif t == "opening":
                    await self._start_turn("opening", None)
                elif t == "submit_code":
                    code = payload.get("code") or ""
                    lang = payload.get("language") or "python"
                    if code.strip():
                        await self._start_turn("code", (code, lang))

    # ---------- 轮次控制 ----------
    async def _start_turn(self, kind: str, payload):
        """开启新轮次；若当前轮次进行中，视为打断（barge-in）。"""
        if self.turn_task and not self.turn_task.done():
            await self._interrupt()
        self.turn_task = asyncio.create_task(self._turn(kind, payload))

    async def _interrupt(self):
        if not (self.turn_task and not self.turn_task.done()):
            await self._send({"type": "interrupted"})
            return
        self.cancel.set()
        await self._send({"type": "interrupted"})  # 前端立即清空播放队列
        try:
            # 等待当前轮次协作式退出（LLM 吐 token 的间隙生效，通常 <1s）
            await asyncio.wait_for(asyncio.shield(self.turn_task), timeout=8)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass

    # ---------- 轮次执行 ----------
    async def _turn(self, kind: str, payload):
        self.cancel.clear()
        self._speaking = False
        await self._send({"type": "turn_start", "kind": kind})

        q: "queue.Queue" = queue.Queue()

        def pump():
            """同步生成器在线程里跑（Session 锁 + 阻塞式 LLM 流都在线程内）。"""
            try:
                if kind == "chat":
                    gen = self.sess.stream_reply(payload, cancel=self.cancel.is_set)
                elif kind == "opening":
                    gen = self.sess.stream_reply(None, cancel=self.cancel.is_set)
                else:
                    gen = self.sess.stream_code_submission(payload[0], payload[1],
                                                         cancel=self.cancel.is_set)
                for ev in gen:
                    if self.cancel.is_set():
                        q.put(("cancelled", None))
                        return
                    q.put(("ev", ev))
            except Exception as e:
                q.put(("err", str(e)))
            q.put(("end", None))

        threading.Thread(target=pump, daemon=True).start()

        buf = ""
        interrupted = False
        try:
            while True:
                tag, val = await asyncio.to_thread(q.get)
                if tag == "end":
                    break
                if tag == "cancelled":
                    interrupted = True
                    break
                if tag == "err":
                    await self._send({"type": "error", "message": "面试官服务暂时不可用，请稍后重试"})
                    break
                ev = val
                if ev.get("type") == "token" and ev.get("channel") != "report":
                    await self._send(ev)  # 文字同步渲染
                    buf += ev.get("text", "")
                    segs, buf = split_speakable(buf)
                    for seg in segs:
                        if not await self._speak(seg):
                            interrupted = True
                            break
                    if interrupted:
                        break
                else:
                    # stage / problem / judge / report_start / report token 原样透传
                    await self._send(ev)
            if not interrupted:
                tail = buf.strip()
                if tail:
                    if not await self._speak(tail):
                        interrupted = True
        finally:
            if self._speaking:
                self._speaking = False
                await self._send({"type": "speak_end"})
            await self._send({"type": "turn_end", "interrupted": interrupted})

    async def _speak(self, text: str) -> bool:
        """合成一句并推流；返回 False 表示被打断。TTS 失败只报错，不阻断文字流。"""
        t = clean_for_tts(text)
        if not t:
            return True
        if self.cancel.is_set():
            return False
        if not self._speaking:
            self._speaking = True
            await self._send({"type": "speak_start"})
        try:
            async for pcm in self.tts.synth(t):
                if self.cancel.is_set():
                    return False
                await self._send(pcm)
        except Exception as e:
            await self._send({"type": "error", "message": f"语音合成失败：{e}（继续文字回复）"})
        return True
