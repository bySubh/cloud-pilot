"""
Cloud Architect agent.

Responsibility: understand the user's plain-English infrastructure request
(plus constraints and optional application context) and produce a list of
structured ResourceSpec objects. Never generates Terraform.

Runs exactly once per pipeline execution - it is never re-invoked during
healing.
"""
from __future__ import annotations

from app.llm.vertex import LLMClient, LLMError, get_llm_client
from app.models.schemas import ApplicationContext, ArchitectOutput, ResourceSpec

_SYSTEM_PROMPT = """You are the Cloud Architect agent in an infrastructure-as-code \
platform called Cloud Pilot. Your ONLY job is to read a plain-English \
infrastructure request plus any constraints and application context, and \
decide which Google Cloud Platform resources are required.

You must respond with ONLY valid JSON matching this schema and nothing else:
{
  "resources": [
    {"resource_type": "google_storage_bucket", "purpose": "...", "reason": "...", "name_hint": "..."}
  ],
  "notes": "optional short string"
}

Rules:
Rules:
- Generate only infrastructure explicitly requested by the user or strictly \
required as a direct dependency (e.g. a companion resource with no independent \
purpose). Never add generic application infrastructure (compute, database, \
storage, networking, etc.) that was not requested and is not a direct \
dependency of something that was.
- Only choose from these supported resource_type values: google_storage_bucket, \
google_sql_database_instance, google_compute_instance.
- Do not generate Terraform or HCL of any kind.
- Keep purposes and reasons short (one sentence).
"""


class ArchitectAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or get_llm_client()

    def run(
        self,
        user_request: str,
        constraints: str = "",
        application_context: ApplicationContext | None = None,
    ) -> tuple[list[ResourceSpec], list[str]]:
        """Returns (resource_specs, errors). Never raises - malformed LLM
        output is captured as a handled error and the caller decides how to
        proceed (the graph will mark the run failed with a clear message)."""
        errors: list[str] = []
        user_prompt = self._build_user_prompt(user_request, constraints, application_context)

        try:
            raw = self._llm.generate_json(_SYSTEM_PROMPT, user_prompt)
        except LLMError as exc:
            errors.append(f"Architect LLM call failed: {exc}")
            return [], errors

        try:
            parsed = ArchitectOutput.model_validate(raw)
        except Exception as exc:  # noqa: BLE001 - pydantic ValidationError et al.
            errors.append(f"Architect produced malformed output: {exc}")
            return [], errors

        if not parsed.resources:
            errors.append("Architect returned zero resources for this request.")

        return parsed.resources, errors

    @staticmethod
    def _build_user_prompt(
        user_request: str, constraints: str, application_context: ApplicationContext | None
    ) -> str:
        parts = [f"Infrastructure request:\n{user_request}"]
        if constraints:
            parts.append(f"Constraints:\n{constraints}")
        if application_context and application_context.summary:
            parts.append(
                "Relevant application context retrieved from the supplied source code:\n"
                f"{application_context.summary}\n"
                f"Frameworks detected: {', '.join(application_context.detected_frameworks) or 'none'}\n"
                f"Database signals: {', '.join(application_context.detected_databases) or 'none'}\n"
                f"Storage signals: {', '.join(application_context.detected_storage_usage) or 'none'}\n"
                f"Endpoint files: {', '.join(application_context.detected_endpoints) or 'none'}"
            )
            if application_context.retrieved_chunks:
                chunk_text = "\n\n".join(
                    f"# {c.source_file} ({c.language}, {c.chunk_type}"
                    f"{', ' + c.symbol if c.symbol else ''}, lines {c.start_line}-{c.end_line}):\n{c.content}"
                    for c in application_context.retrieved_chunks[:5]
                )
                parts.append(f"Retrieved application code chunks (for grounding only):\n{chunk_text}")
        return "\n\n".join(parts)
