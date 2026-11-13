"""Read and normalize source data."""
import json
import logging
import re
import unicodedata
from pathlib import Path
from typing import Any

from pypdf import PdfReader

from .models import DocumentPage

logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    """Normalize Unicode (NFC) and clean whitespace while preserving paragraph breaks."""
    normalized = unicodedata.normalize("NFC", text)
    normalized = normalized.replace("\r\n", "\n").replace("\r", "\n")
    # Collapse horizontal spaces/tabs within lines
    lines = [re.sub(r"[ \t]+", " ", line).strip() for line in normalized.split("\n")]
    # Preserve blank lines as paragraph separators (max 2 consecutive newlines)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def load_pdf(path: Path, document_id: str, source: str, extra_metadata: dict[str, Any] | None = None) -> list[DocumentPage]:
    """Extract each physical page from a PDF; keep its page number and source."""
    reader = PdfReader(str(path))
    pages: list[DocumentPage] = []
    empty_pages: list[int] = []
    base_meta = dict(extra_metadata or {})

    for idx, page_obj in enumerate(reader.pages, start=1):
        raw = page_obj.extract_text() or ""
        text = normalize_text(raw)
        if not text:
            empty_pages.append(idx)
            continue
        pages.append(
            DocumentPage(
                document_id=document_id,
                source=source,
                page=idx,
                text=text,
                metadata={**base_meta, "char_count": len(text)},
            )
        )

    if empty_pages:
        logger.warning("PDF %s has empty/unreadable pages: %s", path.name, empty_pages)
    return pages


def load_corpus_from_manifest(manifest_path: Path, repo_root: Path) -> list[DocumentPage]:
    """Load corpus pages listed in manifest.json and validate page contents."""
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    pages: list[DocumentPage] = []
    empty_records: list[str] = []

    for entry in manifest.get("pages", []):
        file_path = repo_root / entry["source"]
        if not file_path.exists():
            raise FileNotFoundError(f"Corpus file missing: {file_path}")

        raw = file_path.read_text(encoding="utf-8")
        text = normalize_text(raw)
        if not text:
            empty_records.append(entry["file"])
            continue
        if len(text) < 1500 or len(text) != entry["char_count"]:
            raise ValueError(f"Unexpected normalized character count in {file_path}: {len(text)}")

        pages.append(
            DocumentPage(
                document_id=entry["document_id"],
                source=entry["source"],
                page=int(entry["page"]),
                text=text,
                metadata={
                    "company": entry["company"],
                    "year": int(entry["year"]),
                    "doc_type": entry["doc_type"],
                    "char_count": len(text),
                },
            )
        )

    if empty_records:
        logger.warning("Skipped empty corpus pages: %s", empty_records)
    return pages
