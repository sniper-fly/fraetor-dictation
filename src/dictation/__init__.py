"""fraetor-dictationの公開API。"""

from __future__ import annotations

from dictation.application.app_state import AppState
from dictation.application.audio_pipeline_coordinator import (
    AudioPipelineCoordinator,
)
from dictation.application.recording_session_service import (
    RecordingSessionService,
)
from dictation.application.segment_accumulator import SegmentAccumulator
from dictation.application.stt_event_relay import SttEventRelay
from dictation.application.transcription_queue import TranscriptionQueue
from dictation.domain.events import InterimEvent, RecognizedEvent, SttEvent
from dictation.domain.models import (
    RecordingSession,
    Segment,
    SttCredentials,
    TranscriptionJob,
)
from dictation.domain.ports import (
    AudioCapturePort,
    EventBroadcasterPort,
    RecognizedTextTransformPort,
    SegmentLifecycleHookPort,
    SessionFinalizationHookPort,
    SpeechActivityDetectorPort,
    SttCapabilities,
    SttEnginePort,
    SttEngineSpecPort,
    SttLatencyHookPort,
)
from dictation.domain.stt_settings import GeminiSettings, MaiSettings
from dictation.infrastructure.audio.factory import create_audio_capture
from dictation.infrastructure.audio.file_capture import FileAudioCapture
from dictation.infrastructure.messaging.sse_broadcaster import SSEBroadcaster
from dictation.infrastructure.stt.factory import create_stt_engine
from dictation.infrastructure.stt.specs import STT_ENGINE_SPECS
from dictation.infrastructure.vad.factory import create_vad

__all__ = [
    "STT_ENGINE_SPECS",
    "AppState",
    "AudioCapturePort",
    "AudioPipelineCoordinator",
    "EventBroadcasterPort",
    "FileAudioCapture",
    "GeminiSettings",
    "InterimEvent",
    "MaiSettings",
    "RecognizedEvent",
    "RecognizedTextTransformPort",
    "RecordingSession",
    "RecordingSessionService",
    "SSEBroadcaster",
    "Segment",
    "SegmentAccumulator",
    "SegmentLifecycleHookPort",
    "SessionFinalizationHookPort",
    "SpeechActivityDetectorPort",
    "SttCapabilities",
    "SttCredentials",
    "SttEnginePort",
    "SttEngineSpecPort",
    "SttEvent",
    "SttEventRelay",
    "SttLatencyHookPort",
    "TranscriptionJob",
    "TranscriptionQueue",
    "create_audio_capture",
    "create_stt_engine",
    "create_vad",
]
