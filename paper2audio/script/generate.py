from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import TypeAdapter

from ..context.context import ContextCache
from ..parse.models import DocumentModel, Section
from .models import (
    EquationSegment,
    ExplainSegment,
    FigureCueSegment,
    ReadSegment,
    Segment,
)
from .validate import ValidationResult, validate_section_script

_SEGMENT_ADAPTER: TypeAdapter[list[Segment]] = TypeAdapter(list[Segment])


def _section_prompt(
    section: Section,
    doc: DocumentModel,
    ctx: ContextCache,
    profile: str,
    level: Literal["light", "normal", "heavy"],
    explained_terms: set[str],
    max_retries: int = 3,
    validation_error: str = "",
) -> str:
    chunks = doc.get_section_chunks(section.id)
    chunk_lines = "\n".join(f"[{c.id}] {c.text}" for c in chunks)

    glossary_text = json.dumps(ctx.glossary, indent=2)
    fig_desc_text = json.dumps(ctx.figure_descriptions, indent=2)
    contribs_text = "\n".join(f"- {c}" for c in ctx.main_contributions)
    already_explained = ", ".join(sorted(explained_terms)) or "none"

    level_instructions = {
        "light": "Only add explain segments for terms that are genuinely opaque — central to the paper and not obvious. Skip section transitions. Keep it minimal.",
        "normal": "Add a brief section transition before this section. Explain key terms from the glossary when they first appear. Add figure cues when chunks reference figures. Prime significant equations before they are read.",
        "heavy": "Add a brief section transition before this section. Explain key terms thoroughly. Add figure cues. Prime and follow up equations. After any paragraph introducing 3 or more new concepts, add a brief recap explain segment.",
    }[level]

    retry_note = ""
    if validation_error:
        retry_note = f"""
IMPORTANT — YOUR PREVIOUS ATTEMPT WAS REJECTED:
{validation_error}

Fix these issues exactly. Every chunk ID in the CHUNKS list must appear in exactly one read segment."""

    return f"""You are producing the narration script for one section of a research paper audiobook.

PAPER: {doc.title}
MAIN CONTRIBUTIONS:
{contribs_text}

GLOSSARY (terms that may need explaining):
{glossary_text}

FIGURE DESCRIPTIONS:
{fig_desc_text}

USER PROFILE:
{profile}

EXPLANATION LEVEL: {level}
INSTRUCTION: {level_instructions}

ALREADY EXPLAINED TERMS (do NOT explain these again): {already_explained}

SECTION: {section.title}
CHUNKS (these are the sentences in this section — you must read ALL of them):
{chunk_lines}

RULES:
1. Return a JSON array of segment objects. Nothing else — no markdown, no explanation.
2. ReadSegment: {{"type": "read", "chunk_ids": [n, n+1, ...]}} — group consecutive chunks. Never write the paper text. Every chunk ID above must appear in exactly ONE read segment.
3. ExplainSegment: {{"type": "explain", "text": "..."}} — Voice B, 1-3 sentences. Never restate what was just read. Never explain terms in the already-explained list. Write like a knowledgeable friend, not a textbook.
4. FigureCueSegment: {{"type": "figure_cue", "ref": "fig_ref", "text": "..."}} — place when the chunk referencing that figure is read. Text: "Take a look at Figure X now. [2-sentence description of what to notice]."
5. EquationSegment: {{"type": "equation", "ref": "eq_ref", "text": "..."}} — spoken math for short equations, plain-language description for complex ones.
6. Chunk IDs in each read segment must be consecutive and in ascending order.
7. Do NOT explain concepts the user profile marks as already known.
{retry_note}

Return only the JSON array."""


async def generate_section_script(
    section: Section,
    doc: DocumentModel,
    ctx: ContextCache,
    profile: str,
    level: Literal["light", "normal", "heavy"],
    explained_terms: set[str],
    model: str,
    api_key: str,
    cache_dir: Path,
    max_retries: int = 3,
) -> list[Segment]:
    cache_file = cache_dir / "script" / doc.paper_id / f"{section.id}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        return _SEGMENT_ADAPTER.validate_json(cache_file.read_text())

    expected_ids = section.chunk_ids
    client = anthropic.AsyncAnthropic(api_key=api_key)
    validation_error = ""

    for attempt in range(max_retries):
        prompt = _section_prompt(
            section, doc, ctx, profile, level, explained_terms,
            max_retries, validation_error
        )

        message = await client.messages.create(
            model=model,
            max_tokens=8192,
            messages=[{"role": "user", "content": prompt}],
        )

        raw = message.content[0].text.strip()
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

        try:
            segments = _SEGMENT_ADAPTER.validate_json(raw)
        except Exception as e:
            validation_error = f"JSON parse error: {e}"
            continue

        result: ValidationResult = validate_section_script(segments, expected_ids)
        if result.valid:
            cache_file.write_text(
                json.dumps([s.model_dump() for s in segments], indent=2)
            )
            return segments

        validation_error = result.error_message()

    raise RuntimeError(
        f"Section '{section.title}' failed script validation after {max_retries} attempts.\n"
        f"Last error: {validation_error}"
    )


async def generate_full_script(
    doc: DocumentModel,
    ctx: ContextCache,
    profile: str,
    level: Literal["light", "normal", "heavy"],
    model: str,
    api_key: str,
    cache_dir: Path,
    max_retries: int = 3,
    progress_cb=None,
) -> list[Segment]:
    all_segments: list[Segment] = []
    explained_terms: set[str] = set()

    sections_to_process = [s for s in doc.sections if s.chunk_ids]

    for i, section in enumerate(sections_to_process):
        if progress_cb:
            await progress_cb(i, len(sections_to_process), section.title)

        segments = await generate_section_script(
            section=section,
            doc=doc,
            ctx=ctx,
            profile=profile,
            level=level,
            explained_terms=explained_terms,
            model=model,
            api_key=api_key,
            cache_dir=cache_dir,
            max_retries=max_retries,
        )

        # track newly explained terms
        for seg in segments:
            if isinstance(seg, ExplainSegment):
                for term in ctx.glossary:
                    if term.lower() in seg.text.lower():
                        explained_terms.add(term)

        all_segments.extend(segments)

    return all_segments
