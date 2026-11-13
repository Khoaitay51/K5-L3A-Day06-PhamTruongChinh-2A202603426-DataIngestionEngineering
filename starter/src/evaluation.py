"""Evaluate retrieved evidence and preserve per-query traces."""
import re
from typing import Any


def _norm_ws(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def _is_evidence_hit(question: dict[str, Any], hit: dict[str, Any]) -> tuple[bool, bool]:
    """Return (full_evidence_hit, source_and_page_match)."""
    chunk_norm = _norm_ws(hit.get("text", ""))
    page_match = False
    for gold in [question, *question.get("alternate_evidence", [])]:
        if hit.get("document_id") != gold["gold_source_id"] or hit.get("page") != gold["gold_page"]:
            continue
        page_match = True
        quotes = [gold["evidence_quote"], *gold.get("additional_evidence_quotes", [])]
        normalized_quotes = [_norm_ws(text) for text in quotes]
        if all(quote and quote in chunk_norm for quote in normalized_quotes):
            return True, True
    return False, page_match


def _check_filter_compliance(hits: list[dict[str, Any]], applied_filter: dict[str, Any] | None) -> bool | None:
    if not applied_filter:
        return None
    for hit in hits:
        meta = hit.get("metadata") or {}
        for k, v in applied_filter.items():
            if meta.get(k) != v:
                return False
    return True


def evaluate_query(
    question: dict[str, Any],
    hits: list[dict[str, Any]],
    applied_filter: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Check source/page and gold evidence; keep hit@1 and hit@3 trace."""
    hit_flags: list[bool] = []
    page_flags: list[bool] = []
    for h in hits:
        ev_hit, pg_hit = _is_evidence_hit(question, h)
        hit_flags.append(ev_hit)
        page_flags.append(pg_hit)

    hit_at_1 = bool(hit_flags[0]) if hit_flags else False
    hit_at_3 = any(hit_flags[:3])
    page_hit_at_3 = any(page_flags[:3])
    filter_compliant = _check_filter_compliance(hits[:3], applied_filter)

    return {
        "question_id": question["id"],
        "split": question["split"],
        "type": question.get("type"),
        "question": question["question"],
        "gold_source_id": question["gold_source_id"],
        "gold_page": question["gold_page"],
        "evidence_quote": question["evidence_quote"],
        **({"additional_evidence_quotes": question["additional_evidence_quotes"]}
           if question.get("additional_evidence_quotes") else {}),
        **({"alternate_evidence": question["alternate_evidence"]}
           if question.get("alternate_evidence") else {}),
        "applied_filter": applied_filter,
        "hit_at_1": hit_at_1,
        "hit_at_3": hit_at_3,
        "page_hit_at_3": page_hit_at_3,
        "filter_compliant": filter_compliant,
        "top_3": [
            {
                "rank": idx + 1,
                "chunk_id": h["chunk_id"],
                "document_id": h["document_id"],
                "source": h["source"],
                "page": h["page"],
                "score": h["score"],
                "evidence_hit": hit_flags[idx],
                "page_match": page_flags[idx],
                "metadata": h["metadata"],
                "text": h["text"],
            }
            for idx, h in enumerate(hits[:3])
        ],
    }


def summarize(traces: list[dict[str, Any]]) -> dict[str, Any]:
    """Aggregate Hit@1 and Evidence Hit@3 by split (dev/test)."""
    summary: dict[str, Any] = {}
    for split in ("dev", "test", "all"):
        subset = traces if split == "all" else [t for t in traces if t["split"] == split]
        n = len(subset)
        if n == 0:
            continue
        h1 = sum(1 for t in subset if t["hit_at_1"])
        h3 = sum(1 for t in subset if t["hit_at_3"])
        p3 = sum(1 for t in subset if t["page_hit_at_3"])
        flt_items = [t for t in subset if t["filter_compliant"] is not None]
        flt_ok = sum(1 for t in flt_items if t["filter_compliant"])
        summary[split] = {
            "total": n,
            "hit_at_1_count": h1,
            "hit_at_1_rate": round(h1 / n, 4),
            "hit_at_3_count": h3,
            "hit_at_3_rate": round(h3 / n, 4),
            "page_hit_at_3_count": p3,
            "filter_total": len(flt_items),
            "filter_compliant_count": flt_ok,
        }
    return summary
