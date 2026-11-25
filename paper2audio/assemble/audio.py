from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

import numpy as np
import soundfile as sf

from ..config import AudioConfig
from ..parse.models import DocumentModel
from ..script.models import FigureCueSegment, ReadSegment, TimedSegment


_CHIME_FREQ = 880
_CHIME_DURATION = 0.15
_CHIME_SAMPLE_RATE = 24000


def _make_silence(ms: int, sample_rate: int = 24000) -> np.ndarray:
    return np.zeros(int(sample_rate * ms / 1000), dtype=np.float32)


def _make_chime(sample_rate: int = _CHIME_SAMPLE_RATE) -> np.ndarray:
    t = np.linspace(0, _CHIME_DURATION, int(sample_rate * _CHIME_DURATION), False)
    envelope = np.exp(-t * 15)
    return (np.sin(2 * np.pi * _CHIME_FREQ * t) * envelope * 0.3).astype(np.float32)


def stitch_audio(
    timed_segments: list[TimedSegment],
    doc: DocumentModel,
    cfg: AudioConfig,
    output_dir: Path,
    ffmpeg_path: str = "ffmpeg",
) -> tuple[Path, Path, list[TimedSegment]]:
    """Stitch all segments into one wav, then encode to mp3 and m4b.
    Returns (m4b_path, mp3_path, timed_segments_with_timestamps).
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    sample_rate = 24000

    silence_chunk = _make_silence(cfg.pause_between_chunks_ms, sample_rate)
    silence_explain = _make_silence(cfg.pause_around_explain_ms, sample_rate)
    silence_section = _make_silence(cfg.pause_between_sections_ms, sample_rate)
    silence_figure = _make_silence(cfg.pause_before_figure_ms, sample_rate)
    chime = _make_chime(sample_rate)

    # Build section boundaries for chapter markers
    section_starts: dict[str, float] = {}
    current_time = 0.0
    combined: list[np.ndarray] = []

    # group segments by section to detect section transitions
    section_ids = _get_section_sequence(timed_segments, doc)

    prev_section_id = None
    updated: list[TimedSegment] = []

    for i, ts in enumerate(timed_segments):
        seg = ts.segment
        section_id = section_ids[i]

        # section transition pause
        if section_id != prev_section_id and prev_section_id is not None:
            combined.append(silence_section)
            current_time += len(silence_section) / sample_rate

        if section_id not in section_starts:
            section_starts[section_id] = current_time

        if not ts.audio_path or not Path(ts.audio_path).exists():
            updated.append(ts.model_copy(update={"start_seconds": current_time, "end_seconds": current_time}))
            prev_section_id = section_id
            continue

        audio, sr = sf.read(ts.audio_path, dtype="float32")
        if sr != sample_rate:
            audio = _resample(audio, sr, sample_rate)
        if audio.ndim > 1:
            audio = audio.mean(axis=1)

        start = current_time

        if isinstance(seg, FigureCueSegment):
            combined.append(chime)
            combined.append(silence_figure)
            current_time += (len(chime) + len(silence_figure)) / sample_rate
        elif not isinstance(seg, ReadSegment):
            combined.append(silence_explain)
            current_time += len(silence_explain) / sample_rate

        combined.append(audio)
        current_time += len(audio) / sample_rate

        if not isinstance(seg, ReadSegment):
            combined.append(silence_explain)
            current_time += len(silence_explain) / sample_rate
        else:
            combined.append(silence_chunk)
            current_time += len(silence_chunk) / sample_rate

        updated.append(ts.model_copy(update={"start_seconds": start, "end_seconds": current_time}))
        prev_section_id = section_id

    full_audio = np.concatenate(combined) if combined else np.zeros(sample_rate, dtype=np.float32)

    with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp:
        tmp_wav = Path(tmp.name)
    sf.write(str(tmp_wav), full_audio, sample_rate)

    mp3_path = output_dir / "paper.mp3"
    m4b_path = output_dir / "paper.m4b"

    # chapters metadata file
    chapters_path = _write_chapters(section_starts, doc, output_dir)

    # encode mp3
    subprocess.run(
        [ffmpeg_path, "-y", "-i", str(tmp_wav),
         "-af", f"loudnorm=I={cfg.loudness_target_lufs}:TP=-1.5:LRA=11",
         "-codec:a", "libmp3lame", "-b:a", "128k", str(mp3_path)],
        check=True, capture_output=True,
    )

    # encode m4b with chapters (transcode mp3 → aac; mp3 stream can't be copied into m4b)
    subprocess.run(
        [ffmpeg_path, "-y",
         "-i", str(mp3_path),
         "-i", str(chapters_path),
         "-map_metadata", "1",
         "-c:a", "aac", "-b:a", "64k",
         str(m4b_path)],
        check=True, capture_output=True,
    )

    tmp_wav.unlink(missing_ok=True)

    return m4b_path, mp3_path, updated


def _get_section_sequence(timed_segments: list[TimedSegment], doc: DocumentModel) -> list[str]:
    result = []
    for ts in timed_segments:
        seg = ts.segment
        if isinstance(seg, ReadSegment) and seg.chunk_ids:
            cid = seg.chunk_ids[0]
            chunk = doc.chunks.get(cid)
            result.append(chunk.section_id if chunk else "unknown")
        else:
            result.append(result[-1] if result else "unknown")
    return result


def _write_chapters(section_starts: dict[str, float], doc: DocumentModel, output_dir: Path) -> Path:
    lines = [";FFMETADATA1\n"]
    section_map = {s.id: s for s in doc.sections}

    sorted_sections = sorted(section_starts.items(), key=lambda x: x[1])

    for i, (sec_id, start_time) in enumerate(sorted_sections):
        section = section_map.get(sec_id)
        title = section.title if section else sec_id
        start_ms = int(start_time * 1000)
        if i + 1 < len(sorted_sections):
            end_ms = int(sorted_sections[i + 1][1] * 1000)
        else:
            end_ms = start_ms + 600000  # 10 min fallback for last chapter
        lines.append(f"[CHAPTER]\nTIMEBASE=1/1000\nSTART={start_ms}\nEND={end_ms}\ntitle={title}\n\n")

    path = output_dir / "chapters.txt"
    path.write_text("".join(lines))
    return path


def _resample(audio: np.ndarray, orig_sr: int, target_sr: int) -> np.ndarray:
    ratio = target_sr / orig_sr
    n_samples = int(len(audio) * ratio)
    indices = np.linspace(0, len(audio) - 1, n_samples)
    return np.interp(indices, np.arange(len(audio)), audio).astype(np.float32)
