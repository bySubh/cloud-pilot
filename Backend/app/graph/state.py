"""
Strongly-typed LangGraph state for a Cloud Pilot run.

LangGraph nodes receive a SentinelState and return a dict of the fields they
changed (the pattern LangGraph expects for partial state updates); the
canonical shape flowing through the whole graph is always this model.
"""
from __future__ import annotations

from typing import Optional

from pydantic import BaseModel, Field

from app.models.schemas import (
    ApplicationContext,
    ComplianceViolation,
    HealAttemptRecord,
    PolicyResult,
    ResourceSpec,
    TerraformFiles,
    TerraformPlanOutcome,
)


class SentinelState(BaseModel):
    """Complete state threaded through the LangGraph workflow for one run."""

    # --- inputs ---
    user_request: str
    constraints: str = ""
    run_id: str

    # --- architecture ---
    resource_specs: list[ResourceSpec] = Field(default_factory=list)
    application_context: ApplicationContext = Field(default_factory=ApplicationContext)

    # --- generated IaC ---
    terraform_files: TerraformFiles = Field(default_factory=TerraformFiles)

    # --- validation ---
    compliance_violations: list[ComplianceViolation] = Field(default_factory=list)
    policy_result: Optional[PolicyResult] = None
    plan_outputs: Optional[TerraformPlanOutcome] = None

    # --- healing ---
    heal_attempts: int = 0
    max_heal_attempts: int = 2
    heal_history: list[HealAttemptRecord] = Field(default_factory=list)

    # --- flags ---
    terraform_success: bool = False
    policy_success: bool = False
    needs_healing: bool = False

    # --- workspace ---
    workspace_path: str = ""

    # --- final ---
    final_status: str = "running"  # queued|running|success|failed
    errors: list[str] = Field(default_factory=list)

    model_config = {"arbitrary_types_allowed": True}

    def add_error(self, node: str, message: str) -> None:
        self.errors.append(f"[{node}] {message}")
