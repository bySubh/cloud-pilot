"""
Core domain models shared across the graph, agents, and API layer.

These are intentionally structured (never raw dicts) so that every stage of
the pipeline has a typed contract with the ones before/after it.
"""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field


# ---------------------------------------------------------------------------
# Architect output
# ---------------------------------------------------------------------------
class ResourceSpec(BaseModel):
    """A single GCP resource the Architect has decided is required."""

    resource_type: str = Field(..., description="e.g. google_storage_bucket")
    purpose: str = Field(..., description="Human readable purpose of the resource")
    reason: str = Field(..., description="Why the request implies this resource")
    name_hint: Optional[str] = Field(default=None, description="Suggested resource name")


class ArchitectOutput(BaseModel):
    resources: list[ResourceSpec] = Field(default_factory=list)
    notes: Optional[str] = None


class RetrievedCodeChunk(BaseModel):
    """A single application-source chunk retrieved from the app_code RAG
    collection, with the metadata needed to show provenance in the UI."""

    content: str
    source_file: str
    language: str = ""
    chunk_type: str = ""  # "function" | "class" | "module" | "line_window"
    symbol: str = ""
    start_line: int = 0
    end_line: int = 0


class ApplicationContext(BaseModel):
    """Context extracted from an optional application source directory via RAG."""

    summary: str = ""
    detected_frameworks: list[str] = Field(default_factory=list)
    detected_databases: list[str] = Field(default_factory=list)
    detected_storage_usage: list[str] = Field(default_factory=list)
    detected_endpoints: list[str] = Field(default_factory=list)
    retrieved_snippets: list[str] = Field(default_factory=list)
    retrieved_chunks: list[RetrievedCodeChunk] = Field(default_factory=list)

    @property
    def has_context(self) -> bool:
        return bool(self.retrieved_chunks)


class RetrievedInfraTemplate(BaseModel):
    """A single infra_templates RAG hit, with its metadata."""

    content: str
    resource_type: str = "unknown"
    provider: str = "google"
    service: str = ""
    security_notes: str = ""
    source_file: str = ""


# ---------------------------------------------------------------------------
# Terraform generation provenance
# ---------------------------------------------------------------------------
class GenerationMethod(str, Enum):
    """How a given Terraform file bundle was actually produced. Never
    reported dishonestly - the pipeline always records which of these
    actually happened."""

    VERTEX_LLM = "vertex_llm"
    MOCK_LLM = "mock_llm"
    DETERMINISTIC_FALLBACK = "deterministic_fallback"
    UNKNOWN = "unknown"


# ---------------------------------------------------------------------------
# Terraform artifacts
# ---------------------------------------------------------------------------
class TerraformFiles(BaseModel):
    main_tf: str = ""
    variables_tf: str = ""
    providers_tf: str = ""
    terraform_tfvars_example: str = ""

    # Provenance metadata - not a Terraform file, purely for transparency in
    # the API/UI about how the HCL above was actually produced.
    generated_by: GenerationMethod = GenerationMethod.UNKNOWN
    generation_notes: list[str] = Field(default_factory=list)

    def as_dict(self) -> dict[str, str]:
        return {
            "main.tf": self.main_tf,
            "variables.tf": self.variables_tf,
            "providers.tf": self.providers_tf,
            "terraform.tfvars.example": self.terraform_tfvars_example,
        }


class TerraformExecutionMode(str, Enum):
    """Whether Terraform validation actually invoked the real `terraform`
    binary, or a local mock validator was used because it isn't installed."""

    REAL = "real"
    MOCK = "mock"
    UNAVAILABLE = "unavailable"


class TerraformCommandResult(BaseModel):
    command: list[str]
    return_code: int
    stdout: str
    stderr: str
    duration_seconds: float
    workspace: str
    timed_out: bool = False
    mode: TerraformExecutionMode = TerraformExecutionMode.REAL


class TerraformPlanOutcome(BaseModel):
    init_result: Optional[TerraformCommandResult] = None
    plan_result: Optional[TerraformCommandResult] = None
    success: bool = False
    mode: TerraformExecutionMode = TerraformExecutionMode.REAL

    @property
    def error_summary(self) -> str:
        parts = []
        if self.init_result and self.init_result.return_code != 0:
            parts.append(f"terraform init failed:\n{self.init_result.stderr or self.init_result.stdout}")
        if self.plan_result and self.plan_result.return_code != 0:
            parts.append(f"terraform plan failed:\n{self.plan_result.stderr or self.plan_result.stdout}")
        return "\n".join(parts)


# ---------------------------------------------------------------------------
# Policy / Checkov
# ---------------------------------------------------------------------------
class Severity(str, Enum):
    UNKNOWN = "UNKNOWN"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"


class ComplianceViolation(BaseModel):
    rule_id: str
    severity: str = Severity.UNKNOWN.value
    resource_address: str
    description: str
    remediation_hint: str = ""


class PolicyEngineStatus(str, Enum):
    """Precisely what happened when policy validation ran. This is the
    single source of truth the API/UI must use instead of inferring
    "Checkov passed" from `success` alone."""

    CHECKOV_RAN = "checkov_ran"
    CHECKOV_UNAVAILABLE_FALLBACK_USED = "checkov_unavailable_fallback_used"
    CHECKOV_CRASHED_FALLBACK_USED = "checkov_crashed_fallback_used"
    FALLBACK_CRASHED = "fallback_crashed"


class PolicyResult(BaseModel):
    source: str = Field(default="checkov", description="'checkov' or 'fallback'")
    status: PolicyEngineStatus = PolicyEngineStatus.CHECKOV_RAN
    passed_checks: int = 0
    failed_checks: int = 0
    skipped_checks: int = 0
    violations: list[ComplianceViolation] = Field(default_factory=list)
    checkov_available: bool = True
    # `crashed` means an actual validator (Checkov OR the fallback) errored
    # unexpectedly - it does NOT mean "Checkov simply isn't installed".
    crashed: bool = False
    raw_error: Optional[str] = None

    @property
    def success(self) -> bool:
        return self.failed_checks == 0 and not self.crashed

    @property
    def status_message(self) -> str:
        if self.status == PolicyEngineStatus.CHECKOV_RAN:
            return f"Checkov {'passed' if self.success else 'found violations'} ({self.failed_checks} failed, {self.passed_checks} passed)."
        if self.status == PolicyEngineStatus.CHECKOV_UNAVAILABLE_FALLBACK_USED:
            return (
                f"Checkov is not installed - fallback policy validator "
                f"{'passed' if self.success else 'found violations'} ({self.failed_checks} finding(s))."
            )
        if self.status == PolicyEngineStatus.CHECKOV_CRASHED_FALLBACK_USED:
            return (
                f"Checkov crashed ({self.raw_error}) - fallback policy validator "
                f"{'passed' if self.success else 'found violations'} ({self.failed_checks} finding(s))."
            )
        return f"Fallback policy validator itself crashed: {self.raw_error}"


# ---------------------------------------------------------------------------
# Healing
# ---------------------------------------------------------------------------
class HealAttemptRecord(BaseModel):
    attempt: int
    terraform_status: str  # "success" | "failed"
    violations: int
    errors: list[str] = Field(default_factory=list)


# ---------------------------------------------------------------------------
# Run persistence / API
# ---------------------------------------------------------------------------
class RunStatus(str, Enum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCESS = "success"
    FAILED = "failed"


class RunCreateRequest(BaseModel):
    request: str = Field(..., min_length=3)
    constraints: Optional[str] = ""
    application_source: Optional[str] = Field(
        default=None, description="Optional path to an application source directory for RAG"
    )


class RunCreateResponse(BaseModel):
    run_id: str
    status: RunStatus


class RunResult(BaseModel):
    run_id: str
    status: RunStatus
    heal_attempts: int = 0
    resources: list[ResourceSpec] = Field(default_factory=list)
    terraform_files: dict[str, str] = Field(default_factory=dict)
    terraform_generation_method: GenerationMethod = GenerationMethod.UNKNOWN
    terraform_generation_notes: list[str] = Field(default_factory=list)
    policy_results: dict[str, Any] = Field(default_factory=dict)
    terraform_plan: dict[str, Any] = Field(default_factory=dict)
    heal_history: list[HealAttemptRecord] = Field(default_factory=list)
    workspace: str = ""
    errors: list[str] = Field(default_factory=list)
    request: str = ""
    constraints: str = ""
    created_at: str = ""
    updated_at: str = ""
    current_node: str = ""
