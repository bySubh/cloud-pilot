from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from app.config import get_settings
from app.llm.vertex import MockLLM, reset_llm_client_for_tests
from app.rag.chroma_store import InMemoryVectorStore, reset_vector_store_for_tests
from app.rag.embeddings import HashingEmbeddingProvider, reset_embedding_provider_for_tests


@pytest.fixture(autouse=True)
def _isolated_environment(tmp_path, monkeypatch):
    """Every test gets: a mock LLM, an in-memory vector store, a hashing
    embedder, and a fresh temp workspace/db - no external services, no
    shared state between tests."""
    monkeypatch.setenv("DATABASE_URL", "")
    monkeypatch.setenv("SENTINEL_TF_ALLOW_APPLY", "0")
    get_settings.cache_clear()

    reset_embedding_provider_for_tests(HashingEmbeddingProvider())
    reset_vector_store_for_tests(InMemoryVectorStore())
    reset_llm_client_for_tests(MockLLM())

    yield

    reset_llm_client_for_tests(None)
    reset_vector_store_for_tests(None)
    reset_embedding_provider_for_tests(None)
    get_settings.cache_clear()


@pytest.fixture
def tmp_workspace(tmp_path, monkeypatch):
    workspace_dir = tmp_path / "workspace"
    workspace_dir.mkdir()
    import app.config as config_module

    monkeypatch.setattr(config_module, "WORKSPACE_ROOT", workspace_dir)
    import app.terraform.workspace as workspace_module

    monkeypatch.setattr(workspace_module, "WORKSPACE_ROOT", workspace_dir)

    import app.persistence as persistence_module

    monkeypatch.setattr(persistence_module, "WORKSPACE_ROOT", workspace_dir)
    monkeypatch.setattr(persistence_module, "_engine", None)
    monkeypatch.setattr(persistence_module, "_SessionLocal", None)

    return workspace_dir
