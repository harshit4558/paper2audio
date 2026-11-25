from __future__ import annotations

import asyncio
import json
import sys
import zipfile
from pathlib import Path
from typing import Literal

import click

from .assemble.audio import stitch_audio
from .assemble.html import build_companion_html
from .assemble.rss import update_feed
from .clean.speech import clean_document
from .config import load_settings
from .context.context import build_context
from .ingest.arxiv import download as arxiv_download
from .parse.latex import parse_latex
from .script.generate import generate_full_script
from .tts.cache import synthesize_all
from .tts.kokoro_backend import KokoroTTS


def _print(msg: str):
    click.echo(msg)


async def _run(
    arxiv_id: str,
    level: str,
    include_appendix: bool,
    out_dir: Path,
):
    settings = load_settings()
    cfg = settings.app()
    cache_dir = cfg.cache.dir.resolve()
    profile_path = Path("profile.md")
    profile = profile_path.read_text() if profile_path.exists() else ""

    _print("[1/7] Ingesting…")
    source_dir = await arxiv_download(arxiv_id, cache_dir)

    _print("[2/7] Parsing…")
    doc = parse_latex(source_dir, arxiv_id)
    _print(f"      {doc.title}")

    if not include_appendix:
        from .parse.models import DocumentModel
        doc = DocumentModel(
            **doc.model_dump(exclude={"sections"}),
            sections=[s for s in doc.sections if not s.is_appendix],
        )

    _print("[3/7] Cleaning…")
    doc = clean_document(doc)

    _print("[4/7] Context pass…")
    ctx = await build_context(
        doc=doc,
        profile=profile,
        model=cfg.llm.context_model,
        api_key=settings.anthropic_api_key,
        cache_dir=cache_dir,
    )

    _print("[5/7] Generating script…")
    sections_total = len([s for s in doc.sections if s.chunk_ids])

    async def script_progress(i, total, title):
        _print(f"      Section {i+1}/{total}: {title}")

    segments = await generate_full_script(
        doc=doc,
        ctx=ctx,
        profile=profile,
        level=level,
        model=cfg.llm.model,
        api_key=settings.anthropic_api_key,
        cache_dir=cache_dir,
        max_retries=cfg.llm.max_retries,
        progress_cb=script_progress,
    )

    _print("[6/7] TTS synthesis…")
    tts = KokoroTTS(
        voice_read=cfg.tts.kokoro.voice_read,
        voice_explain=cfg.tts.kokoro.voice_explain,
        speed=cfg.tts.kokoro.speed,
    )

    async def tts_progress(i, total):
        if i % 10 == 0:
            _print(f"      {i+1}/{total} segments")

    timed = await synthesize_all(
        segments=segments,
        doc=doc,
        provider=tts,
        cache_dir=cache_dir,
        progress_cb=tts_progress,
    )

    _print("[7/7] Assembling…")
    output_dir = out_dir / arxiv_id
    m4b, mp3, timed = stitch_audio(
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

    build_companion_html(timed, doc, output_dir / "companion.html")

    rss_path = cfg.cache.outputs_dir.resolve() / "feed.xml"
    update_feed(doc, mp3, duration, "http://localhost:8000", rss_path, cfg.rss)

    h = int(duration // 3600)
    m_part = int((duration % 3600) // 60)
    _print(f"\nDone. Duration: {h}h {m_part}m")
    _print(f"Output: {output_dir}/")
    _print(f"  paper.m4b")
    _print(f"  paper.mp3")
    _print(f"  companion.html")
    _print(f"  script.json")


@click.command()
@click.argument("arxiv_id")
@click.option("--level", default="normal", type=click.Choice(["light", "normal", "heavy"]), show_default=True)
@click.option("--include-appendix", is_flag=True, default=False)
@click.option("--out", default="./outputs", show_default=True, help="Output directory")
def main(arxiv_id: str, level: str, include_appendix: bool, out: str):
    """Turn a research paper into an audiobook.\n\nARXIV_ID: arXiv paper ID, e.g. 1706.03762"""
    asyncio.run(_run(arxiv_id, level, include_appendix, Path(out)))
