"""Ollama nomic-embed-text embedding adapter (local, 768 dimensions)."""
import json
import os
from typing import Protocol
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen


class Embedder(Protocol):
    model_name: str
    dimensions: int

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        """Return one vector per input text, in the same order."""
        ...

    def embed_query(self, question: str) -> list[float]:
        """Return a query vector compatible with the stored document vectors."""
        ...


class MyEmbedder:
    """Embedding provider adapter using local Ollama /api/embed with nomic-embed-text."""

    def __init__(
        self,
        model_name: str = "nomic-embed-text",
        base_url: str | None = None,
        dimensions: int = 768,
        batch_size: int = 16,
    ) -> None:
        self.model_name = model_name or os.getenv("EMBEDDING_MODEL", "nomic-embed-text")
        self.base_url = (base_url or os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")).rstrip("/")
        self.dimensions = dimensions
        self.batch_size = batch_size
        # Verify Ollama is reachable and model exists on init
        self._verify_backend()

    def _verify_backend(self) -> None:
        req = Request(f"{self.base_url}/api/tags", method="GET")
        try:
            with urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, TimeoutError) as exc:
            raise RuntimeError(
                f"Ollama embedding service unreachable at {self.base_url}. "
                "Start smartcampus-ollama or Ollama service before running pipeline."
            ) from exc

        models = [m.get("name", "") for m in data.get("models", [])]
        if not any(m == self.model_name or m.startswith(f"{self.model_name}:") for m in models):
            raise RuntimeError(
                f"Model '{self.model_name}' not found in Ollama at {self.base_url}. "
                f"Available models: {models}"
            )

    def _call_embed(self, inputs: list[str]) -> list[list[float]]:
        payload = json.dumps({"model": self.model_name, "input": inputs}).encode("utf-8")
        req = Request(
            f"{self.base_url}/api/embed",
            data=payload,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(req, timeout=60) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except (URLError, HTTPError, TimeoutError) as exc:
            raise RuntimeError(f"Ollama /api/embed failed for model '{self.model_name}': {exc}") from exc

        vectors = data.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(inputs):
            raise RuntimeError(
                f"Invalid embedding response: expected {len(inputs)} vectors, got {len(vectors or [])}"
            )
        for i, vec in enumerate(vectors):
            if len(vec) != self.dimensions:
                raise RuntimeError(
                    f"Embedding dimension mismatch at item {i}: expected {self.dimensions}, got {len(vec)}"
                )
        return vectors

    def embed_documents(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        all_vectors: list[list[float]] = []
        prefixed = [f"search_document: {t}" for t in texts]
        for start in range(0, len(prefixed), self.batch_size):
            batch = prefixed[start : start + self.batch_size]
            all_vectors.extend(self._call_embed(batch))
        return all_vectors

    def embed_query(self, question: str) -> list[float]:
        vectors = self._call_embed([f"search_query: {question}"])
        return vectors[0]
