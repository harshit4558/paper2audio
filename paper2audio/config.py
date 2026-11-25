from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel
from pydantic_settings import BaseSettings, SettingsConfigDict


class LLMConfig(BaseModel):
    model: str = "claude-opus-4-5"
    context_model: str = "claude-opus-4-5"
    max_retries: int = 3


class KokoroConfig(BaseModel):
    voice_read: str = "am_michael"
    voice_explain: str = "af_heart"
    speed: float = 1.0
    sample_rate: int = 24000


class TTSConfig(BaseModel):
    provider: Literal["kokoro"] = "kokoro"
    kokoro: KokoroConfig = KokoroConfig()


class DefaultsConfig(BaseModel):
    level: Literal["light", "normal", "heavy"] = "normal"
    include_appendix: bool = False


class AudioConfig(BaseModel):
    pause_between_chunks_ms: int = 300
    pause_around_explain_ms: int = 600
    pause_before_figure_ms: int = 800
    pause_between_sections_ms: int = 1500
    output_format: Literal["m4b", "mp3", "both"] = "both"
    loudness_target_lufs: float = -23.0


class CacheConfig(BaseModel):
    dir: Path = Path("./cache")
    outputs_dir: Path = Path("./outputs")


class RSSConfig(BaseModel):
    title: str = "paper2audio"
    description: str = "Research papers as audiobooks"
    author: str = "paper2audio"


class FFmpegConfig(BaseModel):
    path: str = "ffmpeg"


class AppConfig(BaseModel):
    llm: LLMConfig = LLMConfig()
    tts: TTSConfig = TTSConfig()
    defaults: DefaultsConfig = DefaultsConfig()
    audio: AudioConfig = AudioConfig()
    cache: CacheConfig = CacheConfig()
    rss: RSSConfig = RSSConfig()
    ffmpeg: FFmpegConfig = FFmpegConfig()


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    anthropic_api_key: str = ""
    openai_api_key: str = ""

    _app: AppConfig | None = None

    def app(self, config_path: Path = Path("config.yaml")) -> AppConfig:
        if self._app is None:
            if config_path.exists():
                raw = yaml.safe_load(config_path.read_text())
                object.__setattr__(self, "_app", AppConfig.model_validate(raw or {}))
            else:
                object.__setattr__(self, "_app", AppConfig())
        return self._app


def load_settings(env_file: Path = Path(".env")) -> Settings:
    return Settings(_env_file=str(env_file))
