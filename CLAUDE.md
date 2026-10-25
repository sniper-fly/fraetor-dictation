このリポジトリは、複数のホストアプリ(Fraetor、Tanakahn等)が共有する音声認識ライブラリ。

## 最重要: 責務境界

機能を足す・変更する前に、必ず [`docs/boundary.md`](docs/boundary.md) を読み、判断フローで置き場所を決めること。
**特定のホスト(Fraetor等)の都合だけで生まれた機能は、このリポジトリに入れない。** ホスト側に書き、必要ならここには拡張点の「口」(ポートのABC)だけを足す。
迷ったらホストに置く。`boundary.md` と食い違う変更が必要なら、実装せずユーザーに確認する。

このライブラリが守る制約(詳細は `docs/boundary.md`):

- ホスト固有の型・名前を参照しない(`src` をimportしない。import-linterで検証)
- 環境変数・設定ファイル・固定パス・資格情報をライブラリ内で読まない。ホストが決める値は引数で受ける
- 動的に変わりうる設定は値ではなく `Callable[[], T]` で受ける
- ホストが実装する拡張点は、必ずポート(ABC)として定義する

## 開発

- PostToolUse hookではなく手動で確認する: `uv run pytest` / `uv run mypy .` / `uv run ruff check` / `uv run ruff format --check` / `uv run lint-imports`
- 依存追加は `pyproject.toml` を直接編集せず `uv add` を使う。`torch`/`torchaudio` はCPU版(`+cpu`)に固定している(`[tool.uv.sources]`)
- dataclassではなくPydanticの `BaseModel` を使う。必ずテストを書く
- `tests/__init__.py` を消さない(`tests/dictation` が本物の `dictation` パッケージと名前衝突するため)

## 公開APIの変更はホストに波及する

`dictation/__init__.py` の `__all__`、ポートのシグネチャ、ファクトリの引数を変えると、ホスト側が壊れる。

- 変更したらタグを打つ(後方互換を壊す場合はバージョンを上げる)
- ホスト側(Fraetor等)で `uv add` してタグを進め、ホスト側のテストとE2Eを通して確認する
- ローカルのホストの場所は、各ホストの `CLAUDE.local.md`(Git管理外)を参照する
