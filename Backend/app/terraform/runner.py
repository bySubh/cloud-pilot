"""
Safe subprocess-based Terraform execution.

Rules enforced here:
  * Only `terraform init` and `terraform plan` are ever run automatically.
  * `terraform apply` is gated behind SENTINEL_TF_ALLOW_APPLY=1 AND a prior
    successful plan - it is never wired to any unauthenticated API route.
  * All commands are argument arrays - never a shell string - so there is no
    command injection surface from user input.
  * Every subprocess call has a timeout.
  * If the `terraform` binary itself is missing, that is reported as a
    structured, handled error rather than raising an unhandled exception.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from pathlib import Path

from app.config import get_settings
from app.models.schemas import TerraformCommandResult, TerraformPlanOutcome


class TerraformNotInstalledError(Exception):
    pass


def _run_command(command: list[str], cwd: Path, timeout: int) -> TerraformCommandResult:
    start = time.perf_counter()
    timed_out = False
    try:
        completed = subprocess.run(
            command,
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        stdout, stderr, return_code = completed.stdout, completed.stderr, completed.returncode
    except subprocess.TimeoutExpired as exc:
        timed_out = True
        stdout = exc.stdout or ""
        stderr = (exc.stderr or "") + f"\nCommand timed out after {timeout}s"
        return_code = -1

    duration = time.perf_counter() - start
    return TerraformCommandResult(
        command=command,
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        workspace=str(cwd),
        timed_out=timed_out,
    )


def run_init_and_plan(workspace_dir: Path) -> TerraformPlanOutcome:
    """Run `terraform init` then `terraform plan` if init succeeded.
    Never raises for expected failure modes; instead returns an outcome
    whose success flag / error_summary the caller inspects."""
    settings = get_settings()
    binary = settings.terraform_binary

    resolved_binary = shutil.which(binary)
    if resolved_binary is None:
        raise TerraformNotInstalledError(
            f"Terraform binary '{binary}' was not found on PATH. Install Terraform or set TERRAFORM_BINARY."
        )

    init_result = _run_command(
        [resolved_binary, "init", "-input=false", "-no-color"], workspace_dir, settings.terraform_timeout_seconds
    )
    if init_result.return_code != 0 or init_result.timed_out:
        return TerraformPlanOutcome(init_result=init_result, plan_result=None, success=False)

    plan_result = _run_command(
        [resolved_binary, "plan", "-input=false", "-no-color", "-out=tfplan.binary"],
        workspace_dir,
        settings.terraform_timeout_seconds,
    )
    success = plan_result.return_code == 0 and not plan_result.timed_out
    return TerraformPlanOutcome(init_result=init_result, plan_result=plan_result, success=success)


def run_apply_if_allowed(workspace_dir: Path, plan_outcome: TerraformPlanOutcome) -> TerraformCommandResult:
    """Explicit opt-in apply path. Never called from the automated healing
    graph or any public API route - available for deliberate CLI/local use
    only, and only after a successful plan."""
    settings = get_settings()
    if not settings.sentinel_tf_allow_apply:
        raise PermissionError("Terraform apply is disabled. Set SENTINEL_TF_ALLOW_APPLY=1 to enable it explicitly.")
    if not plan_outcome.success:
        raise PermissionError("Cannot apply: the most recent terraform plan did not succeed.")

    binary = settings.terraform_binary
    resolved_binary = shutil.which(binary)
    if resolved_binary is None:
        raise TerraformNotInstalledError(f"Terraform binary '{binary}' was not found on PATH.")

    return _run_command(
        [resolved_binary, "apply", "-input=false", "-no-color", "-auto-approve", "tfplan.binary"],
        workspace_dir,
        settings.terraform_timeout_seconds,
    )