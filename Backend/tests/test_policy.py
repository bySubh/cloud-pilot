from pathlib import Path

from app.agents.policy_linter import PolicyLinterAgent, _fallback_rules
from app.models.schemas import PolicyEngineStatus, PolicyResult


def test_fallback_flags_insecure_bucket():
    hcl = """
resource "google_storage_bucket" "bad" {
  name = "bad-bucket"
}
"""
    violations = _fallback_rules(hcl)
    rule_ids = {v.rule_id for v in violations}
    assert "FALLBACK_GCS_001" in rule_ids
    assert "FALLBACK_GCS_002" in rule_ids


def test_fallback_passes_secure_bucket():
    hcl = """
resource "google_storage_bucket" "good" {
  name                        = "good-bucket"
  uniform_bucket_level_access = true
  public_access_prevention    = "enforced"
}
"""
    violations = _fallback_rules(hcl)
    assert violations == []


def test_fallback_flags_public_sql_and_missing_ssl():
    hcl = """
resource "google_sql_database_instance" "pg" {
  name = "pg"
  settings {
    ip_configuration {
      ipv4_enabled = true
    }
  }
}
"""
    violations = _fallback_rules(hcl)
    rule_ids = {v.rule_id for v in violations}
    assert "FALLBACK_SQL_001" in rule_ids
    assert "FALLBACK_SQL_002" in rule_ids


def test_policy_linter_uses_fallback_when_checkov_missing_is_not_a_crash(tmp_workspace, monkeypatch):
    """Checkov simply not being installed is an expected, non-crash
    condition - the fallback validator's own findings should determine
    `success`, and the status must clearly say 'unavailable', not 'crashed'."""
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    ws = tmp_workspace / "ws1"
    ws.mkdir()
    (ws / "main.tf").write_text('resource "google_storage_bucket" "b" {\n  name = "b"\n}\n')

    result = PolicyLinterAgent().run(ws)
    assert result.source == "fallback"
    assert result.checkov_available is False
    assert result.status == PolicyEngineStatus.CHECKOV_UNAVAILABLE_FALLBACK_USED
    assert result.crashed is False
    assert result.failed_checks > 0
    assert result.success is False  # false because of real findings, not because of a crash
    assert "Checkov is not installed" in result.status_message
    assert "Checkov passed" not in result.status_message


def test_policy_linter_uses_fallback_when_checkov_times_out(tmp_workspace, monkeypatch):
    import shutil
    import subprocess

    monkeypatch.setattr(shutil, "which", lambda _: "/usr/bin/checkov")

    def _raise_timeout(*args, **kwargs):
        raise subprocess.TimeoutExpired(cmd="checkov", timeout=1)

    monkeypatch.setattr("app.agents.policy_linter.subprocess.run", _raise_timeout)

    ws = tmp_workspace / "ws2"
    ws.mkdir()
    (ws / "main.tf").write_text(
        'resource "google_storage_bucket" "b" {\n'
        "  uniform_bucket_level_access = true\n"
        '  public_access_prevention    = "enforced"\n'
        "}\n"
    )

    result = PolicyLinterAgent().run(ws)
    assert result.status == PolicyEngineStatus.CHECKOV_CRASHED_FALLBACK_USED
    assert result.crashed is False  # the fallback itself ran fine
    assert result.success is True  # secure bucket -> fallback finds no violations
    assert "Checkov crashed" in result.status_message


def test_policy_result_success_property():
    result = PolicyResult(source="checkov", passed_checks=5, failed_checks=0, violations=[])
    assert result.success is True

    crashed = PolicyResult(source="fallback", status=PolicyEngineStatus.FALLBACK_CRASHED, crashed=True)
    assert crashed.success is False


def test_status_message_never_claims_checkov_passed_when_it_did_not_run():
    result = PolicyResult(
        source="fallback",
        status=PolicyEngineStatus.CHECKOV_UNAVAILABLE_FALLBACK_USED,
        passed_checks=0,
        failed_checks=0,
        crashed=False,
    )
    assert "Checkov passed" not in result.status_message
    assert "fallback" in result.status_message.lower()
