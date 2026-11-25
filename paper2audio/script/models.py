from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, Field


class ReadSegment(BaseModel):
    type: Literal["read"] = "read"
    chunk_ids: list[int]


class ExplainSegment(BaseModel):
    type: Literal["explain"] = "explain"
    text: str


class FigureCueSegment(BaseModel):
    type: Literal["figure_cue"] = "figure_cue"
    ref: str
    text: str


class EquationSegment(BaseModel):
    type: Literal["equation"] = "equation"
    ref: str
    text: str


Segment = Annotated[
    ReadSegment | ExplainSegment | FigureCueSegment | EquationSegment,
    Field(discriminator="type"),
]


class TimedSegment(BaseModel):
    segment: ReadSegment | ExplainSegment | FigureCueSegment | EquationSegment
    audio_path: str = ""
    start_seconds: float = 0.0
    end_seconds: float = 0.0
