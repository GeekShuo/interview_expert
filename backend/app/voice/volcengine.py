"""火山引擎（豆包语音）Provider：纯 WebSocket 实现，无第三方 SDK。

- TTS：豆包语音合成大模型，wss://openspeech.bytedance.com/api/v1/tts/ws_binary
  单连接复用，每句一个 reqid，提交文本后流式收 PCM 音频（首包 <300ms 级）。
- ASR：流式语音识别大模型 SAUC，wss://openspeech.bytedance.com/api/v3/sauc/bigmodel
  火山私有二进制协议（4 字节头 + gzip JSON/PCM），utterances.definite 作为判停定稿。

凭据：VOLC_APP_ID / VOLC_ACCESS_TOKEN（语音技术控制台开通「流式语音识别大模型」与
「语音合成大模型」后获取）。首次接入真实凭据联调时，如报文细节有出入，
以火山官方 demo 校准 _Sauc 的帧构造/解析即可，管道层无需改动。
"""
import asyncio
import gzip
import json
import struct
import uuid
from typing import AsyncIterator

import websockets

from ..config import settings
from .base import OnError, OnFinal, OnPartial

TTS_URL = "wss://openspeech.bytedance.com/api/v1/tts/ws_binary"
ASR_URL = "wss://openspeech.bytedance.com/api/v3/sauc/bigmodel"


async def _connect(url: str, headers: dict):
    """websockets 新旧版本头参数名兼容（>=15 用 additional_headers）。"""
    try:
        return await websockets.connect(url, additional_headers=headers, max_size=None)
    except TypeError:
        return await websockets.connect(url, extra_headers=headers, max_size=None)


# ---------- SAUC 二进制协议帧 ----------
class _Sauc:
    PROTOCOL_VERSION = 0b0001
    HEADER_SIZE = 0b0001          # 以 4 字节为单位：1 = 4 字节头
    CLIENT_FULL_REQUEST = 0b0001
    CLIENT_AUDIO_ONLY = 0b0010
    SERVER_FULL_RESPONSE = 0b1001
    SERVER_ERROR = 0b1111
    NO_SEQUENCE = 0b0000
    POS_SEQUENCE = 0b0001
    NEG_SEQUENCE = 0b0010         # 最后一包（sequence 取负）
    JSON = 0b0001
    GZIP = 0b0001

    @classmethod
    def _header(cls, msg_type: int, flags: int) -> bytes:
        return bytes([
            (cls.PROTOCOL_VERSION << 4) | cls.HEADER_SIZE,
            (msg_type << 4) | flags,
            (cls.JSON << 4) | cls.GZIP,
            0x00,
        ])

    @classmethod
    def request_frame(cls, payload: dict) -> bytes:
        body = gzip.compress(json.dumps(payload).encode("utf-8"))
        return cls._header(cls.CLIENT_FULL_REQUEST, cls.POS_SEQUENCE) \
            + struct.pack(">i", 1) + struct.pack(">I", len(body)) + body

    @classmethod
    def audio_frame(cls, pcm: bytes, seq: int, last: bool = False) -> bytes:
        body = gzip.compress(pcm) if pcm else b""
        flags = cls.NEG_SEQUENCE if last else cls.POS_SEQUENCE
        seq_val = -seq if last else seq
        return cls._header(cls.CLIENT_AUDIO_ONLY, flags) \
            + struct.pack(">i", seq_val) + struct.pack(">I", len(body)) + body

    @classmethod
    def parse(cls, frame: bytes) -> dict:
        """解析服务端帧 → {kind: response|error|ack, payload, is_last, code}"""
        if len(frame) < 4:
            return {"kind": "ack"}
        b0, b1, _b2, _b3 = frame[0], frame[1], frame[2], frame[3]
        header_size = (b0 & 0x0F) * 4
        msg_type = (b1 & 0xF0) >> 4
        flags = b1 & 0x0F
        offset = header_size
        is_last = flags == cls.NEG_SEQUENCE or flags == 0b0011
        if flags in (cls.POS_SEQUENCE, cls.NEG_SEQUENCE, 0b0011):
            offset += 4  # sequence，内容不需要
        if msg_type == cls.SERVER_ERROR:
            code = struct.unpack(">I", frame[offset:offset + 4])[0]
            size = struct.unpack(">I", frame[offset + 4:offset + 8])[0]
            payload = frame[offset + 8:offset + 8 + size]
            return {"kind": "error", "code": code,
                    "payload": _gunzip_json(payload), "is_last": True}
        if msg_type != cls.SERVER_FULL_RESPONSE:
            return {"kind": "ack"}
        size = struct.unpack(">I", frame[offset:offset + 4])[0]
        payload = frame[offset + 4:offset + 4 + size]
        return {"kind": "response", "payload": _gunzip_json(payload), "is_last": is_last}


def _gunzip_json(data: bytes) -> dict:
    try:
        return json.loads(gzip.decompress(data).decode("utf-8"))
    except Exception:
        try:
            return json.loads(data.decode("utf-8"))
        except Exception:
            return {}


# ---------- 流式 ASR（SAUC bigmodel）----------
class VolcASR:
    def __init__(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError):
        self._on_partial = on_partial
        self._on_final = on_final
        self._on_error = on_error
        self._ws = None
        self._seq = 1                     # full request 已用 1，音频从 2 开始
        self._buf = bytearray()           # 凑 100ms 包（16k*2B*0.1s = 3200B）
        self._send_task: asyncio.Task | None = None
        self._recv_task: asyncio.Task | None = None
        self._closed = False
        self._finalized = 0               # 已作为 final 上报的 utterance 数

    async def start(self) -> None:
        self._ws = await _connect(ASR_URL, {
            "X-Api-App-Key": settings.VOLC_APP_ID,
            "X-Api-Access-Key": settings.VOLC_ACCESS_TOKEN,
            "X-Api-Resource-Id": settings.VOLC_ASR_RESOURCE,
            "X-Api-Connect-Id": uuid.uuid4().hex,
        })
        full_req = {
            "user": {"uid": "interview-expert"},
            "audio": {"format": "pcm", "codec": "raw", "rate": 16000, "bits": 16, "channel": 1},
            "request": {
                "model_name": settings.VOLC_ASR_MODEL,
                "enable_punc": True,
                "enable_itn": True,
                "result_type": "single",
                "show_utterances": True,
            },
        }
        await self._ws.send(_Sauc.request_frame(full_req))
        self._send_task = asyncio.create_task(self._send_loop())
        self._recv_task = asyncio.create_task(self._recv_loop())

    def feed(self, pcm: bytes) -> None:
        if not self._closed:
            self._buf.extend(pcm)

    async def _send_loop(self):
        try:
            while not self._closed:
                if len(self._buf) >= 3200:
                    chunk = bytes(self._buf[:3200])
                    del self._buf[:3200]
                    self._seq += 1
                    await self._ws.send(_Sauc.audio_frame(chunk, self._seq))
                else:
                    await asyncio.sleep(0.03)
        except Exception as e:
            if not self._closed:
                self._on_error(f"火山 ASR 发送失败: {e}")

    async def _recv_loop(self):
        try:
            async for msg in self._ws:
                if isinstance(msg, str):
                    continue
                r = _Sauc.parse(msg)
                if r["kind"] == "error":
                    self._on_error(f"火山 ASR 错误 {r.get('code')}: {r.get('payload')}")
                    return
                if r["kind"] != "response":
                    continue
                result = (r.get("payload") or {}).get("result") or {}
                utts = result.get("utterances") or []
                # 定稿：definite 的 utterance 依次上报（判停信号）
                while self._finalized < len(utts) and utts[self._finalized].get("definite"):
                    text = (utts[self._finalized].get("text") or "").strip()
                    self._finalized += 1
                    if text:
                        self._on_final(text)
                # 中间结果：未定稿部分拼接展示
                rest = "".join(u.get("text", "") for u in utts[self._finalized:]).strip()
                if rest:
                    self._on_partial(rest)
                if r.get("is_last"):
                    return
        except Exception as e:
            if not self._closed:
                self._on_error(f"火山 ASR 接收失败: {e}")

    async def stop(self) -> None:
        self._closed = True
        try:
            if self._ws is not None:
                # 尾包：冲刷剩余音频 + NEG_SEQUENCE 结束标记
                if self._buf:
                    self._seq += 1
                    await self._ws.send(_Sauc.audio_frame(bytes(self._buf), self._seq))
                    self._buf.clear()
                await self._ws.send(_Sauc.audio_frame(b"", self._seq + 1, last=True))
        except Exception:
            pass
        for t in (self._send_task, self._recv_task):
            if t:
                t.cancel()
        try:
            if self._ws is not None:
                await self._ws.close()
        except Exception:
            pass


# ---------- 流式 TTS（豆包语音合成大模型，ws_binary）----------
class VolcTTS:
    def __init__(self):
        self.sample_rate = settings.VOLC_TTS_RATE
        self._ws = None
        self._lock = asyncio.Lock()  # 串行化逐句合成（同一连接按 reqid 顺序进行）

    async def _ensure_conn(self):
        if self._ws is not None:
            return
        self._ws = await _connect(TTS_URL, {
            "Authorization": f"Bearer;{settings.VOLC_ACCESS_TOKEN}",
        })

    async def synth(self, text: str) -> AsyncIterator[bytes]:
        async with self._lock:
            await self._ensure_conn()
            req = {
                "app": {
                    "appid": settings.VOLC_APP_ID,
                    "token": settings.VOLC_ACCESS_TOKEN,
                    "cluster": settings.VOLC_TTS_CLUSTER,
                },
                "user": {"uid": "interview-expert"},
                "audio": {
                    "voice_type": settings.VOLC_TTS_VOICE,
                    "encoding": "pcm",
                    "speed_ratio": 1.0,
                    "rate": self.sample_rate,
                },
                "request": {
                    "reqid": uuid.uuid4().hex,
                    "text": text,
                    "text_type": "plain",
                    "operation": "submit",
                },
            }
            await self._ws.send(json.dumps(req, ensure_ascii=False))
            while True:
                msg = await self._ws.recv()
                if isinstance(msg, (bytes, bytearray)):
                    yield bytes(msg)  # 裸 PCM 音频块
                    continue
                # 文本帧：状态/完成标记
                try:
                    res = json.loads(msg)
                except Exception:
                    continue
                code = res.get("code", 0)
                if code != 0:
                    raise RuntimeError(f"火山 TTS 错误 {code}: {res.get('message') or res}")
                if res.get("sequence", 0) < 0:  # 本句音频结束
                    break

    async def close(self) -> None:
        if self._ws is not None:
            ws, self._ws = self._ws, None
            try:
                await ws.close()
            except Exception:
                pass


class VolcProvider:
    name = "volcengine"
    tts_sample_rate = settings.VOLC_TTS_RATE

    def create_asr(self, on_partial: OnPartial, on_final: OnFinal, on_error: OnError) -> VolcASR:
        return VolcASR(on_partial, on_final, on_error)

    def create_tts(self) -> VolcTTS:
        return VolcTTS()
