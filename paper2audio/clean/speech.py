from __future__ import annotations

import re

from ..parse.models import ChunkType, DocumentModel


_CITATION_PATTERNS = [
    re.compile(r"\[[0-9,\s]+\]"),
    re.compile(r"\(([A-Z][a-z]+(?:\s+et\s+al\.)?(?:,\s*\d{4})?(?:;\s*)?)+\)"),
    re.compile(r"\\cite\{[^}]+\}"),
]

_URL_PATTERN = re.compile(r"https?://\S+|www\.\S+")

_ABBREV_MAP = {
    r"\be\.g\.,?": "for example,",
    r"\bi\.e\.,?": "that is,",
    r"\bet al\.": "and colleagues",
    r"\bcf\.": "compare",
    r"\bviz\.": "namely,",
    r"\bw\.r\.t\.": "with respect to",
    r"\bi\.i\.d\.": "independently and identically distributed",
    r"\bLHS\b": "left-hand side",
    r"\bRHS\b": "right-hand side",
    r"\bw\.l\.o\.g\.": "without loss of generality",
    r"\bFig\.": "Figure",
    r"\bEq\.": "Equation",
    r"\bSec\.": "Section",
    r"\bTab\.": "Table",
    r"\bApp\.": "Appendix",
    r"\bEqs\.": "Equations",
    r"\bFigs\.": "Figures",
}

_EM_DASH = re.compile(r"\s*—\s*|--")
_MULTIPLE_SPACES = re.compile(r"\s{2,}")
_FIGURE_PLACEHOLDER = re.compile(r"\[Figure reference: ([^\]]+)\]")
_EQUATION_PLACEHOLDER = re.compile(r"\[Equation reference: ([^\]]+)\]")


def clean_text(text: str) -> str:
    for pat in _CITATION_PATTERNS:
        text = pat.sub("", text)

    text = _URL_PATTERN.sub("", text)

    for pattern, replacement in _ABBREV_MAP.items():
        text = re.sub(pattern, replacement, text)

    text = _EM_DASH.sub(", ", text)
    text = _MULTIPLE_SPACES.sub(" ", text)
    text = text.strip().rstrip(",").strip()

    return text


def clean_document(doc: DocumentModel) -> DocumentModel:
    cleaned_chunks = {}
    for cid, chunk in doc.chunks.items():
        if chunk.chunk_type == ChunkType.CAPTION:
            cleaned_chunks[cid] = chunk
            continue
        cleaned = clean_text(chunk.text)
        if cleaned:
            cleaned_chunks[cid] = chunk.model_copy(update={"text": cleaned})
        else:
            cleaned_chunks[cid] = chunk

    cleaned_figures = []
    for fig in doc.figures:
        cleaned_figures.append(fig.model_copy(update={"caption": clean_text(fig.caption)}))

    return doc.model_copy(update={"chunks": cleaned_chunks, "figures": cleaned_figures})
