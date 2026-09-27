"""テスト全体で共用するポート実装のフェイク。"""

from __future__ import annotations

from typing import TYPE_CHECKING

from dictation.domain.ports import SessionFinalizationHookPort

if TYPE_CHECKING:
    from datetime import datetime

    from dictation.domain.models import RecordingSession


class NoOpSessionFinalizationHook(SessionFinalizationHookPort):
    """テスト用のno-op実装。呼び出し引数を記録するのみ。"""

    def __init__(self) -> None:
        self.calls: list[tuple[RecordingSession, datetime, bool]] = []

    async def on_session_finalized(
        self, session: RecordingSession, *, ended_at: datetime, timed_out: bool
    ) -> None:
        self.calls.append((session, ended_at, timed_out))
