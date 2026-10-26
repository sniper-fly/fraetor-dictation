"""STTエンジン仕様 (`SttEngineSpecPort`) の実装とレジストリ。"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from dictation.domain.ports import SttEngineSpecPort
from dictation.domain.stt_settings import GeminiSettings, MaiSettings
from dictation.infrastructure.stt.gemini_transcribe_client import (
    GeminiTranscribeClient,
)
from dictation.infrastructure.stt.mai_transcribe_client import MaiTranscribeClient

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from dictation.domain.events import SttEvent
    from dictation.domain.models import SttCredentials
    from dictation.domain.ports import SttEnginePort, SttLatencyHookPort


class MaiEngineSpec(SttEngineSpecPort[MaiSettings]):
    @property
    def name(self) -> str:
        return "mai"

    @property
    def settings_model(self) -> type[MaiSettings]:
        return MaiSettings

    def create(
        self,
        stt_event_queue: asyncio.Queue[SttEvent],
        *,
        credentials: SttCredentials,
        sample_rate: int,
        settings_fn: Callable[[], MaiSettings],
        latency_hook: SttLatencyHookPort | None = None,
    ) -> SttEnginePort:
        if not credentials.mai_endpoint or not credentials.mai_api_key:
            msg = "MAI Transcribe の endpoint / api_key が未設定です"
            raise ValueError(msg)
        settings = settings_fn()
        return MaiTranscribeClient(
            stt_event_queue,
            endpoint=credentials.mai_endpoint,
            api_key=credentials.mai_api_key,
            locale=settings.locale,
            model_name=settings.model_name,
            transcribe_style=settings.transcribe_style,
            timeout_sec=settings.timeout_sec,
            sample_rate=sample_rate,
            latency_hook=latency_hook,
        )


class GeminiEngineSpec(SttEngineSpecPort[GeminiSettings]):
    @property
    def name(self) -> str:
        return "gemini"

    @property
    def settings_model(self) -> type[GeminiSettings]:
        return GeminiSettings

    def create(
        self,
        stt_event_queue: asyncio.Queue[SttEvent],
        *,
        credentials: SttCredentials,
        sample_rate: int,
        settings_fn: Callable[[], GeminiSettings],
        latency_hook: SttLatencyHookPort | None = None,
    ) -> SttEnginePort:
        if not credentials.vertex_sa_info or not credentials.vertex_project:
            msg = "Vertex AI のサービスアカウント / プロジェクトが未設定です"
            raise ValueError(msg)
        settings = settings_fn()
        return GeminiTranscribeClient(
            stt_event_queue,
            sa_info=credentials.vertex_sa_info,
            project=credentials.vertex_project,
            location=settings.location,
            language_code=settings.language_code,
            timeout_sec=settings.timeout_sec,
            sample_rate=sample_rate,
            latency_hook=latency_hook,
        )


STT_ENGINE_SPECS: dict[str, SttEngineSpecPort[Any]] = {
    spec.name: spec for spec in (MaiEngineSpec(), GeminiEngineSpec())
}
