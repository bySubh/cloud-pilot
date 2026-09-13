from pathlib import Path

from app.rag.chroma_store import APP_CODE_COLLECTION, INFRA_TEMPLATES_COLLECTION, get_vector_store
from app.rag.ingest import (
    build_application_context,
    ingest_application_source,
    ingest_infra_templates,
    retrieve_infra_templates,
)


def test_ingest_infra_templates_populates_collection():
    count = ingest_infra_templates()
    assert count >= 3
    store = get_vector_store()
    assert store.count(INFRA_TEMPLATES_COLLECTION) == count


def test_retrieve_infra_templates_returns_relevant_metadata():
    ingest_infra_templates()
    results = retrieve_infra_templates(["google_storage_bucket"])
    assert len(results) >= 1
    assert all("resource_type" in r["metadata"] for r in results)


def test_ingest_application_source_missing_path_raises(tmp_path):
    missing = tmp_path / "does-not-exist"
    try:
        ingest_application_source(str(missing), run_id="run1")
        assert False, "expected FileNotFoundError"
    except FileNotFoundError:
        pass


def test_ingest_application_source_and_metadata_filtering(tmp_path):
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "main.py").write_text(
        "import fastapi\n\n"
        "def get_user():\n"
        "    return {'id': 1}\n\n"
        "class Database:\n"
        "    def connect(self):\n"
        "        return 'postgres://localhost/db'\n"
    )
    ignored_dir = app_dir / "node_modules"
    ignored_dir.mkdir()
    (ignored_dir / "ignored.js").write_text("should not be ingested")

    count = ingest_application_source(str(app_dir), run_id="run-42")
    assert count >= 1

    store = get_vector_store()
    # metadata filtering: only chunks tagged with this run_id are returned
    hits = store.query(APP_CODE_COLLECTION, "database connection", n_results=5, where={"run_id": "run-42"})
    assert all(h["metadata"]["run_id"] == "run-42" for h in hits)
    assert all("node_modules" not in h["metadata"]["source_file"] for h in hits)
    # new metadata fields required by the corrections
    assert all("language" in h["metadata"] and h["metadata"]["language"] == "python" for h in hits)
    assert all("chunk_type" in h["metadata"] for h in hits)


def test_ingest_ignores_env_and_credential_files(tmp_path):
    app_dir = tmp_path / "app_secrets"
    app_dir.mkdir()
    (app_dir / "config.py").write_text("SETTING = 1\n")
    (app_dir / ".env").write_text("SECRET_KEY=super-secret-value\n")
    (app_dir / ".env.local").write_text("SECRET_KEY=super-secret-value\n")
    (app_dir / "service-account.json").write_text('{"private_key": "not-a-real-key"}')
    (app_dir / "credentials.json").write_text('{"token": "not-a-real-token"}')
    # Even with a *supported* extension, filename-pattern matching should
    # still exclude these - this is the case SUPPORTED_EXTENSIONS alone
    # would not catch.
    (app_dir / "credentials.py").write_text("API_TOKEN = 'should-not-be-ingested'\n")

    count = ingest_application_source(str(app_dir), run_id="run-secrets")
    assert count >= 1  # config.py still gets ingested

    store = get_vector_store()
    hits = store.query(APP_CODE_COLLECTION, "settings token", n_results=20, where={"run_id": "run-secrets"})
    for h in hits:
        assert ".env" not in h["metadata"]["source_file"]
        assert "credentials" not in h["metadata"]["source_file"]
        assert "service-account" not in h["metadata"]["source_file"]
        assert "super-secret-value" not in h["document"]
        assert "should-not-be-ingested" not in h["document"]


def test_redact_secrets_masks_inline_secret_looking_lines():
    from app.rag.chunker import redact_secrets

    content = "API_KEY = 'abc123'\nnormal_line = 1\n"
    redacted = redact_secrets(content)
    assert "abc123" not in redacted
    assert "normal_line = 1" in redacted


def test_build_application_context_detects_signals(tmp_path):
    app_dir = tmp_path / "app2"
    app_dir.mkdir()
    (app_dir / "server.py").write_text(
        "from fastapi import FastAPI\n"
        "import psycopg2\n\n"
        "app = FastAPI()\n\n"
        "@app.get('/users')\n"
        "def list_users():\n"
        "    return []\n"
    )
    ingest_application_source(str(app_dir), run_id="run-ctx")
    context = build_application_context("run-ctx", "Create infra for this web app")
    assert "FastAPI" in context.detected_frameworks
    assert context.summary != ""
    assert context.has_context is True
    assert context.retrieved_chunks
    assert context.retrieved_chunks[0].source_file.endswith("server.py")
