from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from google import genai
from google.genai import types
from google.oauth2 import service_account

from dictation.infrastructure.stt.batch_wav_engine import BatchWavSttEngine

if TYPE_CHECKING:
    import asyncio

    from dictation.domain.events import SttEvent
    from dictation.domain.ports import SttLatencyHookPort

logger = logging.getLogger(__name__)

_MODEL = "gemini-3.5-transcribe"
_SCOPES = ["https://www.googleapis.com/auth/cloud-platform"]


class GeminiTranscribeClient(BatchWavSttEngine):
    """Vertex AI の Gemini Transcribe によるバッチ文字起こしクライアント。

    WAV を inline で送る。数分を超える長い区間は未検証 (必要なら Files API)。
    """

    def __init__(
        self,
        stt_event_queue: asyncio.Queue[SttEvent],
        *,
        sa_info: dict[str, Any],
        project: str,
        location: str,
        language_code: str,
        timeout_sec: float,
        sample_rate: int,
        latency_hook: SttLatencyHookPort | None = None,
    ) -> None:
        super().__init__(
            stt_event_queue,
            timeout_sec=timeout_sec,
            sample_rate=sample_rate,
            latency_hook=latency_hook,
        )
        credentials = service_account.Credentials.from_service_account_info(  # type: ignore[no-untyped-call]
            sa_info, scopes=_SCOPES
        )
        self._client = genai.Client(
            vertexai=True,
            project=project,
            location=location,
            credentials=credentials,
        )
        self._language_code = language_code

    @property
    def engine_name(self) -> str:
        return "gemini"

    def _transcribe_sync(self, wav_bytes: bytes) -> str:
        response = self._client.models.generate_content(
            model=_MODEL,
            contents=[types.Part.from_bytes(data=wav_bytes, mime_type="audio/wav")],
            config=types.GenerateContentConfig(
                audio_transcription_config=types.AudioTranscriptionConfig(
                    language_codes=[self._language_code]
                )
            ),
        )
        # 無音のみの音声では text が None になる
        return response.text or ""
