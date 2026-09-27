"""fraetor-dictation の最小利用例 (import・最小構築のスモークスクリプト)。"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from dictation import (
    AppState,
    AudioPipelineCoordinator,
    RecordingSessionService,
    SegmentAccumulator,
    SSEBroadcaster,
    SttEventRelay,
    TranscriptionQueue,
    create_audio_capture,
    create_stt_engine,
    create_vad,
)
from dictation.domain.ports import SessionFinalizationHookPort

if TYPE_CHECKING:
    from datetime import datetime

    from dictation.domain.events import SttEvent
    from dictation.domain.models import RecordingSession
    from dictation.domain.ports import SpeechActivityDetectorPort, SttEnginePort


class NoOpSessionFinalizationHook(SessionFinalizationHookPort):
    async def on_session_finalized(
        self, session: RecordingSession, *, ended_at: datetime, timed_out: bool
    ) -> None:
        pass


def build_recording_session_service(
    *, mai_endpoint: str, mai_api_key: str, sample_rate: int = 16000
) -> RecordingSessionService:
    """dictationライブラリの主要コンポーネントを組み立てる最小構成例。"""
    broadcaster = SSEBroadcaster()
    app_state = AppState(broadcaster=broadcaster)
    audio_capture = create_audio_capture(sample_rate=sample_rate)

    def stt_engine_factory(
        event_queue: asyncio.Queue[SttEvent],
    ) -> SttEnginePort:
        return create_stt_engine(
            event_queue,
            endpoint=mai_endpoint,
            api_key=mai_api_key,
            sample_rate=sample_rate,
            locale_fn=lambda: "ja",
            model_name_fn=lambda: "MAI-Transcribe-2",
            transcribe_style_fn=lambda: "verbatim",
            timeout_sec_fn=lambda: 60.0,
        )

    def vad_factory() -> SpeechActivityDetectorPort:
        return create_vad(sample_rate=sample_rate, threshold_fn=lambda: 0.5)

    audio_pipeline = AudioPipelineCoordinator(
        audio_capture,
        stt_engine_factory,
        vad_factory,
        segment_silence_sec_fn=lambda: 1.5,
    )
    accumulator = SegmentAccumulator(broadcaster)
    event_relay = SttEventRelay(app_state, accumulator)
    transcription_queue = TranscriptionQueue(
        app_state, accumulator, NoOpSessionFinalizationHook()
    )
    transcription_queue.start()

    return RecordingSessionService(
        app_state,
        audio_pipeline,
        event_relay,
        transcription_queue,
        max_session_duration_sec_fn=lambda: 600.0,
        silence_timeout_sec_fn=lambda: 120.0,
    )


async def main() -> None:
    service = build_recording_session_service(
        mai_endpoint="https://mai.example/", mai_api_key="dummy"
    )
    print(f"RecordingSessionService constructed: {service}")


if __name__ == "__main__":
    asyncio.run(main())
