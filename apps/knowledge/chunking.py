"""Deterministic chunking.

Chunks aim for ``chunk_size`` characters with ``overlap`` characters of context
carried into the next chunk. Boundaries prefer paragraph breaks, then sentence
breaks, so retrieved passages stay readable and citable.
"""
from __future__ import annotations

from dataclasses import dataclass

from apps.knowledge.extraction import TextSegment


@dataclass(frozen=True)
class Chunk:
    index: int
    text: str
    page_number: int | None = None


def split_text(text: str, *, chunk_size: int = 900, overlap: int = 120) -> list[str]:
    normalized = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not normalized:
        return []
    if len(normalized) <= chunk_size:
        return [normalized]

    overlap = max(0, min(overlap, chunk_size // 2))
    chunks: list[str] = []
    start = 0
    length = len(normalized)

    while start < length:
        end = min(length, start + chunk_size)
        if end < length:
            window = normalized[start:end]
            candidates = [
                window.rfind("\n\n"),
                window.rfind(". "),
                window.rfind("! "),
                window.rfind("? "),
                window.rfind("\n"),
            ]
            cut = max(candidates)
            if cut > int(chunk_size * 0.55):
                end = start + cut + 1
        piece = normalized[start:end].strip()
        if piece:
            chunks.append(piece)
        if end >= length:
            break
        start = max(end - overlap, start + 1)

    return chunks


def chunk_segments(
    segments: list[TextSegment], *, chunk_size: int = 900, overlap: int = 120
) -> list[Chunk]:
    chunks: list[Chunk] = []
    index = 0
    for segment in segments:
        for piece in split_text(segment.text, chunk_size=chunk_size, overlap=overlap):
            chunks.append(Chunk(index=index, text=piece, page_number=segment.page_number))
            index += 1
    return chunks
