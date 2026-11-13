"""Store and search vectors using PostgreSQL + pgvector."""
import hashlib
import json
from typing import Any

import psycopg
from psycopg.types.json import Jsonb

from .models import Chunk

ALLOWED_FILTER_KEYS = {"company", "year", "doc_type", "document_id", "page"}


def _vector_literal(vec: list[float]) -> str:
    return "[" + ",".join(f"{v:.8f}" for v in vec) + "]"


class PgVectorStore:
    def __init__(self, database_url: str, dimensions: int = 768) -> None:
        if not database_url:
            raise ValueError("database_url must not be empty")
        if dimensions <= 0:
            raise ValueError("dimensions must be positive")
        self.database_url = database_url
        self.dimensions = dimensions

    def _connect(self) -> psycopg.Connection[Any]:
        return psycopg.connect(self.database_url)

    def create_schema(self) -> None:
        """Create chunks table with vector(N) column and source/page/metadata indexes."""
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("CREATE EXTENSION IF NOT EXISTS vector;")
                cur.execute(
                    f"""
                    CREATE TABLE IF NOT EXISTS chunks (
                        chunk_id TEXT NOT NULL,
                        strategy TEXT NOT NULL,
                        document_id TEXT NOT NULL,
                        source TEXT NOT NULL,
                        page INTEGER NOT NULL,
                        text TEXT NOT NULL,
                        content_hash TEXT NOT NULL,
                        metadata JSONB NOT NULL DEFAULT '{{}}'::jsonb,
                        embedding vector({self.dimensions}) NOT NULL,
                        created_at TIMESTAMPTZ DEFAULT NOW(),
                        PRIMARY KEY (strategy, chunk_id)
                    );
                    """
                )
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_strategy ON chunks (strategy);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_doc_strategy ON chunks (strategy, document_id);")
                cur.execute("CREATE INDEX IF NOT EXISTS idx_chunks_metadata ON chunks USING GIN (metadata);")
            conn.commit()

    def count_chunks(self, strategy: str) -> int:
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT COUNT(*) FROM chunks WHERE strategy = %s;", (strategy,))
                row = cur.fetchone()
                return int(row[0]) if row else 0

    def upsert(
        self,
        strategy: str,
        chunks: list[Chunk],
        vectors: list[list[float]],
        *,
        replace_strategy: bool = False,
    ) -> None:
        """Validate lengths/dimensions, upsert stable chunk IDs, and prune obsolete chunks."""
        if len(chunks) != len(vectors):
            raise ValueError(f"Chunk count ({len(chunks)}) != vector count ({len(vectors)})")
        if not chunks:
            return

        for idx, vec in enumerate(vectors):
            if len(vec) != self.dimensions:
                raise ValueError(f"Vector {idx} has dim {len(vec)}, expected {self.dimensions}")

        chunks_by_doc: dict[str, list[str]] = {}
        if not replace_strategy:
            for ch in chunks:
                chunks_by_doc.setdefault(ch.document_id, []).append(ch.chunk_id)

        with self._connect() as conn:
            with conn.cursor() as cur:
                # Upsert chunks
                for ch, vec in zip(chunks, vectors):
                    content_hash = hashlib.sha256(ch.text.encode("utf-8")).hexdigest()[:16]
                    meta_payload = {
                        **ch.metadata,
                        "document_id": ch.document_id,
                        "source": ch.source,
                        "page": ch.page,
                    }
                    cur.execute(
                        """
                        INSERT INTO chunks (
                            chunk_id, strategy, document_id, source, page,
                            text, content_hash, metadata, embedding
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s::vector)
                        ON CONFLICT (strategy, chunk_id) DO UPDATE SET
                            document_id = EXCLUDED.document_id,
                            source = EXCLUDED.source,
                            page = EXCLUDED.page,
                            text = EXCLUDED.text,
                            content_hash = EXCLUDED.content_hash,
                            metadata = EXCLUDED.metadata,
                            embedding = EXCLUDED.embedding;
                        """,
                        (
                            ch.chunk_id,
                            strategy,
                            ch.document_id,
                            ch.source,
                            ch.page,
                            ch.text,
                            content_hash,
                            Jsonb(meta_payload),
                            _vector_literal(vec),
                        ),
                    )

                if replace_strategy:
                    cur.execute(
                        "DELETE FROM chunks WHERE strategy = %s AND NOT (chunk_id = ANY(%s));",
                        (strategy, [chunk.chunk_id for chunk in chunks]),
                    )
                # A partial upsert only prunes chunks for the supplied documents.
                for doc_id, active_ids in chunks_by_doc.items():
                    cur.execute(
                        """
                        DELETE FROM chunks
                        WHERE strategy = %s
                          AND document_id = %s
                          AND NOT (chunk_id = ANY(%s));
                        """,
                        (strategy, doc_id, active_ids),
                    )
            conn.commit()

    def search(
        self,
        strategy: str,
        query_vector: list[float],
        top_k: int = 3,
        filters: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Filter permitted metadata in SQL, rank by cosine distance (<=>), and return top-k."""
        if len(query_vector) != self.dimensions:
            raise ValueError(f"Query vector dim {len(query_vector)} != expected {self.dimensions}")

        clean_filters: dict[str, Any] = {}
        if filters:
            for k, v in filters.items():
                if k not in ALLOWED_FILTER_KEYS:
                    raise ValueError(f"Unsupported filter key: {k}")
                clean_filters[k] = v

        vec_str = _vector_literal(query_vector)
        where_clauses = ["strategy = %s"]
        params: list[Any] = [vec_str, strategy]

        if clean_filters:
            where_clauses.append("metadata @> %s::jsonb")
            params.append(json.dumps(clean_filters))

        params.append(vec_str)
        params.append(top_k)

        sql = f"""
            SELECT
                chunk_id,
                document_id,
                source,
                page,
                text,
                metadata,
                1 - (embedding <=> %s::vector) AS cosine_similarity
            FROM chunks
            WHERE {' AND '.join(where_clauses)}
            ORDER BY embedding <=> %s::vector ASC
            LIMIT %s;
        """

        results: list[dict[str, Any]] = []
        with self._connect() as conn:
            with conn.cursor() as cur:
                cur.execute(sql, params)
                for row in cur.fetchall():
                    results.append(
                        {
                            "chunk_id": row[0],
                            "document_id": row[1],
                            "source": row[2],
                            "page": int(row[3]),
                            "text": row[4],
                            "metadata": row[5],
                            "score": round(float(row[6]), 6),
                        }
                    )
        return results
