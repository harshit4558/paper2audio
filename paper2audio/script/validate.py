from __future__ import annotations

from dataclasses import dataclass

from .models import ReadSegment, Segment


@dataclass
class ValidationResult:
    valid: bool
    errors: list[str]

    def error_message(self) -> str:
        return "\n".join(self.errors)


def validate_section_script(
    segments: list[Segment],
    expected_chunk_ids: list[int],
) -> ValidationResult:
    errors: list[str] = []
    read_ids: list[int] = []

    for seg in segments:
        if isinstance(seg, ReadSegment):
            read_ids.extend(seg.chunk_ids)

    covered = set(read_ids)
    expected = set(expected_chunk_ids)

    missing = expected - covered
    if missing:
        errors.append(f"Missing chunk IDs (not read): {sorted(missing)}")

    extra = covered - expected
    if extra:
        errors.append(f"Unknown chunk IDs (not in this section): {sorted(extra)}")

    seen = set()
    duplicates = set()
    for cid in read_ids:
        if cid in seen:
            duplicates.add(cid)
        seen.add(cid)
    if duplicates:
        errors.append(f"Duplicate chunk IDs: {sorted(duplicates)}")

    for seg in segments:
        if isinstance(seg, ReadSegment) and len(seg.chunk_ids) > 1:
            for a, b in zip(seg.chunk_ids, seg.chunk_ids[1:]):
                if b <= a:
                    errors.append(
                        f"Chunk IDs in read segment not in ascending order: {seg.chunk_ids}"
                    )
                    break

    return ValidationResult(valid=len(errors) == 0, errors=errors)
