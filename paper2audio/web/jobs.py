from __future__ import annotations

import asyncio
import json
import uuid
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel

from ..assemble.audio import stitch_audio
from ..assemble.html import build_companion_html
from ..assemble.rss import update_feed
from ..clean.speech import clean_document
from ..config import AppConfig, Settings
from ..context.context import build_context
from ..ingest.arxiv import download as arxiv_download
from ..parse.latex import parse_latex
from ..parse.models import DocumentModel
from ..script.generate import generate_full_script
from ..script.models import TimedSegment
from ..tts.cache import synthesize_all
from ..tts.kokoro_backend import KokoroTTS


class JobStatus(str, Enum):
    QUEUED = "queued"
    INGESTING = "ingesting"
    PARSING = "parsing"
    CLEANING = "cleaning"
    CONTEXT = "context"
    SCRIPTING = "scripting"
    TTS = "tts"
    ASSEMBLING = "assembling"
    DONE = "done"
    FAILED = "failed"


class Job(BaseModel):
    id: str
    arxiv_id: Optional[str] = None
    level: Literal["light", "normal", "heavy"] = "normal"
    include_appendix: bool = False
    status: JobStatus = JobStatus.QUEUED
    stage_progress: dict[str, float] = {}
    paper_title: Optional[str] = None
    duration_seconds: Optional[float] = None
    created_at: datetime = datetime.now(timezone.utc)
    completed_at: Optional[datetime] = None
    download_url: Optional[str] = None
    error: Optional[str] = None
    message: str = ""


_jobs: dict[str, Job] = {}
_queues: dict[str, asyncio.Queue] = {}


def create_job(arxiv_id: str, level: str, include_appendix: bool) -> Job:
    job = Job(id=str(uuid.uuid4()), arxiv_id=arxiv_id, level=level, include_appendix=include_appendix)
    _jobs[job.id] = job
    _queues[job.id] = asyncio.Queue()
    return job


def get_job(job_id: str) -> Optional[Job]:
    return _jobs.get(job_id)


def _update(job: Job, **kwargs):
    for k, v in kwargs.items():
        setattr(job, k, v)
    if job.id in _queues:
        _queues[job.id].put_nowait(job.model_dump_json())


async def event_stream(job_id: str):
    q = _queues.get(job_id)
    if q is None:
        return
    while True:
        try:
            data = await asyncio.wait_for(q.get(), timeout=30)
            yield f"data: {data}\n\n"
            parsed = json.loads(data)
            if parsed.get("status") in ("done", "failed"):
                break
        except asyncio.TimeoutError:
            yield ": keepalive\n\n"


async def run_job(job: Job, settings: Settings, cfg: AppConfig, base_url: str):
    cache_dir = cfg.cache.dir.resolve()
    outputs_dir = cfg.cache.outputs_dir.resolve()
    output_dir = outputs_dir / job.id
    profile_path = Path("profile.md")
    profile = profile_path.read_text() if profile_path.exists() else ""

    try:
        _update(job, status=JobStatus.INGESTING, message="Downloading paper…")
        source_dir = await arxiv_download(job.arxiv_id, cache_dir)

        _update(job, status=JobStatus.PARSING, message="Parsing LaTeX…")
        doc: DocumentModel = parse_latex(source_dir, job.arxiv_id)
        _update(job, paper_title=doc.title)

        if not job.include_appendix:
            doc = DocumentModel(
                **doc.model_dump(exclude={"sections"}),
                sections=[s for s in doc.sections if not s.is_appendix],
            )

        _update(job, status=JobStatus.CLEANING, message="Cleaning text for speech…")
        doc = clean_document(doc)

        _update(job, status=JobStatus.CONTEXT, message="Running context pass…")
        ctx = await build_context(
            doc=doc,
            profile=profile,
            model=cfg.llm.context_model,
            api_key=settings.anthropic_api_key,
            cache_dir=cache_dir,
        )

        sections_total = len([s for s in doc.sections if s.chunk_ids])

        async def script_progress(i, total, title):
            _update(
                job,
                status=JobStatus.SCRIPTING,
                stage_progress={**job.stage_progress, "scripting": i / max(total, 1)},
                message=f"Generating script for section {i+1}/{total}: {title}",
            )

        _update(job, status=JobStatus.SCRIPTING, message="Generating narration script…")
        segments = await generate_full_script(
            doc=doc,
            ctx=ctx,
            profile=profile,
            level=job.level,
            model=cfg.llm.model,
            api_key=settings.anthropic_api_key,
            cache_dir=cache_dir,
            max_retries=cfg.llm.max_retries,
            progress_cb=script_progress,
        )

        tts_provider = KokoroTTS(
            voice_read=cfg.tts.kokoro.voice_read,
            voice_explain=cfg.tts.kokoro.voice_explain,
            speed=cfg.tts.kokoro.speed,
        )

        async def tts_progress(i, total):
            _update(
                job,
                status=JobStatus.TTS,
                stage_progress={**job.stage_progress, "tts": i / max(total, 1)},
                message=f"Synthesising audio {i+1}/{total}…",
            )

        _update(job, status=JobStatus.TTS, message="Synthesising audio…")
        timed = await synthesize_all(
            segments=segments,
            doc=doc,
            provider=tts_provider,
            cache_dir=cache_dir,
            progress_cb=tts_progress,
        )

        _update(job, status=JobStatus.ASSEMBLING, message="Assembling audio…")
        m4b_path, mp3_path, timed = stitch_audio(
            timed_segments=timed,
            doc=doc,
            cfg=cfg.audio,
            output_dir=output_dir,
            ffmpeg_path=cfg.ffmpeg.path,
        )

        duration = timed[-1].end_seconds if timed else 0.0

        script_json = output_dir / "script.json"
        script_json.write_text(
            json.dumps([
                {"segment": t.segment.model_dump(), "start": t.start_seconds, "end": t.end_seconds}
                for t in timed
            ], indent=2)
        )

        companion = output_dir / "companion.html"
        build_companion_html(timed, doc, companion)

        rss_path = outputs_dir / "feed.xml"
        update_feed(doc, mp3_path, duration, base_url, rss_path, cfg.rss)

        _update(
            job,
            status=JobStatus.DONE,
            duration_seconds=duration,
            completed_at=datetime.now(timezone.utc),
            download_url=f"/jobs/{job.id}/download",
            message="Done.",
        )

    except Exception as exc:
        _update(job, status=JobStatus.FAILED, error=str(exc), message=f"Failed: {exc}")
        raise
