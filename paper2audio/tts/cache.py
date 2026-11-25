from __future__ import annotations

import hashlib
from pathlib import Path

from ..parse.models import DocumentModel
from ..script.models import (
    EquationSegment,
    ExplainSegment,
    FigureCueSegment,
    PodcastIntroSegment,
    PodcastOutroSegment,
    ReadSegment,
    Segment,
    TimedSegment,
)
from .interface import TTSProvider


def segment_text(segment: Segment, doc: DocumentModel) -> str:
    if isinstance(segment, ReadSegment):
        return " ".join(doc.get_chunk(cid).text for cid in segment.chunk_ids if cid in doc.chunks)
    return segment.text  # type: ignore[union-attr]


def segment_voice(segment: Segment, provider: TTSProvider) -> str:
    if isinstance(segment, ReadSegment):
        return provider.voice_read
    return provider.voice_explain


def cache_key(text: str, voice: str) -> str:
    return hashlib.sha256(f"{voice}::{text}".encode()).hexdigest()[:16]


async def synthesize_all(
    segments: list[Segment],
    doc: DocumentModel,
    provider: TTSProvider,
    cache_dir: Path,
    progress_cb=None,
) -> list[TimedSegment]:
    audio_cache = cache_dir / "audio"
    audio_cache.mkdir(parents=True, exist_ok=True)

    timed: list[TimedSegment] = []

    for i, seg in enumerate(segments):
        if progress_cb:
            await progress_cb(i, len(segments))

        text = segment_text(seg, doc)
        voice = segment_voice(seg, provider)

        if not text.strip():
            timed.append(TimedSegment(segment=seg, audio_path=""))
            continue

        key = cache_key(text, voice)
        wav_path = audio_cache / f"{key}.wav"

        if not wav_path.exists():
            await provider.synthesize(text, voice, wav_path)

        timed.append(TimedSegment(segment=seg, audio_path=str(wav_path)))

    return timed
