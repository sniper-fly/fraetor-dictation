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

    from dictation import AudioCapturePort, FileAudioCapture

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

    async def test_multiple_utterances_with_short_pause_stay_in_one_session(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """02: 短いポーズでは無音タイムアウトが発動せず、セッションが継続する"""
        events = await run_session(file_capture("02_multiple_utterances.wav"))

        status = [
            json.loads(e["data"])["recording"] for e in events if e["event"] == "status"
        ]
        # 途中で誤って停止/再開していれば3件以上になる。
        assert status == [True, False]
        print(f"\n[認識結果] {' / '.join(recognized_texts(events))}")

    async def test_multiple_utterances_are_flushed_as_separate_segments(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """02: 発話間のポーズで逐次flushされ、1セッション中に複数recognizedが届く

        ポーズの実測値(約1秒)のばらつきに関わらず確実に区切れるよう、
        `segment_silence_sec` を0.3秒にする。
        """
        events = await run_session(
            file_capture("02_multiple_utterances.wav"),
            settings={"segment_silence_sec": 0.3},
        )

        recognized = [
            json.loads(e["data"]) for e in events if e["event"] == "recognized"
        ]
        assert len(recognized) >= 2
        assert len({r["session_id"] for r in recognized}) == 1
        assert [r["segment_id"] for r in recognized] == list(range(len(recognized)))

    async def test_no_utterance_is_lost_when_flush_overlaps_next_utterance(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """02: flushのHTTP応答待ち中に次の発話が始まっても、両方の発話が残る

        発話の切れ目は1箇所(実測1.15秒)。`segment_silence_sec` を0.5秒にして
        1つ目の直後にflushさせると、MAIの応答(1〜2.5秒)が返る前に2つ目が始まる。
        「flushが上書きされた発話開始位置を使って前の発話を消す」既知バグの回帰確認。
        """
        events = await run_session(
            file_capture("02_multiple_utterances.wav"),
            settings={"segment_silence_sec": 0.5},
        )

        full_text = "".join(recognized_texts(events))
        assert "明日" in full_text, f"1つ目の発話が失われた: {full_text}"
        assert "午後" in full_text, f"2つ目の発話が失われた: {full_text}"

    async def test_short_utterance_is_recognized(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """04: 短い発話でもVAD/STTが機能する"""
        events = await run_session(file_capture("04_short_utterance.wav"))

        texts = recognized_texts(events)
        assert texts, "短い発話が認識されなかった"
        print(f"\n[認識結果] {' / '.join(texts)}")

    async def test_silence_only_is_not_detected_as_speech(
        self, file_capture: Callable[[str], FileAudioCapture]
    ) -> None:
        """03: 無音のみの音声ではVADが誤検知せず、タイムアウトで自動停止する"""
        events = await run_session(file_capture("03_silence_only.wav"))

        assert events[-1]["event"] == "session_end"
        assert not recognized_texts(events), "無音でrecognizedが発生した(誤検知)"

    async def test_missing_audio_device_broadcasts_error(
        self, broken_audio_capture: AudioCapturePort
    ) -> None:
        """マイクデバイスが開けない状態で録音開始 → errorイベントが配信される"""
        events = await run_session(broken_audio_capture, session_end_wait_sec=10)

        assert events[-1]["event"] == "error"
        assert "失敗" in events[-1]["data"]
