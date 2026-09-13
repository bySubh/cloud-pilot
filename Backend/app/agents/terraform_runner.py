"""
Terraform Runner agent.

Graph-facing wrapper that decides WHICH Terraform validation strategy is
used, and makes that choice honest and visible:

  * `terraform` binary present  -> real `terraform init` + `terraform plan`
    via safe subprocess execution. `TerraformPlanOutcome.mode == "real"`.
  * `terraform` binary missing, mock mode allowed (default) -> a local
    static structural validator (`app.terraform.mock_runner`) runs instead.
    `mode == "mock"`. This is NEVER presented as a real GCP plan - its
    stdout says so explicitly, and the mode field lets the API/UI do the
    same.
  * `terraform` binary missing, mock mode disabled
    (`SENTINEL_ALLOW_MOCK_TERRAFORM=0`) -> validation is skipped entirely
    and reported as `mode == "unavailable"`, `success == False`, with a
    clear structured error - never silently treated as a passing plan.
"""
from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from app.config import get_settings
from app.models.schemas import TerraformCommandResult, TerraformExecutionMode, TerraformPlanOutcome
from app.terraform.mock_runner import run_mock_init_and_plan
from app.terraform.runner import run_init_and_plan

logger = logging.getLogger("cloud_pilot")


class TerraformRunnerAgent:
    def run(self, workspace_dir: Path) -> tuple[TerraformPlanOutcome, list[str]]:
        settings = get_settings()
        errors: list[str] = []

        resolved_binary = shutil.which(settings.terraform_binary)

        # --- TEMPORARY DIAGNOSTIC LOGGING - remove once the PATH mismatch is root-caused ---
        diagnostic_line = (
            f"TEMP DIAGNOSTIC [TerraformRunnerAgent.run]: sys.executable={sys.executable!r} "
            f"cwd={os.getcwd()!r} settings.terraform_binary={settings.terraform_binary!r} "
            f"PATH={os.environ.get('PATH')!r} shutil.which(terraform_binary)={resolved_binary!r}"
        )
        print(diagnostic_line, flush=True)
        logger.warning(diagnostic_line)
        # --- END TEMPORARY DIAGNOSTIC LOGGING ---

        if resolved_binary is not None:
            outcome = run_init_and_plan(workspace_dir)
            return outcome, errors

        if settings.sentinel_allow_mock_terraform:
            errors.append(
                f"Terraform binary '{settings.terraform_binary}' not found on PATH - "
                "using the local mock validator (static checks only, not a real GCP plan)."
            )
            outcome = run_mock_init_and_plan(workspace_dir)
            return outcome, errors

        message = (
            f"Terraform binary '{settings.terraform_binary}' not found on PATH and mock validation is "
            "disabled (SENTINEL_ALLOW_MOCK_TERRAFORM=0). Terraform validation was not performed."
        )
        errors.append(message)
        unavailable_result = TerraformCommandResult(
            command=[settings.terraform_binary, "init"],
            return_code=127,
            stdout="",
            stderr=message,
            duration_seconds=0.0,
            workspace=str(workspace_dir),
            mode=TerraformExecutionMode.UNAVAILABLE,
        )
        outcome = TerraformPlanOutcome(
            init_result=unavailable_result,
            plan_result=None,
            success=False,
            mode=TerraformExecutionMode.UNAVAILABLE,
        )
        return outcome, errors
    