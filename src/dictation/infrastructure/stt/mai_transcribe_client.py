from __future__ import annotations

import io
import logging
from typing import TYPE_CHECKING

from azure.ai.transcription import TranscriptionClient
from azure.ai.transcription.models import (
    EnhancedModeProperties,
    TranscriptionContent,
    TranscriptionOptions,
)
from azure.core.credentials import AzureKeyCredential

from dictation.infrastructure.stt.batch_wav_engine import BatchWavSttEngine

if TYPE_CHECKING:
    import asyncio

    from dictation.domain.events import SttEvent
    from dictation.domain.ports import SttLatencyHookPort

logger = logging.getLogger(__name__)


class MaiTranscribeClient(BatchWavSttEngine):
    """MAI-Transcribe によるバッチ文字起こしクライアント。

    Azure Foundry の LLM Speech API へ WAV を送り、
    combined_phrases[0].text を認識結果とする。
    """

    def __init__(
        self,
        stt_event_queue: asyncio.Queue[SttEvent],
        *,
        endpoint: str,
        api_key: str,
        locale: str,
        model_name: str,
        transcribe_style: str,
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
        self._client = TranscriptionClient(
            endpoint=endpoint,
            credential=AzureKeyCredential(api_key),
        )
        self._locale = locale
        self._model_name = model_name
        self._transcribe_style = transcribe_style

    @property
    def engine_name(self) -> str:
        return "mai"

    def _transcribe_sync(self, wav_bytes: bytes) -> str:
        # SDK b4 では EnhancedModeProperties に model/modelOptions フィールドが
        # 公開されていないため、MutableMapping の __setitem__ 経由で REST 仕様
        # (enhancedMode.model, enhancedMode.modelOptions.transcribeStyle) を満たす。
        enhanced_mode = EnhancedModeProperties()
        enhanced_mode["enabled"] = True
        enhanced_mode["model"] = self._model_name
        enhanced_mode["modelOptions"] = {"transcribeStyle": self._transcribe_style}
        options = TranscriptionOptions(
            locales=[self._locale],
            enhanced_mode=enhanced_mode,
        )
        request = TranscriptionContent(
            definition=options,
            audio=("audio.wav", io.BytesIO(wav_bytes), "audio/wav"),
        )
        result = self._client.transcribe(request)
        if result.combined_phrases:
            text = result.combined_phrases[0].text
            return text or ""
        return ""
