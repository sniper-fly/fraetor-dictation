from __future__ import annotations

import asyncio
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from dictation import (
    STT_ENGINE_SPECS,
    GeminiSettings,
    MaiSettings,
    SttCredentials,
)
from dictation.infrastructure.stt.gemini_transcribe_client import (
    GeminiTranscribeClient,
)
from dictation.infrastructure.stt.mai_transcribe_client import MaiTranscribeClient

_MAI_CREDS = SttCredentials(mai_endpoint="https://mai.example/", mai_api_key="k")
_VERTEX_CREDS = SttCredentials(
    vertex_sa_info={"type": "service_account"}, vertex_project="proj"
)


class TestRegistry:
    def test_engines_are_looked_up_by_name(self) -> None:
        assert set(STT_ENGINE_SPECS) == {"mai", "gemini"}
        assert STT_ENGINE_SPECS["mai"].settings_model is MaiSettings
        assert STT_ENGINE_SPECS["gemini"].settings_model is GeminiSettings

    def test_mai_default_style_is_clean(self) -> None:
        assert MaiSettings().transcribe_style == "clean"

    def test_gemini_location_rejects_unknown(self) -> None:
        with pytest.raises(ValidationError):
            GeminiSettings(location="asia")  # type: ignore[arg-type]

    def test_timeout_must_be_positive(self) -> None:
        with pytest.raises(ValidationError):
            MaiSettings(timeout_sec=0)


class TestCreate:
    def test_mai_reads_settings_at_each_create(self) -> None:
        current = MaiSettings(locale="ja")
        with patch(
            "dictation.infrastructure.stt.mai_transcribe_client.TranscriptionClient"
        ):
            spec = STT_ENGINE_SPECS["mai"]
            first = spec.create(
                asyncio.Queue(),
                credentials=_MAI_CREDS,
                sample_rate=16000,
                settings_fn=lambda: current,
            )
            current = MaiSettings(locale="en")
            second = spec.create(
                asyncio.Queue(),
                credentials=_MAI_CREDS,
                sample_rate=16000,
                settings_fn=lambda: current,
            )
        assert isinstance(first, MaiTranscribeClient)
        assert isinstance(second, MaiTranscribeClient)
        assert (first._locale, second._locale) == ("ja", "en")

    def test_gemini_creates_client(self) -> None:
        with (
            patch("dictation.infrastructure.stt.gemini_transcribe_client.genai"),
            patch(
                "dictation.infrastructure.stt.gemini_transcribe_client.service_account"
            ),
        ):
            engine = STT_ENGINE_SPECS["gemini"].create(
                asyncio.Queue(),
                credentials=_VERTEX_CREDS,
                sample_rate=16000,
                settings_fn=GeminiSettings,
            )
        assert isinstance(engine, GeminiTranscribeClient)

    @pytest.mark.parametrize("name", ["mai", "gemini"])
    def test_missing_credentials_raise_value_error(self, name: str) -> None:
        with pytest.raises(ValueError, match="未設定"):
            STT_ENGINE_SPECS[name].create(
                asyncio.Queue(),
                credentials=SttCredentials(),
                sample_rate=16000,
                settings_fn=STT_ENGINE_SPECS[name].settings_model,
            )
