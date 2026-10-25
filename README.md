# fraetor-dictation

録音開始/終了トリガーと無音区切りのバッチ認識 + SSE 疑似ストリーミング表示を提供する音声認識ライブラリ。
[Fraetor](https://github.com/sniper-fly/Fraetor) の `dictation` モジュールを抽出した独立パッケージ。

## ホストアプリとの責務境界

機能をライブラリとホストのどちらに置くかの判断基準は [`docs/boundary.md`](docs/boundary.md) を参照。

## アーキテクチャ

4層クリーンアーキテクチャ (Domain / Application / Infrastructure) を採用する。

- `dictation.domain`: 純粋な Pydantic モデルとポート (ABC)。サードパーティ SDK には依存しない。
- `dictation.application`: ユースケース/オーケストレーション。ポート型にのみ依存する。
- `dictation.infrastructure`: ポートの実装 (マイク入力、VAD、STTエンジン、SSE配信)。

依存方向は `infrastructure | application` → `domain` の一方向。

## インストール

```bash
uv add "fraetor-dictation @ git+https://github.com/sniper-fly/fraetor-dictation.git@v0.1.0"
```

`torch`/`torchaudio`はCPU版 (`+cpu`) を`[tool.uv.sources]`で指定している。`uv add`/`uv sync`経由のインストールでは正しくCPU版が解決されるが、`pip install`で直接インストールする場合はこの指定が無視されCUDA版が入るため、`uv`を使うことを前提とする。

## 公開ポート

ホスト側 (Fraetor / Tanakahn 等) は以下のポートを実装することで、STTエンジンやフックを差し替えられる。

| ポート | 役割 |
|---|---|
| `AudioCapturePort` | マイク音声キャプチャ |
| `SttEnginePort` | 音声認識エンジン |
| `SpeechActivityDetectorPort` | 発話区間検出 (VAD) |
| `EventBroadcasterPort` | SSE配信 |
| `SegmentLifecycleHookPort` | 発話区切りタイミングの外部フック (任意) |
| `RecognizedTextTransformPort` | recognizedイベントのテキスト変換フック (任意) |
| `SessionFinalizationHookPort` | セッション確定時の通知フック (必須) |

`create_audio_capture`/`create_stt_engine`/`create_vad` は標準実装 (sounddevice / Azure MAI Transcribe / Silero VAD) を組み立てるファクトリ関数。

## 最小利用例

`examples/minimal_usage.py` を参照。

## 開発

```bash
uv sync
uv run pytest
uv run mypy .
uv run ruff check
uv run ruff format --check
uv run lint-imports
```

### 実音声E2E (`tests/e2e/`)

録音済みWAVを実VAD・実MAIで認識する。HTTPサーバーは使わず、プロセス内で
`RecordingSessionService` を直接動かす。音声は本人の声を含むためGit管理外。
次の環境変数が未設定なら、通常の `uv run pytest` ではスキップされる。

- `FRAETOR_DICTATION_AUDIO_DIR`: WAV (16kHz/16bit/mono) のフォルダ。必要なファイルは `01_normal_speech.wav` と `06_long_speech.wav`
- `FRAETOR_DICTATION_MAI_ENDPOINT` / `FRAETOR_DICTATION_MAI_API_KEY`: MAIの接続情報

資格情報をAWS SSMから取る場合は `scripts/run_e2e.sh` を使う (SSOログイン済みが前提)。
SSMパラメータ名は `DICTATION_SSM_MAI_ENDPOINT` / `DICTATION_SSM_MAI_API_KEY` で指定する。

## License

MIT

