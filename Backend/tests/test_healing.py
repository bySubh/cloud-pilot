from __future__ import annotations

import app.graph.graph as graph_module
from app.graph.graph import run_pipeline_sync
from app.models.schemas import PolicyResult, TerraformCommandResult, TerraformPlanOutcome


def _plan_outcome(success: bool, workspace: str = "/tmp/ws") -> TerraformPlanOutcome:
    result = TerraformCommandResult(
        command=["terraform", "plan"],
        return_code=0 if success else 1,
        stdout="ok" if success else "",
        stderr="" if success else "Error: something failed",
        duration_seconds=0.01,
        workspace=workspace,
    )
    return TerraformPlanOutcome(init_result=result, plan_result=result, success=success)


def _policy_result(success: bool) -> PolicyResult:
    if success:
        return PolicyResult(source="checkov", passed_checks=5, failed_checks=0, violations=[])
    from app.models.schemas import ComplianceViolation

    return PolicyResult(
        source="checkov",
        passed_checks=1,
        failed_checks=1,
        violations=[
            ComplianceViolation(
                rule_id="CKV_GCP_1",
                severity="HIGH",
                resource_address="google_storage_bucket.b",
                description="test violation",
            )
        ],
    )


def test_successful_first_attempt(tmp_workspace, monkeypatch):
    monkeypatch.setattr(graph_module._terraform_runner, "run", lambda ws: (_plan_outcome(True), []))
    monkeypatch.setattr(graph_module._policy_linter, "run", lambda ws: _policy_result(True))

    final_state = run_pipeline_sync("Create a secure GCS bucket", "", "run-success")

    assert final_state.final_status == "success"
    assert final_state.heal_attempts == 0
    assert len(final_state.heal_history) == 1


def test_failed_first_attempt_then_successful_healing(tmp_workspace, monkeypatch):
    outcomes = iter([_plan_outcome(False), _plan_outcome(True)])
    policies = iter([_policy_result(False), _policy_result(True)])

    monkeypatch.setattr(graph_module._terraform_runner, "run", lambda ws: (next(outcomes), []))
    monkeypatch.setattr(graph_module._policy_linter, "run", lambda ws: next(policies))

    final_state = run_pipeline_sync("Create a secure GCS bucket", "", "run-heal-once")

    assert final_state.final_status == "success"
    assert final_state.heal_attempts == 1
    assert len(final_state.heal_history) == 2


def test_failed_twice_results_in_final_failure(tmp_workspace, monkeypatch):
    monkeypatch.setattr(graph_module._terraform_runner, "run", lambda ws: (_plan_outcome(False), []))
    monkeypatch.setattr(graph_module._policy_linter, "run", lambda ws: _policy_result(False))

    final_state = run_pipeline_sync("Create a secure GCS bucket", "", "run-heal-exhausted")

    assert final_state.final_status == "failed"
    assert final_state.heal_attempts == final_state.max_heal_attempts == 2
    # initial attempt + 2 heal attempts = 3 recorded healing-decision entries
    assert len(final_state.heal_history) == 3


def test_architect_is_not_rerun_during_healing(tmp_workspace, monkeypatch):
    call_count = {"n": 0}
    original_run = graph_module._architect.run

    def _counting_run(*args, **kwargs):
        call_count["n"] += 1
        return original_run(*args, **kwargs)

    monkeypatch.setattr(graph_module._architect, "run", _counting_run)
    monkeypatch.setattr(graph_module._terraform_runner, "run", lambda ws: (_plan_outcome(False), []))
    monkeypatch.setattr(graph_module._policy_linter, "run", lambda ws: _policy_result(False))

    run_pipeline_sync("Create a secure GCS bucket", "", "run-architect-once")

    assert call_count["n"] == 1
