from __future__ import annotations

from abc import ABC, abstractmethod
from pathlib import Path


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str, voice: str, out_path: Path) -> Path:
        """Synthesize text to a .wav file at out_path. Returns out_path."""
        ...

    @property
    @abstractmethod
    def sample_rate(self) -> int:
        ...

    @property
    @abstractmethod
    def voice_read(self) -> str:
        ...

    @property
    @abstractmethod
    def voice_explain(self) -> str:
        ...
