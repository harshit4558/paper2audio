from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import anthropic
from pydantic import BaseModel

from ..parse.models import DocumentModel


class ContextCache(BaseModel):
    paper_id: str
    glossary: dict[str, str] = {}
    main_contributions: list[str] = []
    figure_descriptions: dict[str, str] = {}
    paper_difficulty: Literal["introductory", "standard", "dense"] = "standard"


def _build_prompt(doc: DocumentModel, profile: str) -> str:
    sections_text = []
    for section in doc.sections[:8]:  # first 8 sections keeps prompt manageable
        chunks = doc.get_section_chunks(section.id)
        section_body = " ".join(c.text for c in chunks[:30])
        sections_text.append(f"## {section.title}\n{section_body}")

    figures_text = "\n".join(
        f"- {fig.label} ({fig.ref}): {fig.caption}" for fig in doc.figures
    )

    paper_text = "\n\n".join(sections_text)

    return f"""You are preparing to narrate a research paper as an audiobook. Read the paper and produce a context object used by the narration script generator.

PAPER TITLE: {doc.title}
AUTHORS: {", ".join(doc.authors)}
ABSTRACT: {doc.abstract}

FIGURES:
{figures_text or "None"}

PAPER BODY (excerpts):
{paper_text}

USER PROFILE:
{profile}

Return a JSON object with exactly these keys:

{{
  "glossary": {{
    "term": "plain-language definition in one sentence — only include terms the user profile says they may not know"
  }},
  "main_contributions": [
    "concise bullet — 3 to 5 total"
  ],
  "figure_descriptions": {{
    "fig_ref": "2-sentence description of what this figure shows and what is notable about it — based on the caption and context"
  }},
  "paper_difficulty": "introductory | standard | dense"
}}

For the glossary: be selective. Include terms that are central to the paper and non-obvious to someone with the given profile. Skip common ML/CS terms the profile marks as known.
For figure_descriptions: use the ref key (e.g. "fig1", "fig:arch") exactly as it appears in the figures list.
Return only the JSON object, no other text."""


async def build_context(
    doc: DocumentModel,
    profile: str,
    model: str,
    api_key: str,
    cache_dir: Path,
) -> ContextCache:
    cache_file = cache_dir / "context" / f"{doc.paper_id}.json"
    cache_file.parent.mkdir(parents=True, exist_ok=True)

    if cache_file.exists():
        return ContextCache.model_validate_json(cache_file.read_text())

    client = anthropic.AsyncAnthropic(api_key=api_key)
    prompt = _build_prompt(doc, profile)

    message = await client.messages.create(
        model=model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )

    raw = message.content[0].text.strip()
    # strip markdown code fences if present
    if raw.startswith("```"):
        raw = re.sub(r"^```[a-z]*\n?", "", raw)
        raw = re.sub(r"\n?```$", "", raw)

    data = json.loads(raw)
    ctx = ContextCache(paper_id=doc.paper_id, **data)
    cache_file.write_text(ctx.model_dump_json(indent=2))
    return ctx


import re  # noqa: E402  (needed for clean_json above)
