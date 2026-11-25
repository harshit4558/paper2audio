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
    PodcastIntroSegment,
    PodcastOutroSegment,
    ReadSegment,
    Segment,
)
from .validate import ValidationResult, validate_section_script

_SEGMENT_ADAPTER: TypeAdapter[list[Segment]] = TypeAdapter(list[Segment])


# ---------------------------------------------------------------------------
# Podcast intro / outro
# ---------------------------------------------------------------------------

async def generate_intro(
    doc: DocumentModel,
    ctx: ContextCache,
    model: str,
    api_key: str,
    cache_dir: Path,
) -> PodcastIntroSegment:
    cache_file = cache_dir / "script" / doc.paper_id / "_intro.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        return PodcastIntroSegment.model_validate_json(cache_file.read_text())

    contribs = "\n".join(f"- {c}" for c in ctx.main_contributions)
    prompt = f"""You are the host of a research paper podcast. Write a cold open for today's episode.

PAPER: {doc.title}
AUTHORS: {", ".join(doc.authors[:4])}
MAIN CONTRIBUTIONS:
{contribs}
DIFFICULTY: {ctx.paper_difficulty}

Write a 4-6 sentence spoken cold open. It should:
- Open with a hook — why this paper matters or what problem it solved
- Give brief context (when it was published, why the field noticed it)
- Tell the listener what they'll walk away understanding
- End with a natural transition like "Let's get into it." or "Here we go."

Tone: knowledgeable friend — enthusiastic but not hype. Conversational, no jargon in the intro.
Return only the spoken text, no quotes, no markdown."""

    client = anthropic.AsyncAnthropic(api_key=api_key)
    msg = await client.messages.create(
        model=model, max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    seg = PodcastIntroSegment(text=text)
    cache_file.write_text(seg.model_dump_json())
    return seg


async def generate_outro(
    doc: DocumentModel,
    ctx: ContextCache,
    model: str,
    api_key: str,
    cache_dir: Path,
) -> PodcastOutroSegment:
    cache_file = cache_dir / "script" / doc.paper_id / "_outro.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        return PodcastOutroSegment.model_validate_json(cache_file.read_text())

    contribs = "\n".join(f"- {c}" for c in ctx.main_contributions)
    prompt = f"""You are the host of a research paper podcast. Write a closing outro for today's episode.

PAPER: {doc.title}
MAIN CONTRIBUTIONS:
{contribs}

Write a 4-6 sentence spoken outro. It should:
- Restate the one key insight that made this paper matter
- Name the most important result or finding
- Optionally note what this opened up or what to read next
- Close warmly like a podcast host ending an episode

Tone: warm, reflective, like wrapping up a good conversation.
Return only the spoken text, no quotes, no markdown."""

    client = anthropic.AsyncAnthropic(api_key=api_key)
    msg = await client.messages.create(
        model=model, max_tokens=512,
        messages=[{"role": "user", "content": prompt}],
    )
    text = msg.content[0].text.strip()
    seg = PodcastOutroSegment(text=text)
    cache_file.write_text(seg.model_dump_json())
    return seg


# ---------------------------------------------------------------------------
# Per-section script
# ---------------------------------------------------------------------------

def _section_prompt(
    section: Section,
    doc: DocumentModel,
    ctx: ContextCache,
    profile: str,
    level: Literal["light", "normal", "heavy"],
    explained_terms: set[str],
    section_history: list[tuple[str, list[str]]],
    validation_error: str = "",
) -> str:
    chunks = doc.get_section_chunks(section.id)
    chunk_lines = "\n".join(f"[{c.id}] {c.text}" for c in chunks)

    glossary_text = json.dumps(ctx.glossary, indent=2)
    fig_desc_text = json.dumps(ctx.figure_descriptions, indent=2)
    contribs_text = "\n".join(f"- {c}" for c in ctx.main_contributions)
    already_explained = ", ".join(sorted(explained_terms)) or "none"

    # Previous sections summary for back-references
    history_text = ""
    if section_history:
        lines = [f"- {title}: introduced {', '.join(concepts)}" for title, concepts in section_history[-6:]]
        history_text = f"""
PREVIOUS SECTIONS (use these to make back-references when relevant):
{chr(10).join(lines)}
"""

    level_instructions = {
        "light": "Only add explain segments for terms that are genuinely opaque. Skip the section teaser. Keep it minimal.",
        "normal": "Open with a 1-2 sentence podcast-style teaser that tells the listener what this section covers and why it matters — like a host saying 'now we're getting into X, and this is interesting because Y'. Explain key glossary terms on first appearance. Add figure cues. Prime significant equations.",
        "heavy": "Open with a podcast-style teaser for this section. Explain key terms thoroughly. Add figure cues. Prime and follow up equations. After any paragraph introducing 3+ new concepts, add a brief recap.",
    }[level]

    back_ref_instruction = ""
    if section_history:
        back_ref_instruction = """
BACK-REFERENCES: When a concept in this section builds directly on something from a previous section, add a brief ExplainSegment saying something like "Remember [concept] from [earlier section]? This is where it pays off." Only do this when the connection is genuinely illuminating — not every section needs one."""

    retry_note = ""
    if validation_error:
        retry_note = f"""
IMPORTANT — YOUR PREVIOUS ATTEMPT WAS REJECTED:
{validation_error}

Fix these issues. Every chunk ID in the CHUNKS list must appear in exactly one read segment."""

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
{history_text}
EXPLANATION LEVEL: {level}
INSTRUCTION: {level_instructions}
{back_ref_instruction}

ALREADY EXPLAINED TERMS (do NOT explain these again): {already_explained}

SECTION: {section.title}
CHUNKS (you must read ALL of them):
{chunk_lines}

RULES:
1. Return a JSON array of segment objects. Nothing else — no markdown, no explanation.
2. ReadSegment: {{"type": "read", "chunk_ids": [n, n+1, ...]}} — group consecutive chunks. Never write the paper's text. Every chunk ID above must appear in exactly ONE read segment.
3. ExplainSegment: {{"type": "explain", "text": "..."}} — Voice B, 1-3 sentences. Never restate what was just read. Never explain terms already in the explained list. Write like a knowledgeable friend, not a textbook.
4. FigureCueSegment: {{"type": "figure_cue", "ref": "fig_ref", "text": "..."}} — place when the chunk referencing that figure is read.
5. EquationSegment: {{"type": "equation", "ref": "eq_ref", "text": "..."}} — spoken math or plain-language description.
6. Chunk IDs in each read segment must be in ascending order.
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
    section_history: list[tuple[str, list[str]]],
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
            section, doc, ctx, profile, level,
            explained_terms, section_history, validation_error,
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
    section_history: list[tuple[str, list[str]]] = []

    # Podcast cold open
    intro = await generate_intro(doc, ctx, model, api_key, cache_dir)
    all_segments.append(intro)

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
            section_history=section_history,
            model=model,
            api_key=api_key,
            cache_dir=cache_dir,
            max_retries=max_retries,
        )

        # track explained terms and build section history
        newly_introduced: list[str] = []
        for seg in segments:
            if isinstance(seg, ExplainSegment):
                for term in ctx.glossary:
                    if term.lower() in seg.text.lower() and term not in explained_terms:
                        explained_terms.add(term)
                        newly_introduced.append(term)

        if newly_introduced:
            section_history.append((section.title, newly_introduced))

        all_segments.extend(segments)

    # Podcast outro
    outro = await generate_outro(doc, ctx, model, api_key, cache_dir)
    all_segments.append(outro)

    return all_segments
