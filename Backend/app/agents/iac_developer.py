"""
IaC Developer agent.

Responsibility: turn ResourceSpecs (+ constraints, application RAG context,
retrieved infra-template RAG results, and - during healing - the previous
Terraform files plus the specific Checkov/plan failures) into a Terraform
file bundle.

Generation path (in order of preference):
  1. Real Vertex AI/Gemini (`VertexGeminiClient`) - the LLM is given the full
     context (resource specs, constraints, app context, retrieved templates,
     previous failures/files) and asked to return structured JSON containing
     the four Terraform files. Labeled `vertex_llm`.
  2. The offline `MockLLM` - used automatically when Vertex isn't configured.
     It is still "the LLM step" in this pipeline (it receives the same
     prompt shape and returns the same JSON contract), just without real
     generative capability. Labeled `mock_llm`.
  3. The deterministic generator (`app.terraform.generator`) - a safety net
     used ONLY if step 1/2 raises, returns malformed JSON, or produces HCL
     that fails structural validation. Labeled `deterministic_fallback`.
     This project never silently claims an LLM produced Terraform when the
     deterministic fallback actually did.
"""
from __future__ import annotations

import json
import logging

from app.llm.vertex import LLMClient, LLMError, get_llm_client
from app.models.schemas import ApplicationContext, GenerationMethod, ResourceSpec, TerraformFiles
from app.rag.ingest import retrieve_infra_templates
from app.terraform.generator import (
    HealingContext,
    ensure_terraform_files_are_plan_safe,
    generate_terraform_files,
    validate_terraform_files,
)

logger = logging.getLogger("cloud_pilot")

_SYSTEM_PROMPT = """You are the IaC Developer agent in an infrastructure-as-code \
platform called Cloud Pilot. Your job is to turn a list of GCP ResourceSpecs \
into a secure, syntactically valid Terraform configuration using the \
"google" provider.

You MUST respond with ONLY valid JSON matching this schema and nothing else:
{
  "main_tf": "...",
  "variables_tf": "...",
  "providers_tf": "...",
  "terraform_tfvars_example": "..."
}

Rules:
- Only emit resource blocks for these supported types: google_storage_bucket, \
google_sql_database_instance (with a companion google_sql_database), \
google_compute_instance. Do not invent other resource types.
- Follow secure-by-default conventions: GCS buckets must set \
uniform_bucket_level_access = true and public_access_prevention = "enforced". \
Cloud SQL instances must set ipv4_enabled = false, require_ssl = true, and use \
a private_network. Compute instances must omit access_config (no public IP), \
enable shielded VM options, and use OS Login.
- Every variable declared in variables.tf MUST have a `default` value - \
Terraform is run with `-input=false` and must never prompt interactively.
- providers.tf must declare the google provider and required_providers block.
- If you are given "PREVIOUS TERRAFORM FILES" plus specific errors/violations, \
this is a healing attempt: fix the specific problems described. Modify the \
existing configuration minimally - do not regenerate unrelated resources from \
scratch.
"""


class IacDeveloperAgent:
    def __init__(self, llm_client: LLMClient | None = None) -> None:
        self._llm = llm_client or get_llm_client()

    def generate(
        self,
        resource_specs: list[ResourceSpec],
        constraints: str = "",
        application_context: ApplicationContext | None = None,
        previous_terraform_files: TerraformFiles | None = None,
        previous_terraform_errors: list[str] | None = None,
        previous_policy_violations: list[str] | None = None,
    ) -> tuple[TerraformFiles, list[str]]:
        """Returns (terraform_files, warnings). Never raises - any failure in
        the LLM path falls back to the deterministic generator, and that
        fallback is always recorded in `terraform_files.generated_by` /
        `generation_notes`, never silently."""
        warnings: list[str] = []

        retrieved_templates = self._retrieve_templates(resource_specs, warnings)

        user_prompt = self._build_user_prompt(
            resource_specs=resource_specs,
            constraints=constraints,
            application_context=application_context,
            retrieved_templates=retrieved_templates,
            previous_terraform_files=previous_terraform_files,
            previous_terraform_errors=previous_terraform_errors or [],
            previous_policy_violations=previous_policy_violations or [],
        )

        files = self._try_llm_generation(user_prompt, resource_specs, warnings)

        if files is None:
            files = self._deterministic_fallback(
                resource_specs, constraints, previous_terraform_errors, previous_policy_violations, warnings
            )

        return files, warnings

    # -- LLM path ----------------------------------------------------------
    def _try_llm_generation(
        self, user_prompt: str, resource_specs: list[ResourceSpec], warnings: list[str]
    ) -> TerraformFiles | None:
        client_kind = self._llm.kind
        try:
            raw = self._llm.generate_json(_SYSTEM_PROMPT, user_prompt)
        except LLMError as exc:
            warnings.append(f"{client_kind.value} generation failed, falling back to deterministic generator: {exc}")
            return None

        try:
            candidate = TerraformFiles(
                main_tf=str(raw.get("main_tf", "")),
                variables_tf=str(raw.get("variables_tf", "")),
                providers_tf=str(raw.get("providers_tf", "")),
                terraform_tfvars_example=str(raw.get("terraform_tfvars_example", "")),
                generated_by=client_kind,
            )
        except Exception as exc:  # noqa: BLE001 - malformed structure from the LLM
            warnings.append(f"{client_kind.value} returned malformed Terraform JSON, falling back: {exc}")
            return None

        candidate = ensure_terraform_files_are_plan_safe(candidate)
        problems = validate_terraform_files(candidate, resource_specs=resource_specs)
        if problems:
            warnings.append(
                f"{client_kind.value} output failed structural validation ({'; '.join(problems)}); "
                "falling back to deterministic generator."
            )
            return None

        warnings.append(f"Terraform generated by {client_kind.value}.")
        return candidate

    def _deterministic_fallback(
        self,
        resource_specs: list[ResourceSpec],
        constraints: str,
        previous_terraform_errors: list[str] | None,
        previous_policy_violations: list[str] | None,
        warnings: list[str],
    ) -> TerraformFiles:
        healing_context = None
        if previous_terraform_errors or previous_policy_violations:
            healing_context = HealingContext(
                terraform_errors=previous_terraform_errors or [],
                policy_violations=previous_policy_violations or [],
            )
        files = generate_terraform_files(
            resource_specs=resource_specs,
            constraints=constraints,
            healing_context=healing_context,
        )
        files = files.model_copy(update={"generated_by": GenerationMethod.DETERMINISTIC_FALLBACK})
        warnings.append("Terraform generated by deterministic_fallback (safe offline generator).")
        return files

    # -- RAG retrieval -------------------------------------------------------
    def _retrieve_templates(self, resource_specs: list[ResourceSpec], warnings: list[str]) -> list[dict]:
        resource_types = [spec.resource_type for spec in resource_specs]
        try:
            retrieved = retrieve_infra_templates(resource_types)
            if not retrieved:
                warnings.append("No matching infra templates were retrieved from RAG; relying on prompt rules only.")
            return retrieved
        except Exception as exc:  # noqa: BLE001 - RAG must never break generation
            warnings.append(f"Infra template retrieval failed, continuing without RAG templates: {exc}")
            return []

    # -- prompt construction -------------------------------------------------
    def _build_user_prompt(
        self,
        resource_specs: list[ResourceSpec],
        constraints: str,
        application_context: ApplicationContext | None,
        retrieved_templates: list[dict],
        previous_terraform_files: TerraformFiles | None,
        previous_terraform_errors: list[str],
        previous_policy_violations: list[str],
    ) -> str:
        parts: list[str] = []

        specs_json = json.dumps([spec.model_dump() for spec in resource_specs], indent=2)
        parts.append(
            "Resource specs to implement:\n"
            f"RESOURCE_SPECS_JSON:\n{specs_json}\n"
        )

        if constraints:
            parts.append(f"Constraints:\n{constraints}\n")

        if application_context and application_context.summary:
            parts.append(
                "Application context retrieved from the user's source code (use it to inform naming/sizing, "
                "never to invent unsupported resources):\n"
                f"{application_context.summary}\n"
                f"Frameworks: {', '.join(application_context.detected_frameworks) or 'none'}\n"
                f"Database signals: {', '.join(application_context.detected_databases) or 'none'}\n"
                f"Storage signals: {', '.join(application_context.detected_storage_usage) or 'none'}\n"
            )
            if application_context.retrieved_chunks:
                chunk_text = "\n\n".join(
                    f"# {c.source_file} ({c.language}/{c.chunk_type}):\n{c.content[:300]}"
                    for c in application_context.retrieved_chunks[:3]
                )
                parts.append(f"Relevant application code excerpts:\n{chunk_text}")

        if retrieved_templates:
            template_text = "\n\n".join(
                f"# --- template for {hit['metadata'].get('resource_type', 'unknown')} "
                f"({hit['metadata'].get('security_notes', '')}) ---\n{hit['document']}"
                for hit in retrieved_templates[:4]
            )
            parts.append(f"Retrieved secure infra templates (follow these conventions):\n{template_text}\n")

        if previous_terraform_files is not None and (previous_terraform_errors or previous_policy_violations):
            parts.append(
                "PREVIOUS TERRAFORM FILES (this is a healing attempt - fix the problems below, "
                "do not discard unrelated resources):\n"
                f"--- main.tf ---\n{previous_terraform_files.main_tf}\n"
                f"--- variables.tf ---\n{previous_terraform_files.variables_tf}\n"
            )
            if previous_terraform_errors:
                parts.append("Previous terraform plan/init errors to fix:\n" + "\n".join(previous_terraform_errors))
            if previous_policy_violations:
                parts.append("Previous Checkov/policy violations to fix:\n" + "\n".join(previous_policy_violations))

        return "\n\n".join(parts)
