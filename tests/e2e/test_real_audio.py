"""録音済みWAVを「マイク入力」として、実VAD・実MAIで認識を検証するE2E。

環境変数が未設定ならスキップされる (`conftest.py` 参照)。音声ファイルの内容は
Fraetorの `tests/e2e/fixtures/audio/README.md` の想定発話と同じものを使う。
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from tests.e2e.conftest import recognized_texts, run_session

if TYPE_CHECKING:
    from collections.abc import Callable

    from dictation import FileAudioCapture

# 長文の session_end が届く最悪の時刻 = 録音が止まる上限 + 進行中のMAI送信の最大待ち。
_LONG_MAX_SESSION_SEC = 30
_LONG_MAI_TIMEOUT_SEC = 15.0


class TestRealAudio:
    async def test_normal_speech_is_recognized_and_session_ends(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """01: 発話が検知され、無音タイムアウトで自動停止し、認識結果が非空"""
        events = await run_session(file_capture("01_normal_speech.wav"))

        assert [e["event"] for e in events][-1] == "session_end"
        texts = recognized_texts(events)
        assert texts, "recognized イベントが1件も届かなかった"
        print(f"\n[認識結果] {' / '.join(texts)}")

    async def test_long_speech_all_sentences_survive_incremental_flush(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """06: 長文を無音区切りで逐次flushしても、全文が失われず届く

        文間のポーズ (実測0.71〜0.87秒) より短く、文中の息継ぎ (実測最大0.29秒)
        より長い0.45秒で区切り、複数回のflushを確実に起こす。
        """
        events = await run_session(
            file_capture("06_long_speech.wav"),
            settings={
                "segment_silence_sec": 0.45,
                "silence_timeout_sec": 5,
                "max_session_duration_sec": _LONG_MAX_SESSION_SEC,
                "mai_timeout_sec": _LONG_MAI_TIMEOUT_SEC,
            },
            session_end_wait_sec=_LONG_MAX_SESSION_SEC + _LONG_MAI_TIMEOUT_SEC,
        )

        recognized = [
            json.loads(e["data"]) for e in events if e["event"] == "recognized"
        ]
        assert len(recognized) >= 2, "複数回のflushが発生するはず"
        full_text = "".join(r["text"] for r in recognized)
        for keyword in ["定例会議", "進捗", "課題", "スケジュール", "よろしく"]:
            assert keyword in full_text, f"'{keyword}' を含む文が失われた: {full_text}"
        print(f"\n[認識結果] {' / '.join(r['text'] for r in recognized)}")
