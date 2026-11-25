from __future__ import annotations

import asyncio
from pathlib import Path

import numpy as np
import soundfile as sf

from .interface import TTSProvider


class KokoroTTS(TTSProvider):
    def __init__(self, voice_read: str = "am_michael", voice_explain: str = "af_heart", speed: float = 1.0):
        self._voice_read = voice_read
        self._voice_explain = voice_explain
        self._speed = speed
        self._pipeline = None

    def _load(self):
        if self._pipeline is None:
            from kokoro import KPipeline
            self._pipeline = KPipeline(lang_code="a")

    def _synthesize_sync(self, text: str, voice: str, out_path: Path) -> Path:
        self._load()
        chunks: list[np.ndarray] = []
        for _, _, audio in self._pipeline(text, voice=voice, speed=self._speed):
            chunks.append(audio)
        if not chunks:
            audio = np.zeros(self._sample_rate // 4, dtype=np.float32)
        else:
            audio = np.concatenate(chunks)
        out_path.parent.mkdir(parents=True, exist_ok=True)
        sf.write(str(out_path), audio, self._sample_rate)
        return out_path

    async def synthesize(self, text: str, voice: str, out_path: Path) -> Path:
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(None, self._synthesize_sync, text, voice, out_path)

    _sample_rate: int = 24000

    @property
    def sample_rate(self) -> int:
        return self._sample_rate

    @property
    def voice_read(self) -> str:
        return self._voice_read

    @property
    def voice_explain(self) -> str:
        return self._voice_explain
