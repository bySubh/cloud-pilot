"""
Policy Linter agent.

Runs Checkov against the generated Terraform workspace and normalizes the
results into ComplianceViolation objects. If Checkov is not installed or
crashes, a small deterministic fallback validator runs instead - and the
result is clearly labeled as a fallback so the API/UI never claims Checkov
passed when it did not actually run.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from pathlib import Path

from app.config import get_settings
from app.models.schemas import ComplianceViolation, PolicyEngineStatus, PolicyResult


class PolicyLinterAgent:
    def run(self, workspace_dir: Path) -> PolicyResult:
        settings = get_settings()
        binary = settings.checkov_binary

        resolved_binary = shutil.which(binary)
        if resolved_binary is None:
            return self._run_fallback(
                workspace_dir,
                reason=f"Checkov binary '{binary}' not found on PATH",
                status=PolicyEngineStatus.CHECKOV_UNAVAILABLE_FALLBACK_USED,
            )

        checkov_args = ["-d", str(workspace_dir), "--framework", "terraform", "--output", "json", "--compact"]
        if os.name == "nt" and resolved_binary.lower().endswith(".cmd"):
            # On Windows, shutil.which() for a pip-installed console script
            # resolves to a .CMD shim (e.g. Backend\.venv\Scripts\checkov.CMD).
            # subprocess.run() cannot execute .CMD files directly without
            # shell=True (which we avoid for security), so route it through
            # cmd.exe /c instead - this preserves the safe argument-array
            # approach (no string concatenation/shell interpolation).
            command = ["cmd.exe", "/c", resolved_binary, *checkov_args]
        else:
            command = [resolved_binary, *checkov_args]

        try:
            completed = subprocess.run(
                command,
                capture_output=True,
                text=True,
                timeout=settings.terraform_timeout_seconds,
            )
        except subprocess.TimeoutExpired:
            return self._run_fallback(
                workspace_dir, reason="Checkov timed out", status=PolicyEngineStatus.CHECKOV_CRASHED_FALLBACK_USED
            )
        except Exception as exc:  # noqa: BLE001
            return self._run_fallback(
                workspace_dir,
                reason=f"Checkov crashed: {exc}",
                status=PolicyEngineStatus.CHECKOV_CRASHED_FALLBACK_USED,
            )

        # Checkov exits non-zero when it finds failed checks, which is
        # expected and NOT a crash - only treat unparseable output as a
        # crash.
        try:
            payload = json.loads(completed.stdout or "{}")
        except json.JSONDecodeError:
            return self._run_fallback(
                workspace_dir,
                reason=f"Checkov produced unparseable output (return code {completed.returncode}): "
                f"{completed.stderr[:300]}",
                status=PolicyEngineStatus.CHECKOV_CRASHED_FALLBACK_USED,
            )

        return self._parse_checkov_payload(payload)

    # -- real Checkov parsing --------------------------------------------
    def _parse_checkov_payload(self, payload: dict | list) -> PolicyResult:
        # checkov --compact --output json returns either a single dict or a
        # list of dicts (one per framework run).
        runs = payload if isinstance(payload, list) else [payload]

        violations: list[ComplianceViolation] = []
        passed = 0
        skipped = 0

        for run in runs:
            summary = run.get("summary", {})
            passed += summary.get("passed", 0)
            skipped += summary.get("skipped", 0)
            for check in run.get("results", {}).get("failed_checks", []):
                violations.append(
                    ComplianceViolation(
                        rule_id=check.get("check_id", "UNKNOWN"),
                        severity=(check.get("severity") or "UNKNOWN"),
                        resource_address=check.get("resource", "unknown"),
                        description=check.get("check_name", ""),
                        remediation_hint=check.get("guideline", "") or "See Checkov documentation for this rule.",
                    )
                )

        return PolicyResult(
            source="checkov",
            status=PolicyEngineStatus.CHECKOV_RAN,
            passed_checks=passed,
            failed_checks=len(violations),
            skipped_checks=skipped,
            violations=violations,
            checkov_available=True,
            crashed=False,
        )

    # -- fallback validator ------------------------------------------------
    def _run_fallback(self, workspace_dir: Path, reason: str, status: PolicyEngineStatus) -> PolicyResult:
        """
        Run the deterministic fallback rule set. `status` distinguishes WHY
        the fallback is running:
          * CHECKOV_UNAVAILABLE_FALLBACK_USED - Checkov simply isn't
            installed. This is an expected, non-crash condition - `crashed`
            is left False so `success` reflects the fallback's own findings.
          * CHECKOV_CRASHED_FALLBACK_USED - Checkov was invoked but errored
            unexpectedly (timeout, unparseable output, etc). `crashed`
            stays False too (the fallback validator itself still ran fine
            and its findings are trustworthy) - `crashed=True` is reserved
            for when the FALLBACK validator itself blows up, handled below.
        """
        try:
            main_tf_path = workspace_dir / "main.tf"
            content = main_tf_path.read_text(encoding="utf-8") if main_tf_path.exists() else ""
            violations = _fallback_rules(content)
        except Exception as exc:  # noqa: BLE001 - the fallback validator itself crashed
            return PolicyResult(
                source="fallback",
                status=PolicyEngineStatus.FALLBACK_CRASHED,
                passed_checks=0,
                failed_checks=0,
                skipped_checks=0,
                violations=[],
                checkov_available=False,
                crashed=True,
                raw_error=f"{reason}; additionally the fallback validator crashed: {exc}",
            )

        return PolicyResult(
            source="fallback",
            status=status,
            passed_checks=0,
            failed_checks=len(violations),
            skipped_checks=0,
            violations=violations,
            checkov_available=False,
            crashed=False,
            raw_error=reason,
        )


def _fallback_rules(hcl: str) -> list[ComplianceViolation]:
    """Minimal, deterministic policy checks used only when Checkov itself is
    unavailable. Clearly a smaller rule set than real Checkov - this is by
    design and is always labeled 'fallback', never presented as equivalent."""
    violations: list[ComplianceViolation] = []

    for match in re.finditer(r'resource\s+"google_storage_bucket"\s+"(\w+)"\s*{([^}]*)}', hcl, re.DOTALL):
        name, body = match.group(1), match.group(2)
        if "uniform_bucket_level_access" not in body or "true" not in body:
            violations.append(
                ComplianceViolation(
                    rule_id="FALLBACK_GCS_001",
                    severity="HIGH",
                    resource_address=f"google_storage_bucket.{name}",
                    description="GCS bucket missing uniform_bucket_level_access = true",
                    remediation_hint="Set uniform_bucket_level_access = true to disable legacy ACLs.",
                )
            )
        if "public_access_prevention" not in body:
            violations.append(
                ComplianceViolation(
                    rule_id="FALLBACK_GCS_002",
                    severity="CRITICAL",
                    resource_address=f"google_storage_bucket.{name}",
                    description="GCS bucket missing public_access_prevention = \"enforced\"",
                    remediation_hint='Add public_access_prevention = "enforced" to block public exposure.',
                )
            )

    for match in re.finditer(
        r'resource\s+"google_sql_database_instance"\s+"(\w+)"\s*{(.*?)\n}', hcl, re.DOTALL
    ):
        name, body = match.group(1), match.group(2)
        normalized = body.replace(" ", "")
        has_disabled_public_ip = "ipv4_enabled=false" in normalized
        if not has_disabled_public_ip:
            violations.append(
                ComplianceViolation(
                    rule_id="FALLBACK_SQL_001",
                    severity="CRITICAL",
                    resource_address=f"google_sql_database_instance.{name}",
                    description="Cloud SQL instance may be exposed with a public IP",
                    remediation_hint="Set ipv4_enabled = false and use private_network instead.",
                )
            )
        if "require_ssl" not in body:
            violations.append(
                ComplianceViolation(
                    rule_id="FALLBACK_SQL_002",
                    severity="HIGH",
                    resource_address=f"google_sql_database_instance.{name}",
                    description="Cloud SQL instance does not enforce SSL",
                    remediation_hint="Set require_ssl = true in the ip_configuration block.",
                )
            )

    for match in re.finditer(r'resource\s+"google_compute_firewall"\s+"(\w+)"\s*{(.*?)\n}', hcl, re.DOTALL):
        name, body = match.group(1), match.group(2)
        if "0.0.0.0/0" in body:
            violations.append(
                ComplianceViolation(
                    rule_id="FALLBACK_FW_001",
                    severity="CRITICAL",
                    resource_address=f"google_compute_firewall.{name}",
                    description="Firewall rule allows traffic from 0.0.0.0/0",
                    remediation_hint="Restrict source_ranges to known, trusted CIDR blocks.",
                )
            )

    for match in re.finditer(
        r'resource\s+"google_compute_instance"\s+"(\w+)"\s*{(.*?)\n}', hcl, re.DOTALL
    ):
        name, body = match.group(1), match.group(2)
        if "access_config" in body:
            violations.append(
                ComplianceViolation(
                    rule_id="FALLBACK_GCE_001",
                    severity="MEDIUM",
                    resource_address=f"google_compute_instance.{name}",
                    description="Compute instance is assigned a public IP via access_config",
                    remediation_hint="Remove the access_config block unless a public IP is explicitly required.",
                )
            )

    return violations