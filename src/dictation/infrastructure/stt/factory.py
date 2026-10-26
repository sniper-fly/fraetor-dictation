"""STTエンジン選択のファクトリ (DIコンテナ用)。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dictation.infrastructure.stt.mai_transcribe_client import MaiTranscribeClient

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable

    from dictation.domain.events import SttEvent
    from dictation.domain.ports import SttEnginePort


def create_stt_engine(
    stt_event_queue: asyncio.Queue[SttEvent],
    *,
    endpoint: str,
    api_key: str,
    sample_rate: int,
    locale_fn: Callable[[], str],
    model_name_fn: Callable[[], str],
    transcribe_style_fn: Callable[[], str],
    timeout_sec_fn: Callable[[], float],
) -> SttEnginePort:
    """設定に応じたSTTエンジンを生成する。

    後方互換のため残している MAI Transcribe 専用のファクトリ。複数エンジンから
    選ぶ場合は `STT_ENGINE_SPECS` (name → `SttEngineSpecPort`) を使う。

    ロケール・モデル名・タイムアウトは生成時 (= セッション開始時) に
    各 `Callable` から読む。エンジンはセッションごとに作り直されるため、
    設定画面からの変更は次のセッションで反映される。
    """
    return MaiTranscribeClient(
        stt_event_queue,
        endpoint=endpoint,
        api_key=api_key,
        locale=locale_fn(),
        model_name=model_name_fn(),
        transcribe_style=transcribe_style_fn(),
        timeout_sec=timeout_sec_fn(),
        sample_rate=sample_rate,
    )
