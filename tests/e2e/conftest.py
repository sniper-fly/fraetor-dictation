"""実音声・実STTを使うE2Eテストの共通部品。

HTTPサーバーもプロセス起動も使わず、`RecordingSessionService` をプロセス内で
直接組み立てて動かす。音声は `FileAudioCapture` でWAVを実時間再生し、VADは
実物のSilero、STTは実クラウドのMAI Transcribeを使う。

音声は本人の声を含むため公開リポジトリに置かない。次の環境変数で受け取り、
未設定の環境 (公開CI等) ではこのディレクトリのテストをスキップする。

- `FRAETOR_DICTATION_AUDIO_DIR`: WAV (16kHz/16bit/mono) を置いたフォルダ
- `FRAETOR_DICTATION_MAI_ENDPOINT` / `FRAETOR_DICTATION_MAI_API_KEY`
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from dictation import (
    AppState,
    AudioPipelineCoordinator,
    FileAudioCapture,
    RecordingSessionService,
    SegmentAccumulator,
    SSEBroadcaster,
    SttEventRelay,
    TranscriptionQueue,
    create_stt_engine,
    create_vad,
)
from tests.fakes import NoOpSessionFinalizationHook

if TYPE_CHECKING:
    from collections.abc import Callable

    from dictation import AudioCapturePort, SpeechActivityDetectorPort, SttEnginePort
    from dictation.domain.events import SttEvent

_ENV_AUDIO_DIR = "FRAETOR_DICTATION_AUDIO_DIR"
_ENV_ENDPOINT = "FRAETOR_DICTATION_MAI_ENDPOINT"
_ENV_API_KEY = "FRAETOR_DICTATION_MAI_API_KEY"

_SAMPLE_RATE = 16000
# 既定値は、短い音声 (最長12秒) の1セッションが数十秒で終わるように選ぶ。
_DEFAULTS: dict[str, float] = {
    "max_session_duration_sec": 20,
    "silence_timeout_sec": 3,
    "segment_silence_sec": 1.0,
    "mai_timeout_sec": 60,
    "vad_threshold": 0.5,
}
# 通常の短い音声向けの、session_end を待つ上限。
SESSION_END_WAIT_SEC = 30.0


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    """このディレクトリのテストに、実時間再生を許す長いタイムアウトを付ける。"""
    for item in items:
        if Path(__file__).parent in Path(item.fspath).parents:
            item.add_marker(pytest.mark.timeout(180))


@pytest.fixture(autouse=True)
def _require_e2e_environment() -> None:
    missing = [
        name
        for name in (_ENV_AUDIO_DIR, _ENV_ENDPOINT, _ENV_API_KEY)
        if not os.environ.get(name)
    ]
    if missing:
        pytest.skip(f"E2Eの環境変数が未設定です: {missing}")


def audio_path(wav_filename: str) -> Path:
    path = Path(os.environ[_ENV_AUDIO_DIR]) / wav_filename
    if not path.exists():
        pytest.skip(f"音声ファイルがありません: {path}")
    return path


async def run_session(
    audio_capture: AudioCapturePort,
    *,
    settings: dict[str, float] | None = None,
    session_end_wait_sec: float = SESSION_END_WAIT_SEC,
) -> list[dict[str, str]]:
    """録音開始から session_end までのイベントを集めて返す。

    `settings` は `_DEFAULTS` の上書き。`session_end` が届かなければ
    `TimeoutError`、録音開始の失敗は `error` イベントとして返る。
    """
    cfg = {**_DEFAULTS, **(settings or {})}
    broadcaster = SSEBroadcaster()
    app_state = AppState(broadcaster=broadcaster)

    def stt_engine_factory(queue: asyncio.Queue[SttEvent]) -> SttEnginePort:
        return create_stt_engine(
            queue,
            endpoint=os.environ[_ENV_ENDPOINT],
            api_key=os.environ[_ENV_API_KEY],
            sample_rate=_SAMPLE_RATE,
            locale_fn=lambda: "ja",
            model_name_fn=lambda: "MAI-Transcribe-2",
            transcribe_style_fn=lambda: "verbatim",
            timeout_sec_fn=lambda: cfg["mai_timeout_sec"],
        )

    def vad_factory() -> SpeechActivityDetectorPort:
        return create_vad(
            sample_rate=_SAMPLE_RATE, threshold_fn=lambda: cfg["vad_threshold"]
        )

    accumulator = SegmentAccumulator(broadcaster)
    transcription_queue = TranscriptionQueue(
        app_state, accumulator, NoOpSessionFinalizationHook()
    )
    transcription_queue.start()
    service = RecordingSessionService(
        app_state,
        AudioPipelineCoordinator(
            audio_capture,
            stt_engine_factory,
            vad_factory,
            segment_silence_sec_fn=lambda: cfg["segment_silence_sec"],
        ),
        SttEventRelay(app_state, accumulator),
        transcription_queue,
        max_session_duration_sec_fn=lambda: cfg["max_session_duration_sec"],
        silence_timeout_sec_fn=lambda: cfg["silence_timeout_sec"],
    )

    events_queue = broadcaster.subscribe()
    events: list[dict[str, str]] = []

    async def collect() -> None:
        while True:
            event = await events_queue.get()
            events.append(event)
            if event["event"] in ("session_end", "error"):
                return

    try:
        await service.start_session()
        await asyncio.wait_for(collect(), timeout=session_end_wait_sec)
    finally:
        await service.stop_session()
        await transcription_queue.shutdown(timeout=cfg["mai_timeout_sec"])
    return events


def recognized_texts(events: list[dict[str, str]]) -> list[str]:
    return [json.loads(e["data"])["text"] for e in events if e["event"] == "recognized"]


@pytest.fixture
def file_capture() -> Callable[[str], FileAudioCapture]:
    def make(wav_filename: str) -> FileAudioCapture:
        return FileAudioCapture(audio_path(wav_filename), sample_rate=_SAMPLE_RATE)

    return make
