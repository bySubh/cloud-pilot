"""
Ingestion routines for both RAG collections.

`ingest_infra_templates` seeds the `infra_templates` collection from the
curated example Terraform snippets under Backend/infra_templates/. It is
idempotent - it clears and re-adds so repeated app startups don't duplicate.

`ingest_application_source` walks an optional user-supplied application
directory, chunks supported source files, and stores them in the `app_code`
collection tagged with a run_id so retrieval can be scoped to a single run's
uploaded source tree.
"""
from __future__ import annotations

import logging
from pathlib import Path

from app.config import INFRA_TEMPLATES_ROOT
from app.rag.chroma_store import APP_CODE_COLLECTION, INFRA_TEMPLATES_COLLECTION, get_vector_store
from app.rag.chunker import SUPPORTED_EXTENSIONS, chunk_source_file, is_ignored_path

logger = logging.getLogger("cloud_pilot")

_MAX_APP_FILES = 400
_MAX_FILE_SIZE_BYTES = 300_000


def ingest_infra_templates() -> int:
    """Seed the infra_templates collection from the on-disk example files.
    Returns the number of documents added."""
    store = get_vector_store()
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []
    ids: list[str] = []

    if not INFRA_TEMPLATES_ROOT.exists():
        logger.warning("infra_templates directory not found at %s", INFRA_TEMPLATES_ROOT)
        return 0

    for tf_file in sorted(INFRA_TEMPLATES_ROOT.rglob("*.tf")):
        meta_file = tf_file.with_suffix(".meta.txt")
        content = tf_file.read_text(encoding="utf-8")
        service = tf_file.parent.name
        metadata = {
            "resource_type": _guess_resource_type(content),
            "provider": "google",
            "service": service,
            "security_notes": meta_file.read_text(encoding="utf-8").strip() if meta_file.exists() else "",
            "source_file": str(tf_file.relative_to(INFRA_TEMPLATES_ROOT)),
        }
        documents.append(content)
        metadatas.append(metadata)
        ids.append(f"infra-{service}-{tf_file.stem}")

    store.add(INFRA_TEMPLATES_COLLECTION, documents, metadatas, ids)
    logger.info("Ingested %d infra templates", len(documents))
    return len(documents)


def _guess_resource_type(hcl_content: str) -> str:
    for line in hcl_content.splitlines():
        line = line.strip()
        if line.startswith("resource "):
            parts = line.split('"')
            if len(parts) >= 2:
                return parts[1]
    return "unknown"


def retrieve_infra_templates(resource_types: list[str], n_results: int = 3) -> list[dict]:
    store = get_vector_store()
    results = []
    seen_docs = set()
    for resource_type in resource_types:
        query = f"secure Terraform configuration for {resource_type} on GCP"
        for hit in store.query(INFRA_TEMPLATES_COLLECTION, query, n_results=n_results):
            key = hit["document"][:50]
            if key in seen_docs:
                continue
            seen_docs.add(key)
            results.append(hit)
    return results


def ingest_application_source(app_source_path: str, run_id: str) -> int:
    """Walk, chunk, and embed an application source tree. Returns chunk count.
    Raises FileNotFoundError / NotADirectoryError for bad paths so the caller
    can surface a clean, structured error rather than a stack trace."""
    root = Path(app_source_path).resolve()
    if not root.exists():
        raise FileNotFoundError(f"application_source path does not exist: {app_source_path}")
    if not root.is_dir():
        raise NotADirectoryError(f"application_source path is not a directory: {app_source_path}")

    store = get_vector_store()
    documents: list[str] = []
    metadatas: list[dict[str, str]] = []
    ids: list[str] = []
    files_seen = 0

    for path in root.rglob("*"):
        if files_seen >= _MAX_APP_FILES:
            break
        if not path.is_file():
            continue
        if path.suffix not in SUPPORTED_EXTENSIONS:
            continue
        relative_parts = path.relative_to(root).parts
        if is_ignored_path(relative_parts, path.name):
            continue
        try:
            if path.stat().st_size > _MAX_FILE_SIZE_BYTES:
                continue
            content = path.read_text(encoding="utf-8", errors="ignore")
        except OSError:
            continue

        files_seen += 1
        relative_path = str(path.relative_to(root))
        for chunk in chunk_source_file(relative_path, content):
            documents.append(chunk.content)
            metadatas.append(
                {
                    "run_id": run_id,
                    "source_file": chunk.source_file,
                    "language": chunk.language,
                    "chunk_type": chunk.chunk_type,
                    "start_line": chunk.start_line,
                    "end_line": chunk.end_line,
                    "symbol": chunk.symbol or "",
                }
            )
            ids.append(f"{run_id}-{relative_path}-{chunk.start_line}")

    store.add(APP_CODE_COLLECTION, documents, metadatas, ids)
    logger.info("Ingested %d application code chunks from %d files for run %s", len(documents), files_seen, run_id)
    return len(documents)


_DB_KEYWORDS = ("postgres", "mysql", "sqlalchemy", "database_url", "psycopg2", "prisma", "mongoose")
_STORAGE_KEYWORDS = ("storage", "bucket", "s3", "gcs", "blob")
_ENDPOINT_KEYWORDS = ("@app.route", "@router.", "app.get(", "app.post(", "router.get(", "router.post(")
_FRAMEWORK_KEYWORDS = {
    "fastapi": "FastAPI",
    "flask": "Flask",
    "django": "Django",
    "express": "Express",
    "next": "Next.js",
    "react": "React",
}


def build_application_context(run_id: str, user_request: str) -> "ApplicationContext":  # type: ignore[name-defined]
    """Retrieve app_code chunks relevant to the request and summarize signals.
    This is what makes the application RAG collection genuinely useful to
    the Architect and IaC Developer, rather than just a store of raw text:
    it returns structured, attributable chunks (with file/language/symbol)
    alongside a derived summary of framework/database/storage/endpoint
    signals."""
    from app.models.schemas import ApplicationContext, RetrievedCodeChunk

    store = get_vector_store()
    hits = store.query(APP_CODE_COLLECTION, user_request, n_results=8, where={"run_id": run_id})
    if not hits:
        return ApplicationContext()

    frameworks: set[str] = set()
    databases: set[str] = set()
    storage: set[str] = set()
    endpoints: set[str] = set()
    snippets: list[str] = []
    retrieved_chunks: list[RetrievedCodeChunk] = []

    for hit in hits:
        text = hit["document"]
        meta = hit["metadata"]
        lowered = text.lower()
        snippets.append(text[:400])
        retrieved_chunks.append(
            RetrievedCodeChunk(
                content=text[:1000],
                source_file=meta.get("source_file", "unknown"),
                language=meta.get("language", ""),
                chunk_type=meta.get("chunk_type", ""),
                symbol=meta.get("symbol", ""),
                start_line=int(meta.get("start_line", 0) or 0),
                end_line=int(meta.get("end_line", 0) or 0),
            )
        )
        for keyword, label in _FRAMEWORK_KEYWORDS.items():
            if keyword in lowered:
                frameworks.add(label)
        for keyword in _DB_KEYWORDS:
            if keyword in lowered:
                databases.add(keyword)
        for keyword in _STORAGE_KEYWORDS:
            if keyword in lowered:
                storage.add(keyword)
        for keyword in _ENDPOINT_KEYWORDS:
            if keyword in text:
                endpoints.add(meta.get("source_file", "unknown"))

    summary = (
        f"Detected {len(frameworks)} framework(s), {len(databases)} database signal(s), "
        f"{len(storage)} storage signal(s) across {len(hits)} relevant code chunks."
    )

    return ApplicationContext(
        summary=summary,
        detected_frameworks=sorted(frameworks),
        detected_databases=sorted(databases),
        detected_storage_usage=sorted(storage),
        detected_endpoints=sorted(endpoints),
        retrieved_snippets=snippets,
        retrieved_chunks=retrieved_chunks,
    )
