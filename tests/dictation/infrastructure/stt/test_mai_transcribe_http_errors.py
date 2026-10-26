"""MAI Transcribe SDK の実HTTPスタック(requests層)で異常応答を受けた場合の振る舞い。

`test_mai_transcribe_client.py` はSDKクライアント自体をモックするのに対し、
こちらはSDKが実際にHTTPを発行する層を `responses` で差し替える。
`azure-ai-transcription` SDK は `httpx` ではなく `requests` ベースの
`RequestsTransport` を使うため、`respx` (httpx専用) ではモックできない。
実クラウド通信もAPIキーも不要なので通常のテストとして実行できる。
"""

from __future__ import annotations

import asyncio
import re
import time
from typing import TYPE_CHECKING

import responses

from dictation import (
    MaiSettings,
    SttCredentials,
    SttLatencyHookPort,
    create_stt_engine,
)
from dictation.infrastructure.stt.specs import MaiEngineSpec

if TYPE_CHECKING:
    from dictation import SttEnginePort, SttEvent

_TRANSCRIBE_URL_PATTERN = re.compile(r".*/speechtotext/transcriptions:transcribe.*")
_SILENCE_PCM = b"\x00\x00" * 1600  # 0.1秒分の無音 (16kHz/16bit/mono)
_SAMPLE_RATE = 16000


def _make_engine(
    queue: asyncio.Queue[SttEvent], *, timeout_sec: float = 60.0
) -> SttEnginePort:
    return create_stt_engine(
        queue,
        endpoint="https://mai.example/",
        api_key="dummy-key-for-test",
        sample_rate=_SAMPLE_RATE,
        locale_fn=lambda: "ja",
        model_name_fn=lambda: "MAI-Transcribe-2",
        transcribe_style_fn=lambda: "verbatim",
        timeout_sec_fn=lambda: timeout_sec,
    )


class TestErrorResponses:
    async def _run_with_status(self, status: int, body: dict[str, str]) -> bool:
        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, _TRANSCRIBE_URL_PATTERN, status=status, json=body)
            queue: asyncio.Queue[SttEvent] = asyncio.Queue()
            engine = _make_engine(queue)
            await engine.start()
            engine.feed_audio(_SILENCE_PCM)
            await engine.stop()
        return queue.empty()

    async def test_unauthorized_response_is_swallowed(self) -> None:
        """401応答 → stop()が例外を握り潰し、recognizedが来ない"""
        assert await self._run_with_status(401, {"error": "unauthorized"})

    async def test_rate_limited_response_is_swallowed(self) -> None:
        """429応答 → stop()が例外を握り潰し、recognizedが来ない"""
        assert await self._run_with_status(429, {"error": "too many requests"})

    async def test_server_error_response_is_swallowed(self) -> None:
        """5xx応答 → stop()が例外を握り潰し、recognizedが来ない"""
        assert await self._run_with_status(503, {"error": "service unavailable"})

    async def test_timeout_is_swallowed(self) -> None:
        """タイムアウト超過 → タイムアウトが握り潰され、recognizedが来ない"""

        def _slow_response(request: object) -> tuple[int, dict[str, str], str]:
            time.sleep(0.5)
            return (200, {}, "{}")

        with responses.RequestsMock(assert_all_requests_are_fired=False) as rsps:
            rsps.add_callback(
                responses.POST, _TRANSCRIBE_URL_PATTERN, callback=_slow_response
            )
            queue: asyncio.Queue[SttEvent] = asyncio.Queue()
            engine = _make_engine(queue, timeout_sec=0.1)
            await engine.start()
            engine.feed_audio(_SILENCE_PCM)
            await engine.stop()
            # asyncio.wait_for のタイムアウトはスレッドの実行自体を中断しないため、
            # バックグラウンドのリクエスト送信が終わるまで待つ
            await asyncio.sleep(0.6)

        assert queue.empty()


class TestLatencyHook:
    async def test_hook_receives_failure_and_exception_is_swallowed(self) -> None:
        """5xx → succeeded=False で通知され、フックが例外を投げても録音は続く"""
        calls: list[dict[str, object]] = []

        class _Hook(SttLatencyHookPort):
            def on_stt_completed(self, **kwargs: object) -> None:
                calls.append(kwargs)
                raise RuntimeError("hook broken")

        with responses.RequestsMock() as rsps:
            rsps.add(responses.POST, _TRANSCRIBE_URL_PATTERN, status=503, json={})
            engine = MaiEngineSpec().create(
                asyncio.Queue(),
                credentials=SttCredentials(
                    mai_endpoint="https://mai.example/", mai_api_key="k"
                ),
                sample_rate=_SAMPLE_RATE,
                settings_fn=MaiSettings,
                latency_hook=_Hook(),
            )
            await engine.start()
            engine.feed_audio(_SILENCE_PCM)
            await engine.stop()

        assert len(calls) == 1
        assert calls[0]["engine"] == "mai"
        assert calls[0]["succeeded"] is False
