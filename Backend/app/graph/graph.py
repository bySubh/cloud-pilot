"""
LangGraph workflow wiring for Cloud Pilot.

    User Request -> Architect -> IaC Developer -> Policy Linter
                  -> Terraform Runner -> Healing Decision
                       |success            |failure (budget remaining)
                       v                   v
                      END            IaC Developer (loop)

The Architect runs exactly once (it is not part of the healing loop).
Healing is capped at state.max_heal_attempts (default 2) and can never
loop indefinitely - routing.decide_after_validation is the single source
of truth for that decision.
"""
from __future__ import annotations

from langgraph.graph import END, StateGraph

from app.agents.architect import ArchitectAgent
from app.agents.iac_developer import IacDeveloperAgent
from app.agents.policy_linter import PolicyLinterAgent
from app.agents.terraform_runner import TerraformRunnerAgent
from app.graph.routing import END_FAILED, END_SUCCESS, HEAL, decide_after_validation
from app.graph.state import SentinelState
from app.models.schemas import HealAttemptRecord
from app.rag.ingest import build_application_context
from app.terraform.workspace import create_workspace, read_terraform_files, write_terraform_files
from app.utils.logging import NodeTimer

_architect = ArchitectAgent()
_iac_developer = IacDeveloperAgent()
_policy_linter = PolicyLinterAgent()
_terraform_runner = TerraformRunnerAgent()


def architect_node(state: SentinelState) -> dict:
    with NodeTimer(state.run_id, "architect") as timer:
        application_context = build_application_context(state.run_id, state.user_request)
        resource_specs, errors = _architect.run(state.user_request, state.constraints, application_context)
        for err in errors:
            timer.info(err)
        workspace_dir = create_workspace(state.run_id)
        return {
            "resource_specs": resource_specs,
            "application_context": application_context,
            "workspace_path": str(workspace_dir),
            "errors": state.errors + errors,
        }


def iac_developer_node(state: SentinelState) -> dict:
    with NodeTimer(state.run_id, "iac_developer") as timer:
        previous_tf_errors = [state.plan_outputs.error_summary] if state.plan_outputs and not state.plan_outputs.success else []
        previous_violations = [v.description for v in state.compliance_violations] if state.heal_attempts > 0 else []
        previous_files = state.terraform_files if state.heal_attempts > 0 and state.terraform_files.main_tf else None

        terraform_files, warnings = _iac_developer.generate(
            resource_specs=state.resource_specs,
            constraints=state.constraints,
            application_context=state.application_context,
            previous_terraform_files=previous_files,
            previous_terraform_errors=previous_tf_errors,
            previous_policy_violations=previous_violations,
        )
        for w in warnings:
            timer.info(w)
        timer.info(f"Terraform generation method: {terraform_files.generated_by.value}")

        from pathlib import Path

        write_terraform_files(Path(state.workspace_path), terraform_files)
        return {"terraform_files": terraform_files}


def policy_linter_node(state: SentinelState) -> dict:
    from pathlib import Path

    with NodeTimer(state.run_id, "policy_linter") as timer:
        result = _policy_linter.run(Path(state.workspace_path))
        timer.info(result.status_message)
        return {
            "policy_result": result,
            "compliance_violations": result.violations,
            "policy_success": result.success,
        }


def terraform_runner_node(state: SentinelState) -> dict:
    from pathlib import Path

    with NodeTimer(state.run_id, "terraform_runner") as timer:
        outcome, errors = _terraform_runner.run(Path(state.workspace_path))
        if errors:
            for e in errors:
                timer.info(e)
        timer.info(f"terraform plan success={outcome.success} mode={outcome.mode.value}")
        return {
            "plan_outputs": outcome,
            "terraform_success": outcome.success,
            "errors": state.errors + errors,
        }


def healing_decision_node(state: SentinelState) -> dict:
    with NodeTimer(state.run_id, "healing_decision") as timer:
        decision = decide_after_validation(state)
        record = HealAttemptRecord(
            attempt=state.heal_attempts,
            terraform_status="success" if state.terraform_success else "failed",
            violations=len(state.compliance_violations),
            errors=[state.plan_outputs.error_summary] if state.plan_outputs and not state.plan_outputs.success else [],
        )
        new_history = state.heal_history + [record]

        if decision == END_SUCCESS:
            timer.info("Validation succeeded - ending run")
            return {"needs_healing": False, "final_status": "success", "heal_history": new_history}

        if decision == HEAL:
            timer.info(f"Healing attempt {state.heal_attempts + 1} of {state.max_heal_attempts}")
            return {
                "needs_healing": True,
                "heal_attempts": state.heal_attempts + 1,
                "heal_history": new_history,
            }

        timer.info("Healing budget exhausted - ending run as failed")
        return {"needs_healing": False, "final_status": "failed", "heal_history": new_history}


def _route_from_healing_decision(state: SentinelState) -> str:
    if state.final_status == "success":
        return END_SUCCESS
    if state.needs_healing:
        return HEAL
    return END_FAILED


def build_graph():
    graph = StateGraph(SentinelState)

    graph.add_node("architect", architect_node)
    graph.add_node("iac_developer", iac_developer_node)
    graph.add_node("policy_linter", policy_linter_node)
    graph.add_node("terraform_runner", terraform_runner_node)
    graph.add_node("healing_decision", healing_decision_node)

    graph.set_entry_point("architect")
    graph.add_edge("architect", "iac_developer")
    graph.add_edge("iac_developer", "policy_linter")
    graph.add_edge("policy_linter", "terraform_runner")
    graph.add_edge("terraform_runner", "healing_decision")

    graph.add_conditional_edges(
        "healing_decision",
        _route_from_healing_decision,
        {
            END_SUCCESS: END,
            END_FAILED: END,
            HEAL: "iac_developer",
        },
    )

    return graph.compile()


_compiled_graph = None


def get_compiled_graph():
    global _compiled_graph
    if _compiled_graph is None:
        _compiled_graph = build_graph()
    return _compiled_graph


def run_pipeline_sync(user_request: str, constraints: str, run_id: str) -> SentinelState:
    """Synchronous entry point used by the API background task and CLI/tests."""
    from app.config import get_settings

    initial_state = SentinelState(
        user_request=user_request,
        constraints=constraints or "",
        run_id=run_id,
        max_heal_attempts=get_settings().max_heal_attempts,
        final_status="running",
    )
    compiled = get_compiled_graph()
    result_dict = compiled.invoke(initial_state, config={"recursion_limit": 50})
    return SentinelState.model_validate(result_dict)
