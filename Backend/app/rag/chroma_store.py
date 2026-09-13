"""
Chroma-backed vector store wrapper.

Two collections are used by the system:
  * "infra_templates" - curated, secure Terraform example snippets.
  * "app_code"         - chunks of an optionally-supplied application source
                          tree, used to enrich the Architect's context.

If ChromaDB itself cannot be initialized (e.g. package missing, filesystem
permissions), we fall back to a minimal in-memory vector store with the same
interface, so RAG degrades gracefully instead of crashing the pipeline.
"""
from __future__ import annotations

import logging
import uuid
from abc import ABC, abstractmethod
from typing import Any

from app.config import get_settings
from app.rag.embeddings import get_embedding_provider

logger = logging.getLogger("cloud_pilot")

INFRA_TEMPLATES_COLLECTION = "infra_templates"
APP_CODE_COLLECTION = "app_code"


class VectorStore(ABC):
    @abstractmethod
    def add(
        self,
        collection: str,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str] | None = None,
    ) -> None:
        ...

    @abstractmethod
    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Returns a list of {document, metadata, distance} dicts."""

    @abstractmethod
    def count(self, collection: str) -> int:
        ...


class ChromaVectorStore(VectorStore):
    def __init__(self) -> None:
        import chromadb  # type: ignore

        settings = get_settings()
        self._client = chromadb.PersistentClient(path=settings.chroma_persist_directory)
        self._embedder = get_embedding_provider()

    def _get_collection(self, name: str):
        return self._client.get_or_create_collection(name=name)

    def add(
        self,
        collection: str,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str] | None = None,
    ) -> None:
        if not documents:
            return
        col = self._get_collection(collection)
        ids = ids or [str(uuid.uuid4()) for _ in documents]
        embeddings = self._embedder.embed(documents)
        col.add(documents=documents, metadatas=metadatas, ids=ids, embeddings=embeddings)

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        col = self._get_collection(collection)
        if col.count() == 0:
            return []
        embedding = self._embedder.embed([query_text])[0]
        results = col.query(
            query_embeddings=[embedding],
            n_results=min(n_results, max(col.count(), 1)),
            where=where,
        )
        output = []
        docs = results.get("documents", [[]])[0]
        metas = results.get("metadatas", [[]])[0]
        dists = results.get("distances", [[]])[0] if results.get("distances") else [None] * len(docs)
        for doc, meta, dist in zip(docs, metas, dists):
            output.append({"document": doc, "metadata": meta, "distance": dist})
        return output

    def count(self, collection: str) -> int:
        return self._get_collection(collection).count()


class InMemoryVectorStore(VectorStore):
    """Cosine-similarity fallback store, no external dependency required."""

    def __init__(self) -> None:
        self._collections: dict[str, list[dict[str, Any]]] = {}
        self._embedder = get_embedding_provider()

    def add(
        self,
        collection: str,
        documents: list[str],
        metadatas: list[dict[str, Any]],
        ids: list[str] | None = None,
    ) -> None:
        if not documents:
            return
        ids = ids or [str(uuid.uuid4()) for _ in documents]
        embeddings = self._embedder.embed(documents)
        bucket = self._collections.setdefault(collection, [])
        for doc, meta, emb, doc_id in zip(documents, metadatas, embeddings, ids):
            bucket.append({"id": doc_id, "document": doc, "metadata": meta, "embedding": emb})

    def query(
        self,
        collection: str,
        query_text: str,
        n_results: int = 5,
        where: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        bucket = self._collections.get(collection, [])
        if not bucket:
            return []
        query_embedding = self._embedder.embed([query_text])[0]

        def matches_where(meta: dict[str, Any]) -> bool:
            if not where:
                return True
            return all(meta.get(k) == v for k, v in where.items())

        scored = []
        for item in bucket:
            if not matches_where(item["metadata"]):
                continue
            score = _cosine_similarity(query_embedding, item["embedding"])
            scored.append((score, item))
        scored.sort(key=lambda x: x[0], reverse=True)
        top = scored[:n_results]
        return [
            {"document": item["document"], "metadata": item["metadata"], "distance": 1 - score}
            for score, item in top
        ]

    def count(self, collection: str) -> int:
        return len(self._collections.get(collection, []))


def _cosine_similarity(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b))
    norm_a = sum(x * x for x in a) ** 0.5 or 1.0
    norm_b = sum(y * y for y in b) ** 0.5 or 1.0
    return dot / (norm_a * norm_b)


_store_singleton: VectorStore | None = None


def get_vector_store() -> VectorStore:
    global _store_singleton
    if _store_singleton is not None:
        return _store_singleton
    try:
        _store_singleton = ChromaVectorStore()
        logger.info("Using ChromaVectorStore")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to InMemoryVectorStore: %s", exc)
        _store_singleton = InMemoryVectorStore()
    return _store_singleton


def reset_vector_store_for_tests(store: VectorStore | None = None) -> None:
    global _store_singleton
    _store_singleton = store
