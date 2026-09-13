"""
Mock Terraform validator.

Used ONLY when the real `terraform` binary is not installed/on PATH. It
performs local, static structural checks against the generated HCL - brace
balance, presence of a provider block, at least one resource block, and
that every `var.X` reference has a matching `variable "X"` declaration (the
exact class of problem that would otherwise make `terraform init`/`plan`
fail immediately).

This is explicitly NOT a substitute for real Terraform. Every result it
produces is tagged `mode=mock`, and its stdout/stderr always says so in
plain language, so the API/UI never implies that a real GCP plan ran.
"""
from __future__ import annotations

import re
import time
from pathlib import Path

from app.models.schemas import TerraformCommandResult, TerraformExecutionMode, TerraformPlanOutcome

_MOCK_DISCLAIMER = (
    "MOCK VALIDATION: the real `terraform` binary is not installed in this environment. "
    "This is a static structural check of the generated HCL only - it does NOT contact GCP "
    "and does NOT represent a real `terraform plan`."
)


def _check_files(workspace_dir: Path) -> list[str]:
    problems: list[str] = []
    files = {}
    for name in ("main.tf", "variables.tf", "providers.tf"):
        path = workspace_dir / name
        if not path.exists():
            problems.append(f"{name} is missing from the workspace")
            files[name] = ""
        else:
            files[name] = path.read_text(encoding="utf-8")

    for name, content in files.items():
        if content.count("{") != content.count("}"):
            problems.append(f"{name} has unbalanced braces ({content.count('{')} '{{' vs {content.count('}')} '}}')")

    if files.get("providers.tf") and 'provider "google"' not in files["providers.tf"]:
        problems.append('providers.tf is missing a provider "google" block')

    if files.get("main.tf") and 'resource "' not in files["main.tf"]:
        problems.append("main.tf declares no resource blocks")

    declared_vars = set(re.findall(r'variable\s+"(\w+)"', files.get("variables.tf", "")))
    referenced_vars = set(re.findall(r"var\.(\w+)", files.get("main.tf", "") + files.get("providers.tf", "")))
    missing = referenced_vars - declared_vars
    if missing:
        problems.append(f"references var.{{{', '.join(sorted(missing))}}} with no matching variable declaration")

    undefaulted = []
    for match in re.finditer(r'variable\s+"(\w+)"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', files.get("variables.tf", ""), re.DOTALL):
        if "default" not in match.group(2):
            undefaulted.append(match.group(1))
    if undefaulted:
        problems.append(
            f"variables {{{', '.join(undefaulted)}}} have no default - `terraform plan -input=false` would fail "
            "waiting for interactive input"
        )

    return problems


def run_mock_init_and_plan(workspace_dir: Path) -> TerraformPlanOutcome:
    start = time.perf_counter()
    problems = _check_files(workspace_dir)
    duration = time.perf_counter() - start

    success = not problems
    if success:
        stdout = f"{_MOCK_DISCLAIMER}\nAll static structural checks passed (braces balanced, provider present, all variables referenced are declared and defaulted)."
        stderr = ""
        return_code = 0
    else:
        stdout = _MOCK_DISCLAIMER
        stderr = "Mock validation found the following problem(s):\n" + "\n".join(f"- {p}" for p in problems)
        return_code = 1

    command_result = TerraformCommandResult(
        command=["<mock>", "init+plan"],
        return_code=return_code,
        stdout=stdout,
        stderr=stderr,
        duration_seconds=duration,
        workspace=str(workspace_dir),
        timed_out=False,
        mode=TerraformExecutionMode.MOCK,
    )

    return TerraformPlanOutcome(
        init_result=command_result,
        plan_result=command_result,
        success=success,
        mode=TerraformExecutionMode.MOCK,
    )
