"""Implement fixed-size and structure-aware (paragraph) chunking."""
from .models import Chunk, DocumentPage


def _make_chunk(
    page: DocumentPage,
    strategy_tag: str,
    idx: int,
    text: str,
) -> Chunk:
    return Chunk(
        chunk_id=f"{page.document_id}_p{page.page}_{strategy_tag}_{idx:03d}",
        document_id=page.document_id,
        source=page.source,
        page=page.page,
        text=text,
        metadata={
            **page.metadata,
            "chunk_index": idx,
            "char_count": len(text),
        },
    )


def fixed_size(page: DocumentPage, size: int = 500, overlap: int = 50) -> list[Chunk]:
    """Split text into overlapping fixed-size character windows and retain page metadata."""
    if size <= 0:
        raise ValueError("size must be positive")
    if overlap < 0 or overlap >= size:
        raise ValueError("overlap must be >= 0 and < size")

    text = page.text
    if not text:
        return []

    chunks: list[Chunk] = []
    start = 0
    idx = 0
    step = size - overlap

    while start < len(text):
        end = start + size
        piece = text[start:end].strip()
        if piece:
            chunks.append(_make_chunk(page, "fixed", idx, piece))
            idx += 1
        if end >= len(text):
            break
        start += step

    return chunks


def _split_oversized_block(block: str, max_size: int, overlap: int = 80) -> list[str]:
    """Split a block exceeding max_size by lines first, then by sliding window if needed."""
    if len(block) <= max_size:
        return [block]

    lines = [ln.strip() for ln in block.split("\n") if ln.strip()]
    parts: list[str] = []
    current: list[str] = []
    current_len = 0

    for ln in lines:
        if len(ln) > max_size:
            if current:
                parts.append("\n".join(current))
                current = []
                current_len = 0
            start = 0
            step = max(1, max_size - overlap)
            while start < len(ln):
                end = start + max_size
                piece = ln[start:end].strip()
                if piece:
                    parts.append(piece)
                if end >= len(ln):
                    break
                start += step
            continue

        added_len = len(ln) + (1 if current else 0)
        if current_len + added_len > max_size and current:
            parts.append("\n".join(current))
            current = [ln]
            current_len = len(ln)
        else:
            current.append(ln)
            current_len += added_len

    if current:
        parts.append("\n".join(current))
    return parts


def by_paragraph(page: DocumentPage, max_size: int = 800) -> list[Chunk]:
    """Group natural paragraphs up to max_size, split oversized blocks, and retain metadata."""
    if max_size <= 0:
        raise ValueError("max_size must be positive")

    raw_paragraphs = [p.strip() for p in page.text.split("\n\n") if p.strip()]
    units: list[str] = []
    for para in raw_paragraphs:
        units.extend(_split_oversized_block(para, max_size=max_size))

    chunks: list[Chunk] = []
    current_group: list[str] = []
    current_len = 0
    idx = 0

    for unit in units:
        added_len = len(unit) + (2 if current_group else 0)
        if current_len + added_len > max_size and current_group:
            chunks.append(_make_chunk(page, "para", idx, "\n\n".join(current_group)))
            idx += 1
            current_group = [unit]
            current_len = len(unit)
        else:
            current_group.append(unit)
            current_len += added_len

    if current_group:
        chunks.append(_make_chunk(page, "para", idx, "\n\n".join(current_group)))

    return chunks
