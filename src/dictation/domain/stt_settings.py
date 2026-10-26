"""STTエンジンごとの設定モデル。

型・範囲・選択肢・既定値だけを持つ。表示用のラベルや単位はホストが持つ。
"""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field


class MaiSettings(BaseModel):
    """MAI Transcribe の設定。"""

    model_config = ConfigDict(frozen=True)

    locale: str = "ja"
    model_name: Literal["MAI-Transcribe-2"] = "MAI-Transcribe-2"
    transcribe_style: Literal["clean", "verbatim"] = "clean"
    timeout_sec: float = Field(default=60, gt=0)


class GeminiSettings(BaseModel):
    """Vertex AI Gemini Transcribe の設定。"""

    model_config = ConfigDict(frozen=True)

    location: Literal["global", "us", "eu"] = "global"
    language_code: str = "ja-JP"
    timeout_sec: float = Field(default=60, gt=0)
