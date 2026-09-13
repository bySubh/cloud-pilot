"""
Thin wrapper around Vertex AI / Gemini.

Design goals:
 * The rest of the codebase never talks to the SDK directly - it calls
   `generate_text` / `generate_json` on an `LLMClient`.
 * If Vertex AI credentials / SDK are unavailable (e.g. in CI, tests, or a
   laptop with no GCP project configured), we transparently fall back to a
   deterministic MockLLM so the full pipeline remains runnable and testable
   without any external service.
 * Structured output is requested via a strict "respond with JSON only"
   instruction and parsed defensively - malformed output never crashes the
   pipeline, it is surfaced as a handled error.
 * Every client exposes a `kind` (GenerationMethod) so callers (in
   particular the IaC Developer agent) can honestly record whether a given
   piece of generated content actually came from Vertex AI or from the
   offline mock, instead of ever silently conflating the two.
"""
from __future__ import annotations

import json
import logging
import re
from abc import ABC, abstractmethod
from typing import Any

from app.config import get_settings
from app.models.schemas import GenerationMethod

logger = logging.getLogger("cloud_pilot")


class LLMError(Exception):
    """Raised when an LLM call fails in a way callers must handle explicitly."""


class LLMClient(ABC):
    #: Which GenerationMethod this client represents. Subclasses must set
    #: this so callers never have to guess/isinstance-check to be honest
    #: about provenance.
    kind: GenerationMethod = GenerationMethod.UNKNOWN

    @abstractmethod
    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        ...

    def generate_json(self, system_prompt: str, user_prompt: str) -> dict[str, Any]:
        """Generate a response and parse it as JSON, stripping markdown fences."""
        raw = self.generate_text(system_prompt, user_prompt)
        return _extract_json(raw)


def _extract_json(raw: str) -> dict[str, Any]:
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(json)?", "", cleaned.strip(), flags=re.IGNORECASE).strip()
    cleaned = re.sub(r"```$", "", cleaned.strip()).strip()
    # Some models wrap JSON with prose; extract the first {...} or [...] block.
    match = re.search(r"(\{.*\}|\[.*\])", cleaned, flags=re.DOTALL)
    candidate = match.group(1) if match else cleaned
    try:
        return json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise LLMError(f"LLM did not return valid JSON: {exc}. Raw output: {raw[:500]}") from exc


class VertexGeminiClient(LLMClient):
    """Real Vertex AI-backed Gemini client. Imports the SDK lazily."""

    kind = GenerationMethod.VERTEX_LLM

    def __init__(self) -> None:
        settings = get_settings()
        try:
            import vertexai  # type: ignore
            from vertexai.generative_models import GenerativeModel  # type: ignore
        except ImportError as exc:  # pragma: no cover - exercised only w/o deps
            raise LLMError("google-cloud-aiplatform is not installed") from exc

        if not settings.google_cloud_project:
            raise LLMError("GOOGLE_CLOUD_PROJECT is not configured")

        vertexai.init(project=settings.google_cloud_project, location=settings.google_cloud_location)
        self._model = GenerativeModel(settings.gemini_model)

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        try:
            response = self._model.generate_content(
                [system_prompt, user_prompt],
            )
            return response.text
        except Exception as exc:  # noqa: BLE001 - surface any SDK failure uniformly
            raise LLMError(f"Vertex AI generation failed: {exc}") from exc


_RESOURCE_SPECS_MARKER = "RESOURCE_SPECS_JSON:"


class MockLLM(LLMClient):
    """
    Deterministic offline stand-in for Gemini.

    Used automatically when Vertex AI is not configured/available, and
    explicitly in tests. It implements keyword/structure-based heuristics so
    the pipeline produces sensible, varied output without any network call -
    it is a genuine (if simple) stand-in for the LLM step, NOT the same code
    path as the deterministic-fallback safety net used when even this mock
    fails to produce usable output. Callers must label output from this
    client as `mock_llm`, never `vertex_llm` or `deterministic_fallback`.
    """

    kind = GenerationMethod.MOCK_LLM

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        system_prompt_lower = system_prompt.lower()

        # IaC Developer detection must still inspect the system prompt.
        if "iac developer" in system_prompt_lower or "main_tf" in system_prompt_lower:
            return self._mock_terraform_output(system_prompt, user_prompt)

        # Architect resource matching MUST use ONLY the user's actual
        # infrastructure request - not the "Constraints:" section. Constraints
        # frequently name resources only to EXCLUDE them (e.g. "do not create
        # Cloud Storage buckets or Cloud SQL databases"), and a plain substring
        # match can't distinguish that from a positive request. The request
        # and constraints sections are joined with "\n\nConstraints:" by
        # ArchitectAgent._build_user_prompt, so split there to isolate intent.
        if "architect" in system_prompt_lower:
            request_section = user_prompt.split("\n\nConstraints:")[0]
            return json.dumps(self._mock_architect_output(request_section.lower()))

        return json.dumps({"resources": []})

    # -- heuristics ------------------------------------------------------
    @staticmethod
    def _mock_architect_output(combined: str) -> dict[str, Any]:
        resources: list[dict[str, str]] = []

        if any(k in combined for k in ("postgres", "postgresql", "sql", "database", "relational")):
            resources.append(
                {
                    "resource_type": "google_sql_database_instance",
                    "purpose": "PostgreSQL database",
                    "reason": "Request indicates need for relational persistence",
                    "name_hint": "app-postgres",
                }
            )
        if any(k in combined for k in ("object storage", "storage", "bucket", "file")):
            resources.append(
                {
                    "resource_type": "google_storage_bucket",
                    "purpose": "Object storage",
                    "reason": "Request indicates need for object/file storage",
                    "name_hint": "app-storage-bucket",
                }
            )
        if any(k in combined for k in ("compute", "vm", "instance", "server", "web application")):
            resources.append(
                {
                    "resource_type": "google_compute_instance",
                    "purpose": "Application compute",
                    "reason": "Request indicates need for a compute instance to run the application",
                    "name_hint": "app-web-vm",
                }
            )

        if not resources:
            resources.append(
                {
                    "resource_type": "google_storage_bucket",
                    "purpose": "General purpose storage",
                    "reason": "Fallback resource because no specific resource keywords were detected",
                    "name_hint": "app-default-bucket",
                }
            )

        return {"resources": resources, "notes": "Generated by offline MockLLM (no Vertex AI credentials configured)."}

    @staticmethod
    def _mock_terraform_output(system_prompt: str, user_prompt: str) -> str:
        """
        The mock LLM has no real generative capability, so it "answers" the
        IaC Developer prompt by parsing the machine-readable resource specs
        block the agent embeds in the prompt (see app/agents/iac_developer.py)
        and running them through the same secure-by-default HCL builder the
        deterministic fallback uses. This keeps offline/demo mode fully
        functional while still being a distinct, explicitly-labeled code
        path (`mock_llm`) from the deterministic-fallback safety net, which
        only fires if this step - or a real Vertex call - fails outright.
        """
        # Local import to avoid a module-load-time cycle: app.terraform.generator
        # never imports app.llm, so this is safe as a lazy import here.
        from app.models.schemas import ResourceSpec
        from app.terraform.generator import generate_terraform_files

        match = re.search(rf"{re.escape(_RESOURCE_SPECS_MARKER)}\s*(\[.*?\])", user_prompt, re.DOTALL)
        resource_specs: list[ResourceSpec] = []
        if match:
            try:
                raw_specs = json.loads(match.group(1))
                resource_specs = [ResourceSpec.model_validate(item) for item in raw_specs]
            except Exception as exc:  # noqa: BLE001 - malformed embedded JSON is handled, not fatal
                logger.warning("MockLLM could not parse embedded resource specs: %s", exc)

        constraints_match = re.search(r"Constraints:\n(.*?)(\n\n|$)", user_prompt, re.DOTALL)
        constraints = constraints_match.group(1).strip() if constraints_match else ""

        files = generate_terraform_files(resource_specs=resource_specs, constraints=constraints)
        return json.dumps(
            {
                "main_tf": files.main_tf,
                "variables_tf": files.variables_tf,
                "providers_tf": files.providers_tf,
                "terraform_tfvars_example": files.terraform_tfvars_example,
            }
        )


_client_singleton: LLMClient | None = None


def get_llm_client() -> LLMClient:
    """Return a process-wide LLM client, preferring real Vertex AI, falling
    back to the deterministic mock if unavailable."""
    global _client_singleton
    if _client_singleton is not None:
        return _client_singleton

    settings = get_settings()
    if settings.google_genai_use_vertexai and settings.google_cloud_project:
        try:
            _client_singleton = VertexGeminiClient()
            logger.info("Using VertexGeminiClient for LLM calls")
            return _client_singleton
        except LLMError as exc:
            logger.warning("Falling back to MockLLM: %s", exc)

    _client_singleton = MockLLM()
    return _client_singleton


def reset_llm_client_for_tests(client: LLMClient | None = None) -> None:
    """Test helper: override or reset the process-wide singleton."""
    global _client_singleton
    _client_singleton = client