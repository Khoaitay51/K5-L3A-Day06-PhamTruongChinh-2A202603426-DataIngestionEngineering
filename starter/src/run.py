"""End-to-end Day 06 Data Ingestion & Retrieval Benchmark pipeline."""
import json
import os
import statistics
import time
from pathlib import Path
from typing import Any

from dotenv import load_dotenv

from .chunking import by_paragraph, fixed_size
from .embeddings import MyEmbedder
from .evaluation import evaluate_query, summarize
from .ingestion import load_corpus_from_manifest
from .models import Chunk
from .store import PgVectorStore


def _chunk_stats(chunks: list[Chunk]) -> dict[str, Any]:
    lengths = [len(c.text) for c in chunks]
    return {
        "chunk_count": len(chunks),
        "total_chars": sum(lengths),
        "min_chars": min(lengths) if lengths else 0,
        "median_chars": int(statistics.median(lengths)) if lengths else 0,
        "max_chars": max(lengths) if lengths else 0,
    }


def main() -> None:
    starter_dir = Path(__file__).resolve().parents[1]
    repo_root = starter_dir.parent
    load_dotenv(starter_dir / ".env")

    database_url = os.getenv("DATABASE_URL")
    if not database_url:
        raise RuntimeError("DATABASE_URL missing in starter/.env")

    model_name = os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
    dimensions = int(os.getenv("EMBEDDING_DIM", "768"))

    manifest_path = repo_root / "data" / "manifest.json"
    questions_path = repo_root / "benchmark" / "questions.json"
    results_dir = repo_root / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    # 1. Load corpus and validate >= 30 pages
    pages = load_corpus_from_manifest(manifest_path, repo_root)
    if len(pages) < 30:
        raise RuntimeError(f"Corpus must have >= 30 content pages, got {len(pages)}")
    print(f"[1/5] Loaded {len(pages)} corpus pages (all >= 1500 chars).")

    # 2. Load benchmark questions and validate >= 20 (15 dev / 5 test)
    questions = json.loads(questions_path.read_text(encoding="utf-8"))
    dev_count = sum(1 for q in questions if q["split"] == "dev")
    test_count = sum(1 for q in questions if q["split"] == "test")
    if len(questions) < 20 or dev_count < 15 or test_count < 5:
        raise RuntimeError(f"Invalid benchmark split: total={len(questions)}, dev={dev_count}, test={test_count}")
    print(f"[2/5] Loaded {len(questions)} benchmark questions ({dev_count} dev / {test_count} test).")

    # 3. Initialize embedder & pgvector store
    embedder = MyEmbedder(model_name=model_name, dimensions=dimensions)
    store = PgVectorStore(database_url=database_url, dimensions=dimensions)
    store.create_schema()
    print(f"[3/5] Connected to PostgreSQL + pgvector and Ollama ({model_name}, dim={dimensions}).")

    # 4. Build chunks and ingest for Strategies A, B, C
    strategy_configs = {
        "A": {"name": "fixed_size_500_50", "use_filter": False, "builder": lambda p: fixed_size(p, size=500, overlap=50)},
        "B": {"name": "paragraph_800", "use_filter": False, "builder": lambda p: by_paragraph(p, max_size=800)},
        "C": {"name": "paragraph_800_filtered", "use_filter": True, "builder": lambda p: by_paragraph(p, max_size=800)},
    }

    ingest_summary: dict[str, Any] = {}
    chunks_by_strategy: dict[str, list[Chunk]] = {}

    # Embed B once and reuse vectors for C since C has identical chunks (only retrieval filter differs)
    cached_para_vectors: list[list[float]] | None = None

    for strat_key, cfg in strategy_configs.items():
        t0 = time.perf_counter()
        strat_chunks: list[Chunk] = []
        for page in pages:
            strat_chunks.extend(cfg["builder"](page))
        chunks_by_strategy[strat_key] = strat_chunks

        if strat_key == "C" and cached_para_vectors is not None:
            vectors = cached_para_vectors
        else:
            vectors = embedder.embed_documents([c.text for c in strat_chunks])
            if strat_key == "B":
                cached_para_vectors = vectors

        store.upsert(strat_key, strat_chunks, vectors, replace_strategy=True)
        elapsed = round(time.perf_counter() - t0, 2)

        # Idempotency check: re-upsert first document's chunks and confirm row count unchanged
        count_before = store.count_chunks(strat_key)
        first_doc_id = strat_chunks[0].document_id
        sample_pairs = [(c, v) for c, v in zip(strat_chunks, vectors) if c.document_id == first_doc_id]
        store.upsert(strat_key, [p[0] for p in sample_pairs], [p[1] for p in sample_pairs])
        count_after = store.count_chunks(strat_key)
        if count_before != len(strat_chunks) or count_after != count_before:
            raise RuntimeError(
                f"Unexpected row count for {strat_key}: "
                f"expected {len(strat_chunks)}, got {count_before} before and {count_after} after reload"
            )

        stats = _chunk_stats(strat_chunks)
        ingest_summary[strat_key] = {
            "config_name": cfg["name"],
            **stats,
            "ingest_seconds": elapsed,
            "cost_usd": 0.0,
            "idempotent_reingest_ok": True,
            "stored_rows": count_after,
        }
        print(
            f"    Strategy {strat_key} ({cfg['name']}): {stats['chunk_count']} chunks, "
            f"median={stats['median_chars']} chars, time={elapsed}s, stored={count_after}"
        )

    # 5. Embed all queries once and run top-3 retrieval for each strategy
    print("[4/5] Embedding queries and running top-3 retrieval...")
    query_vectors = {q["id"]: embedder.embed_query(q["question"]) for q in questions}

    all_summaries: dict[str, Any] = {}
    traces_by_strategy: dict[str, list[dict[str, Any]]] = {}

    for strat_key, cfg in strategy_configs.items():
        traces: list[dict[str, Any]] = []
        for q in questions:
            applied_filter = q.get("filter") if cfg["use_filter"] else None
            hits = store.search(
                strategy=strat_key,
                query_vector=query_vectors[q["id"]],
                top_k=3,
                filters=applied_filter,
            )
            traces.append(evaluate_query(q, hits, applied_filter=applied_filter))

        traces_by_strategy[strat_key] = traces
        (results_dir / f"traces_{strat_key}.json").write_text(
            json.dumps(traces, indent=2, ensure_ascii=False), encoding="utf-8"
        )
        all_summaries[strat_key] = {
            "ingest": ingest_summary[strat_key],
            "metrics": summarize(traces),
        }

    (results_dir / "summary.json").write_text(
        json.dumps(all_summaries, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Export 10 sorted source page positions for manual Chunk Coherence audit
    sorted_pages = sorted(pages, key=lambda p: (p.document_id, p.page))[:10]
    coherence_samples = []
    for p in sorted_pages:
        sample_entry: dict[str, Any] = {
            "document_id": p.document_id,
            "page": p.page,
            "source": p.source,
            "chunks": {},
        }
        for strat_key in ("A", "B", "C"):
            sample_entry["chunks"][strat_key] = [
                {"chunk_id": c.chunk_id, "text": c.text}
                for c in chunks_by_strategy[strat_key]
                if c.document_id == p.document_id and c.page == p.page
            ]
        coherence_samples.append(sample_entry)
    (results_dir / "coherence_samples.json").write_text(
        json.dumps(coherence_samples, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    # Export 5 test questions' top-3 results for manual Grounding & Metadata audit
    grounding_samples = {
        strat_key: [t for t in traces_by_strategy[strat_key] if t["split"] == "test"]
        for strat_key in ("A", "B", "C")
    }
    (results_dir / "grounding_samples.json").write_text(
        json.dumps(grounding_samples, indent=2, ensure_ascii=False), encoding="utf-8"
    )

    print("\n[5/5] Benchmark Summary:")
    print(f"{'Strategy':<10} {'Split':<6} {'Hit@1':<12} {'Hit@3':<12} {'FilterOK':<10}")
    for strat_key in ("A", "B", "C"):
        m = all_summaries[strat_key]["metrics"]
        for split in ("dev", "test"):
            s = m[split]
            h1 = f"{s['hit_at_1_count']}/{s['total']} ({s['hit_at_1_rate']*100:.1f}%)"
            h3 = f"{s['hit_at_3_count']}/{s['total']} ({s['hit_at_3_rate']*100:.1f}%)"
            flt = f"{s['filter_compliant_count']}/{s['filter_total']}" if s["filter_total"] else "N/A"
            print(f"{strat_key:<10} {split:<6} {h1:<12} {h3:<12} {flt:<10}")

    print(f"\nResults saved to: {results_dir}")


if __name__ == "__main__":
    main()
