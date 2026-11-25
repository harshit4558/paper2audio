from __future__ import annotations

from enum import Enum
from typing import Optional

from pydantic import BaseModel, model_validator


class ChunkType(str, Enum):
    BODY = "body"
    FOOTNOTE = "footnote"
    CAPTION = "caption"


class Chunk(BaseModel):
    id: int
    text: str
    section_id: str
    chunk_type: ChunkType = ChunkType.BODY


class Figure(BaseModel):
    ref: str
    label: str
    caption: str
    image_path: Optional[str] = None
    citing_chunk_id: Optional[int] = None


class Equation(BaseModel):
    ref: str
    label: str
    latex: str
    is_inline: bool = False
    citing_chunk_id: Optional[int] = None


class Section(BaseModel):
    id: str
    title: str
    level: int
    chunk_ids: list[int] = []
    is_appendix: bool = False


class DocumentModel(BaseModel):
    paper_id: str
    title: str
    authors: list[str] = []
    abstract: str = ""
    sections: list[Section] = []
    chunks: dict[int, Chunk] = {}
    figures: list[Figure] = []
    equations: list[Equation] = []

    @model_validator(mode="after")
    def chunks_keyed_by_int(self) -> DocumentModel:
        self.chunks = {int(k): v for k, v in self.chunks.items()}
        return self

    def get_chunk(self, chunk_id: int) -> Chunk:
        return self.chunks[chunk_id]

    def get_section_chunks(self, section_id: str) -> list[Chunk]:
        section = next((s for s in self.sections if s.id == section_id), None)
        if section is None:
            return []
        return [self.chunks[cid] for cid in section.chunk_ids if cid in self.chunks]

    def all_body_chunk_ids(self) -> list[int]:
        ids = []
        for section in self.sections:
            ids.extend(section.chunk_ids)
        return ids
