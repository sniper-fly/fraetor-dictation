from __future__ import annotations

from abc import ABC, abstractmethod
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel

if TYPE_CHECKING:
    import asyncio
    from collections.abc import Callable
    from datetime import datetime

    from dictation.domain.events import SttEvent
    from dictation.domain.models import RecordingSession, SttCredentials


class AudioCapturePort(ABC):
    """マイク音声キャプチャの抽象ポート。

    PCM データ (16kHz/16-bit/mono) をシンクに渡す。ストリームの
    ライフサイクル戦略 (常駐/セッション毎開閉) は実装側の詳細。
    """

    @abstractmethod
    async def start_recording(self, sink: Callable[[bytes], object]) -> None:
        """シンクを設定して録音を開始する。"""

    @abstractmethod
    async def stop_recording(self) -> None:
        """録音を停止する。ストリームの扱いは実装のライフサイクル戦略に従う。"""


class SttCapabilities(BaseModel):
    """STTエンジンの能力を表す。"""

    streaming: bool
    """録音中に interim イベントを発火するか。"""

    post_processing: bool
    """stop() でバッチ処理が走り、完了まで時間がかかるか。"""


class SttEnginePort(ABC):
    """音声認識エンジンの抽象ポート。

    feed_audio で 16kHz/16bit/mono の PCM バイトを受け取り、
    認識結果は stt_event_queue に `SttEvent` (`InterimEvent`/`RecognizedEvent`)
    を投入する。
    """

    def __init__(self, stt_event_queue: asyncio.Queue[SttEvent]) -> None:
        self._queue = stt_event_queue

    @property
    @abstractmethod
    def capabilities(self) -> SttCapabilities: ...

    @abstractmethod
    async def start(self) -> None:
        """認識を開始する。"""

    @abstractmethod
    def feed_audio(self, buffer: bytes) -> None:
        """PCM音声データを送る (16kHz/16bit/mono)。"""

    @abstractmethod
    async def flush(self, *, trim_before_sample: int | None) -> str:
        """未送信の音声のうち trim_before_sample 以降を1セグメントとして認識する。

        結果は stop() と同じく recognized イベントとして queue に投入する。
        `trim_before_sample` (セッション開始からの累積サンプル数) より前は
        送信対象から除外する。`None` の場合は未送信分をそのまま送る。

        送るべき新規音声がなければ何もしない。失敗しても例外は伝播させず、
        そのセグメントのテキストを失うだけに留める (録音は継続する)。

        戻り値は認識結果テキスト (空文字列可)。空文字列の場合は recognized
        イベントは発行されていない。
        """

    @abstractmethod
    async def stop(self) -> None:
        """認識を停止する。

        バッチ型エンジンの場合はここで処理を実行し、結果を queue に投入する。
        flush() 済みの区間を除いた残りが最終セグメントとして送られる。
        """

    def discard_pending_audio(self) -> None:  # noqa: B027
        """未送信の音声を破棄し、以後の stop()/flush() の送信対象から外す。

        録音を止める際、未送信区間に新しい発話が無い (無音のみ) と呼び出し側が
        判断した場合に呼ぶ。stop() は未送信分を最終セグメントとして送るため、
        無音のみでも送って応答時間の分だけ完了が遅れる。それを避けるための口。

        バッファを持たないエンジン向けに、既定では何もしない (追加のみで既存の
        実装を壊さない)。呼んだ後に届いた音声は破棄の対象外。
        """


class SpeechActivityDetectorPort(ABC):
    """発話区間検出の抽象ポート。"""

    @property
    @abstractmethod
    def last_speech_time(self) -> float:
        """最後に発話を検知した時刻 (time.monotonic())。"""

    @property
    @abstractmethod
    def last_speech_start_sample(self) -> int | None:
        """直近の発話開始位置 (このインスタンス生存期間の累積サンプル数)。

        発話がまだ一度も検出されていない場合は None。前方の無音を送信対象から
        削るために、STT の PCM バッファのオフセットとして使う。
        """

    @abstractmethod
    def feed(self, pcm_bytes: bytes) -> None:
        """PCM (16kHz/16bit/mono) を受け取り、発話区間を検出する。"""


class SegmentLifecycleHookPort(ABC):
    """発話区切りの生成タイミングに対する外部フック(意図翻訳レイヤー用の拡張点)。"""

    @abstractmethod
    def on_speech_start(self) -> None:
        """発話開始検知の瞬間(未flush区間で最初の1回のみ)に呼ばれる。"""

    @abstractmethod
    def on_flush(self, *, produced_text: bool) -> None:
        """flush完了直後に呼ばれる。produced_textはrecognizedイベントが
        発行される見込みか(認識結果が空でなかったか)を示す。"""

    @abstractmethod
    def reset(self) -> None:
        """セッション開始時に呼ばれる。前セッションの状態を残さない。"""


class RecognizedTextTransformPort(ABC):
    """recognizedイベントのテキストに対する変換フック。"""

    @abstractmethod
    async def transform(self, session_id: str, text: str) -> str:
        """テキストを必要に応じて変換して返す。変換不要/失敗時は元のテキストを返す。"""


class SessionFinalizationHookPort(ABC):
    """録音停止後の文字起こし完了(セッション確定)時に呼ばれるフック。

    dictationは`RecordingSession`をそのまま渡すのみで、永続化・履歴化・
    不変レコードへの変換は関知しない(依存性逆転)。
    """

    @abstractmethod
    async def on_session_finalized(
        self, session: RecordingSession, *, ended_at: datetime, timed_out: bool
    ) -> None:
        """文字起こし完了直後(SSE `session_end` 配信の前)に呼ばれる。

        呼び出し中の例外は`TranscriptionQueue`が握り潰し、ログに記録して
        処理を継続する(1セッションの確定失敗が他セッションの処理を
        止めないため)。
        """


class EventBroadcasterPort(ABC):
    """SSE配信の抽象ポート。"""

    @abstractmethod
    def subscribe(self) -> asyncio.Queue[dict[str, str]]:
        """新規購読者のキューを登録して返す。"""

    @abstractmethod
    def unsubscribe(self, queue: asyncio.Queue[dict[str, str]]) -> None:
        """購読者のキューを解除する。"""

    @abstractmethod
    async def broadcast(self, event: str, data: Any) -> None:
        """全購読者にイベントを配信する。"""


class SttLatencyHookPort(ABC):
    """STT 1リクエストごとの応答時間の通知先 (計測の「口」)。

    記録・集計・表示はホストが実装する。ライブラリはバッチ型エンジンの
    送信前後を測り、成功・失敗・タイムアウトのいずれでも通知する。
    実装が例外を投げても録音は止まらない (ライブラリ側で握り潰す)。
    """

    @abstractmethod
    def on_stt_completed(
        self,
        *,
        engine: str,
        phase: str,
        duration_sec: float,
        audio_duration_sec: float,
        succeeded: bool,
    ) -> None:
        """1回の送信が終わった。`phase` は `flush` / `stop`。"""


class SttEngineSpecPort[SettingsT: BaseModel](ABC):
    """STTエンジンの仕様 (名前・設定モデル・生成方法) を表すポート。

    エンジンごとに使える設定値が異なるため、設定の型・範囲・選択肢・既定値は
    エンジンごとの Pydantic モデルとして持つ。ホストは `settings_model` から
    設定画面を組み立て、保存した値を `Callable` で `create()` に渡す。
    表示用の日本語ラベルや単位はホストの責務で、ここには持たない。
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """エンジン名 (レジストリのキー。例: `mai`, `gemini`)。"""

    @property
    @abstractmethod
    def settings_model(self) -> type[SettingsT]:
        """このエンジンの設定を表すモデルの型。"""

    @abstractmethod
    def create(
        self,
        stt_event_queue: asyncio.Queue[SttEvent],
        *,
        credentials: SttCredentials,
        sample_rate: int,
        settings_fn: Callable[[], SettingsT],
        latency_hook: SttLatencyHookPort | None = None,
    ) -> SttEnginePort:
        """エンジンを生成する。

        設定は生成時 (= セッション開始時) に `settings_fn` から読む。
        必要な資格情報が `credentials` に無ければ `ValueError` を投げる。
        """
