"""バッチ型STTエンジンの共通基底。

バッファ・送信済みオフセット・ロック・WAV化・タイムアウト・例外の握り潰し・
キュー投入・応答時間の通知が共通で、SDK 呼び出し (`_transcribe_sync`) だけが
エンジン固有になる。
"""

from __future__ import annotations

import asyncio
import io
import logging
import threading
import time
import wave
from abc import abstractmethod
from typing import TYPE_CHECKING

from dictation.domain.events import RecognizedEvent
from dictation.domain.ports import SttCapabilities, SttEnginePort

if TYPE_CHECKING:
    from dictation.domain.events import SttEvent
    from dictation.domain.ports import SttLatencyHookPort

logger = logging.getLogger(__name__)

_CAPABILITIES = SttCapabilities(streaming=False, post_processing=True)
_PCM_SAMPLE_WIDTH_BYTES = 2  # 16-bit
_PCM_CHANNELS = 1
_WAV_HEADER_BYTES = 44


class BatchWavSttEngine(SttEnginePort):
    """WAV を1リクエストで送るバッチ型エンジンの共通実装。

    feed_audio で蓄積した PCM を、無音区切りごとの `flush()` と最終の `stop()`
    で WAV 化して送信する。結果は送信1回ごとに recognized イベントとして
    queue へ投入する (1セッションで複数件になる)。

    セッション全体の生 PCM を `_buffer` に保持し続け、どこまで送ったかを
    `_sent_offset_bytes` で覚える方式を採る。「送信済みの分を捨てる」形に
    しないのは、無音区切りの直前で語頭が欠けた場合に前の区間へさかのぼって
    切り出せる余地を残すため。バッファは `stop()` で必ず解放され、1セッション
    上限 (10分/16kHz/16bit/mono ≒ 19MB) を超えて増えることはない。
    """

    def __init__(
        self,
        stt_event_queue: asyncio.Queue[SttEvent],
        *,
        timeout_sec: float,
        sample_rate: int,
        latency_hook: SttLatencyHookPort | None = None,
    ) -> None:
        super().__init__(stt_event_queue)
        self._buffer = bytearray()
        self._sent_offset_bytes = 0
        # feed_audio は sounddevice のコールバックスレッドから、flush/stop は
        # イベントループから呼ばれる。バッファとオフセットの整合を守るために
        # メモリ操作だけを排他する (WAV化・HTTP送信はロックの外)。
        self._lock = threading.Lock()
        self._timeout_sec = timeout_sec
        self._sample_rate = sample_rate
        self._latency_hook = latency_hook

    @property
    @abstractmethod
    def engine_name(self) -> str:
        """ログと応答時間の通知に使うエンジン名。"""

    @abstractmethod
    def _transcribe_sync(self, wav_bytes: bytes) -> str:
        """WAV を送信して認識結果テキストを返す (別スレッドで呼ばれる)。"""

    @property
    def capabilities(self) -> SttCapabilities:
        return _CAPABILITIES

    async def start(self) -> None:
        with self._lock:
            self._buffer.clear()
            self._sent_offset_bytes = 0
        logger.info("%s started (buffering)", self.engine_name)

    def feed_audio(self, buffer: bytes) -> None:
        with self._lock:
            self._buffer.extend(buffer)

    def discard_pending_audio(self) -> None:
        """未送信の音声を破棄する。送信済み位置を現在のバッファ末尾まで進めるだけで、
        送信済みの区間は消さない (さかのぼり切り出しの余地を残す方針を保つ)。"""
        with self._lock:
            self._sent_offset_bytes = len(self._buffer)

    async def flush(self, *, trim_before_sample: int | None) -> str:
        """未送信区間を1セグメントとして送信する。バッファは解放しない。"""
        wav_bytes = self._extract_pending_wav(trim_before_sample)
        if not wav_bytes:
            return ""
        text = await self._transcribe(wav_bytes, phase="flush")
        logger.info("%s flushed (chars=%d)", self.engine_name, len(text or ""))
        return text

    async def stop(self) -> None:
        # 残っている未送信分をすべて最終セグメントとして送る。無音区切りが
        # 一度も発生しなかったセッションでは、これが唯一の送信になる。
        wav_bytes = self._extract_pending_wav(trim_before_sample=None)
        with self._lock:
            self._buffer.clear()
            self._sent_offset_bytes = 0
        if not wav_bytes:
            logger.info("%s stopped (no audio)", self.engine_name)
            return
        text = await self._transcribe(wav_bytes, phase="stop")
        logger.info("%s stopped (chars=%d)", self.engine_name, len(text or ""))

    async def _transcribe(self, wav_bytes: bytes, *, phase: str) -> str:
        """WAV を送信し、テキストが得られれば recognized イベントを投入する。

        失敗はログに記録して握り潰す (プラン: リトライなし、テキストは失う)。
        呼び出し元は録音・後続セグメントの処理を継続する。
        """
        started = time.monotonic()
        succeeded = True
        text = ""
        try:
            text = await asyncio.wait_for(
                asyncio.to_thread(self._transcribe_sync, wav_bytes),
                timeout=self._timeout_sec,
            )
        except Exception:
            succeeded = False
            logger.exception("%s %s failed", self.engine_name, phase)
        self._notify_latency(
            phase=phase,
            duration_sec=time.monotonic() - started,
            wav_bytes=wav_bytes,
            succeeded=succeeded,
        )
        if text:
            self._queue.put_nowait(RecognizedEvent(text=text))
        return text

    def _notify_latency(
        self,
        *,
        phase: str,
        duration_sec: float,
        wav_bytes: bytes,
        succeeded: bool,
    ) -> None:
        if self._latency_hook is None:
            return
        pcm_bytes = max(len(wav_bytes) - _WAV_HEADER_BYTES, 0)
        audio_duration_sec = pcm_bytes / _PCM_SAMPLE_WIDTH_BYTES / self._sample_rate
        try:
            self._latency_hook.on_stt_completed(
                engine=self.engine_name,
                phase=phase,
                duration_sec=duration_sec,
                audio_duration_sec=audio_duration_sec,
                succeeded=succeeded,
            )
        except Exception:
            logger.exception("STT latency hook failed")

    def _extract_pending_wav(self, trim_before_sample: int | None) -> bytes:
        """未送信区間を切り出して WAV 化し、送信済みオフセットを進める。

        `trim_before_sample` が未送信区間の途中を指す場合はそこまでの無音を
        捨てる。既に送信済みの位置より前を指す場合 (発話が前の区間で始まって
        いた場合) は再送しないよう送信済み位置を優先する。
        """
        with self._lock:
            start = self._sent_offset_bytes
            if trim_before_sample is not None:
                start = max(start, trim_before_sample * _PCM_SAMPLE_WIDTH_BYTES)
            pending = bytes(self._buffer[start:])
            self._sent_offset_bytes = len(self._buffer)
        if not pending:
            return b""
        return self._build_wav(pending)

    def _build_wav(self, pcm_bytes: bytes) -> bytes:
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(_PCM_CHANNELS)
            wf.setsampwidth(_PCM_SAMPLE_WIDTH_BYTES)
            wf.setframerate(self._sample_rate)
            wf.writeframes(pcm_bytes)
        return bio.getvalue()
