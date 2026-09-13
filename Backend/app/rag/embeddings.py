"""
Embedding provider used by both RAG collections (infra_templates, app_code).

Prefers `sentence-transformers`. If the package or model weights are not
available (offline environment, no internet to download weights), falls
back to a deterministic hashing-based embedding so RAG remains fully
functional (if less semantically rich) without any external dependency.
"""
from __future__ import annotations

import hashlib
import logging
from abc import ABC, abstractmethod

from app.config import get_settings

logger = logging.getLogger("cloud_pilot")

_EMBED_DIM = 384  # matches MiniLM-L6-v2 output dimensionality


class EmbeddingProvider(ABC):
    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        ...


class SentenceTransformerEmbeddingProvider(EmbeddingProvider):
    def __init__(self) -> None:
        from sentence_transformers import SentenceTransformer  # type: ignore

        settings = get_settings()
        self._model = SentenceTransformer(settings.embedding_model_name)

    def embed(self, texts: list[str]) -> list[list[float]]:
        vectors = self._model.encode(texts, convert_to_numpy=True)
        return [v.tolist() for v in vectors]


class HashingEmbeddingProvider(EmbeddingProvider):
    """
    Deterministic, dependency-free embedding fallback.

    Not semantically meaningful in the way a trained model is, but it is
    stable, fast, and good enough to support retrieval-by-similarity in
    environments without network access or the sentence-transformers
    package installed (e.g. minimal CI containers).
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [self._embed_one(t) for t in texts]

    @staticmethod
    def _embed_one(text: str) -> list[float]:
        vector = [0.0] * _EMBED_DIM
        tokens = text.lower().split()
        if not tokens:
            tokens = [""]
        for token in tokens:
            digest = hashlib.sha256(token.encode("utf-8")).digest()
            for i in range(_EMBED_DIM):
                vector[i] += digest[i % len(digest)] / 255.0
        norm = sum(v * v for v in vector) ** 0.5 or 1.0
        return [v / norm for v in vector]


_provider_singleton: EmbeddingProvider | None = None


def get_embedding_provider() -> EmbeddingProvider:
    global _provider_singleton
    if _provider_singleton is not None:
        return _provider_singleton
    try:
        _provider_singleton = SentenceTransformerEmbeddingProvider()
        logger.info("Using SentenceTransformerEmbeddingProvider")
    except Exception as exc:  # noqa: BLE001
        logger.warning("Falling back to HashingEmbeddingProvider: %s", exc)
        _provider_singleton = HashingEmbeddingProvider()
    return _provider_singleton


def reset_embedding_provider_for_tests(provider: EmbeddingProvider | None = None) -> None:
    global _provider_singleton
    _provider_singleton = provider
