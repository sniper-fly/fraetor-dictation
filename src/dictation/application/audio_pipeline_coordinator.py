from __future__ import annotations

import asyncio
import time
from typing import TYPE_CHECKING

from dictation.application.segment_silence_monitor import SegmentSilenceMonitor

if TYPE_CHECKING:
    from collections.abc import Callable

    from dictation.domain.events import SttEvent
    from dictation.domain.ports import (
        AudioCapturePort,
        SegmentLifecycleHookPort,
        SpeechActivityDetectorPort,
        SttEnginePort,
    )


class AudioPipelineCoordinator:
    """STT/VADの起動停止と、録音コールバックのfanoutを制御する。

    セッション開始のたびに新しい STT エンジンインスタンスと専用の
    イベントキューを生成する (`stt_engine_factory` の呼び出しごとの都度 new)。
    セッションごとにキューを分離することで、キュー化された文字起こし
    ジョブの結果が別セッションの処理に混線することを防ぐ。

    無音区切りごとの逐次送信 (flush) もここで制御する。監視は
    `SegmentSilenceMonitor` に委ね、このクラスは「前回 flush 以降で
    まだ送信していない最初の発話開始位置を追跡する」ことと「flush を
    直列化する」ことを担う。

    VAD の `last_speech_start_sample` は「直近に検出した発話の開始位置」を
    常に上書きする値であり、「まだ送信していない発話区間の先頭」ではない。
    1回の flush 対象区間に (短いポーズを挟んだ) 複数の発話が含まれる場合、
    VAD の値をそのまま `trim_before_sample` に使うと最後の発話より前が
    誤って前方無音として削られてしまう。これを避けるため、「未送信区間の
    最初の発話開始位置」はコーディネーターが `_pending_speech_start_sample`
    として自前で追跡する。VAD 自身は発話区間検出のみに専念させる
    (「送信済み/未送信」という STT 側の概念をポートに持ち込まない)。
    """

    def __init__(
        self,
        audio_capture: AudioCapturePort,
        stt_engine_factory: Callable[[asyncio.Queue[SttEvent]], SttEnginePort],
        vad_factory: Callable[[], SpeechActivityDetectorPort],
        segment_silence_sec_fn: Callable[[], float],
        segment_lifecycle_hook: SegmentLifecycleHookPort | None = None,
    ) -> None:
        self._audio_capture = audio_capture
        self._stt_engine_factory = stt_engine_factory
        self._vad_factory = vad_factory
        self._segment_silence_sec_fn = segment_silence_sec_fn
        self._segment_lifecycle_hook = segment_lifecycle_hook
        self._stt_client: SttEnginePort | None = None
        self._event_queue: asyncio.Queue[SttEvent] | None = None
        self._vad: SpeechActivityDetectorPort | None = None
        self._session_start_time: float = time.monotonic()
        self._segment_monitor: SegmentSilenceMonitor | None = None
        # 前回 flush (または start()) 以降で、まだ送信していない最初の
        # 発話開始位置。`_on_audio_chunk` (音声コールバックスレッド) が
        # 発話開始を検出した直後に一度だけ書き込み、`_flush_segment`
        # (イベントループ) が読んでリセットする。
        self._pending_speech_start_sample: int | None = None
        # VAD から最後に取り込んだ発話開始位置。VAD の `last_speech_start_sample` は
        # 新しい発話が来るまで古い値を保持し続けるため、これと比較して「新しい発話か」を
        # 判定する。比較しないと flush 後の無音チャンクで古い値を拾い直し、無音のみの
        # 音声を STT へ繰り返し送ってしまう。
        self._last_seen_speech_start_sample: int | None = None
        # flush の HTTP 送信が並走すると、レスポンス順の揺れで
        # SegmentAccumulator が到着順に振るセグメント ID の順序が崩れる。
        # TranscriptionQueue がセッション間の順序を単一ワーカーで守るのと
        # 同じ考え方を、セッション内のセグメントに適用する。
        self._flush_lock = asyncio.Lock()

    @property
    def stt_client(self) -> SttEnginePort | None:
        return self._stt_client

    @property
    def event_queue(self) -> asyncio.Queue[SttEvent] | None:
        return self._event_queue

    def last_speech_time(self) -> float:
        """最後に発話を検知した時刻。VAD未起動時はセッション開始時刻を返す。"""
        return self._vad.last_speech_time if self._vad else self._session_start_time

    async def start(self) -> None:
        """STT接続 → マイクキャプチャ開始。失敗時は例外を伝播する。"""
        self._session_start_time = time.monotonic()
        self._pending_speech_start_sample = None
        self._last_seen_speech_start_sample = None
        if self._segment_lifecycle_hook:
            self._segment_lifecycle_hook.reset()
        self._event_queue = asyncio.Queue()
        self._stt_client = self._stt_engine_factory(self._event_queue)
        try:
            await self._stt_client.start()
        except Exception:
            self._stt_client = None
            self._event_queue = None
            raise
        self._vad = self._vad_factory()
        try:
            await self._audio_capture.start_recording(self._on_audio_chunk)
        except Exception:
            await self._stt_client.stop()
            self._stt_client = None
            self._event_queue = None
            self._vad = None
            raise
        # 無音区切り閾値はセッション開始時に1回だけ読む。セッション中に
        # 変更しても実行中の監視には反映しない (次のセッションから反映)。
        self._segment_monitor = SegmentSilenceMonitor(
            segment_silence_sec=self._segment_silence_sec_fn(),
            last_speech_time_fn=self.last_speech_time,
            on_silence=self._flush_segment,
        )
        self._segment_monitor.start()

    async def stop(self) -> tuple[SttEnginePort, asyncio.Queue[SttEvent]] | None:
        """録音を停止し、STTクライアントと専用イベントキューの所有権を返す。

        STT の stop() (文字起こし本体、重い処理) はここでは呼ばない。
        呼び出し元が `TranscriptionQueue` にジョブとして委譲する。残っている
        未送信区間は、その stop() が最終セグメントとして送信する。ただし未送信
        区間に新しい発話が無い (無音のみ) ときは、送っても認識結果が空で応答時間の
        分だけ完了が遅れるだけなので、STT に未送信分の破棄を指示してから返す。
        """
        await self._audio_capture.stop_recording()
        # 進行中の flush の完了を待ってから監視を止める。送信の途中で
        # キャンセルすると、STT の送信済みオフセットだけが進んでその区間の
        # テキストが失われる。待ち時間は flush 自身の `mai_timeout_sec` で
        # 上限が付く。
        async with self._flush_lock:
            if self._segment_monitor:
                await self._segment_monitor.stop()
                self._segment_monitor = None
            # 判断は _pending_speech_start_sample をリセットする前に行う。flush の
            # 完了を待った後 (ロック内) で見るのは、待っている間に flush が未送信の
            # 発話を消化した結果を反映するため。
            if self._stt_client and self._pending_speech_start_sample is None:
                self._stt_client.discard_pending_audio()

        stt_client = self._stt_client
        event_queue = self._event_queue
        self._stt_client = None
        self._event_queue = None
        self._vad = None
        self._pending_speech_start_sample = None
        self._last_seen_speech_start_sample = None

        if stt_client is None or event_queue is None:
            return None
        return stt_client, event_queue

    async def _flush_segment(self) -> None:
        """無音区切り時に、まだ送信していない最初の発話開始位置から送信する。

        `_pending_speech_start_sample` が `None` (前回 flush 以降、新しい
        発話が一度も検出されていない) なら送信せずに return する。
        `_on_audio_chunk` は VAD の検知結果に関わらずあらゆる音声チャンクを
        `feed_audio` に流すため、無音が続く間も STT クライアント側のバッファは
        (無音の) PCM で増え続ける。STT クライアント自身の no-op ガードは
        「新しいバイトがあるか」しか見ていないため、無音のみの区間でも
        新しいバイトは常に存在し、`if not pending` には引っかからない。
        「新しい発話があったか」を知っているのはこのコーディネーターだけ
        なので、ここで判定する必要がある。

        `trim_before_sample` の評価と `_pending_speech_start_sample` の
        リセットは HTTP 送信 (`await`) より前に行う。送信中に次の発話が
        始まっても、それは次回の flush 対象として正しく追跡されるように
        するため (送信中に読むと、その間に上書きされた値を拾ってしまう)。
        """
        stt_client = self._stt_client
        if stt_client is None or self._pending_speech_start_sample is None:
            return
        async with self._flush_lock:
            trim_before_sample = self._pending_speech_start_sample
            self._pending_speech_start_sample = None
            text = await stt_client.flush(trim_before_sample=trim_before_sample)
            if self._segment_lifecycle_hook:
                self._segment_lifecycle_hook.on_flush(produced_text=bool(text))

    def _on_audio_chunk(self, buffer: bytes) -> None:
        """PCMチャンクをSTTとVADの両方に転送する。

        VAD が発話開始を検出した直後、まだ未送信の発話開始位置を
        保持していなければ記録する (2件目以降の発話開始は無視する。
        「未送信区間の最初」を保持したいため上書きしない)。
        """
        if self._stt_client:
            self._stt_client.feed_audio(buffer)
        if self._vad:
            self._vad.feed(buffer)
            start = self._vad.last_speech_start_sample
            if start is not None and start != self._last_seen_speech_start_sample:
                self._last_seen_speech_start_sample = start
                if self._pending_speech_start_sample is None:
                    self._pending_speech_start_sample = start
                    if self._segment_lifecycle_hook:
                        self._segment_lifecycle_hook.on_speech_start()
