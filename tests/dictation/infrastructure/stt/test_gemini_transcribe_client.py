from __future__ import annotations

import asyncio
import time
from unittest.mock import MagicMock, patch

from dictation.domain.events import RecognizedEvent, SttEvent
from dictation.infrastructure.stt.gemini_transcribe_client import (
    GeminiTranscribeClient,
)

_SAMPLE_RATE = 16000
_PCM = b"\x01\x00" * 1600  # 0.1秒


def _make_client(
    queue: asyncio.Queue[SttEvent] | None = None,
    *,
    timeout_sec: float = 60,
    hook: MagicMock | None = None,
) -> tuple[GeminiTranscribeClient, MagicMock, MagicMock]:
    with (
        patch(
            "dictation.infrastructure.stt.gemini_transcribe_client.genai.Client"
        ) as client_cls,
        patch(
            "dictation.infrastructure.stt.gemini_transcribe_client.service_account"
        ) as sa,
    ):
        client = GeminiTranscribeClient(
            queue if queue is not None else asyncio.Queue(),
            sa_info={"type": "service_account"},
            project="proj",
            location="us",
            language_code="ja-JP",
            timeout_sec=timeout_sec,
            sample_rate=_SAMPLE_RATE,
            latency_hook=hook,
        )
    return client, client_cls, sa


class TestRequest:
    async def test_sends_wav_with_language_to_transcribe_model(self) -> None:
        queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        client, client_cls, _ = _make_client(queue)
        sdk = client_cls.return_value
        sdk.models.generate_content.return_value = MagicMock(text="こんにちは")
        await client.start()
        client.feed_audio(_PCM)

        await client.stop()

        kwargs = sdk.models.generate_content.call_args.kwargs
        assert kwargs["model"] == "gemini-3.5-transcribe"
        part = kwargs["contents"][0]
        assert part.inline_data.mime_type == "audio/wav"
        assert part.inline_data.data.startswith(b"RIFF")
        assert kwargs["config"].audio_transcription_config.language_codes == ["ja-JP"]
        event = queue.get_nowait()
        assert isinstance(event, RecognizedEvent)
        assert event.text == "こんにちは"

    async def test_client_is_created_for_vertex_with_given_location(self) -> None:
        _, client_cls, _ = _make_client()

        kwargs = client_cls.call_args.kwargs
        assert kwargs["vertexai"] is True
        assert kwargs["project"] == "proj"
        assert kwargs["location"] == "us"

    async def test_silence_only_result_is_empty_without_event(self) -> None:
        """無音のみの音声では text が None で返り、空文字に正規化される。"""
        queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        client, client_cls, _ = _make_client(queue)
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text=None
        )
        await client.start()
        client.feed_audio(_PCM)

        await client.stop()

        assert queue.empty()


class TestLatencyHook:
    async def test_notified_on_success_with_audio_duration(self) -> None:
        hook = MagicMock()
        client, client_cls, _ = _make_client(hook=hook)
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text="x"
        )
        await client.start()
        client.feed_audio(_PCM)

        await client.stop()

        kwargs = hook.on_stt_completed.call_args.kwargs
        assert kwargs["engine"] == "gemini"
        assert kwargs["phase"] == "stop"
        assert kwargs["succeeded"] is True
        assert kwargs["audio_duration_sec"] == 0.1
        assert kwargs["duration_sec"] >= 0

    async def test_notified_on_failure(self) -> None:
        hook = MagicMock()
        client, client_cls, _ = _make_client(hook=hook)
        client_cls.return_value.models.generate_content.side_effect = RuntimeError("x")
        await client.start()
        client.feed_audio(_PCM)

        await client.flush(trim_before_sample=None)

        kwargs = hook.on_stt_completed.call_args.kwargs
        assert kwargs["phase"] == "flush"
        assert kwargs["succeeded"] is False

    async def test_hook_exception_does_not_break_recognition(self) -> None:
        hook = MagicMock()
        hook.on_stt_completed.side_effect = RuntimeError("hook broken")
        queue: asyncio.Queue[SttEvent] = asyncio.Queue()
        client, client_cls, _ = _make_client(queue, hook=hook)
        client_cls.return_value.models.generate_content.return_value = MagicMock(
            text="結果"
        )
        await client.start()
        client.feed_audio(_PCM)

        await client.stop()

        assert queue.get_nowait().text == "結果"

    async def test_notified_on_timeout(self) -> None:
        hook = MagicMock()
        client, client_cls, _ = _make_client(hook=hook, timeout_sec=0.05)

        def slow(**_: object) -> MagicMock:
            time.sleep(0.3)
            return MagicMock(text="x")

        client_cls.return_value.models.generate_content.side_effect = slow
        await client.start()
        client.feed_audio(_PCM)

        await client.stop()

        assert hook.on_stt_completed.call_args.kwargs["succeeded"] is False
