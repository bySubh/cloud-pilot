"""
Conditional edge logic for the Cloud Pilot LangGraph workflow.

Kept separate from graph.py so the healing-decision rule is easy to find,
read, and unit test in isolation.
"""
from __future__ import annotations

from app.graph.state import SentinelState

HEAL = "heal"
END_SUCCESS = "end_success"
END_FAILED = "end_failed"


def decide_after_validation(state: SentinelState) -> str:
    """The single decision point that determines whether the graph loops
    back to the IaC Developer, or terminates.

    Healing is required if terraform plan failed OR Checkov found policy
    violations, AND is only allowed while heal_attempts < max_heal_attempts.
    """
    success = state.terraform_success and state.policy_success

    if success:
        return END_SUCCESS

    if state.heal_attempts < state.max_heal_attempts:
        return HEAL

    return END_FAILED
