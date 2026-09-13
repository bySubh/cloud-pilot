import pytest
from pydantic import ValidationError

from app.graph.state import SentinelState


def test_valid_state_minimal():
    state = SentinelState(user_request="Create a bucket", run_id="abc123")
    assert state.max_heal_attempts == 2
    assert state.heal_attempts == 0
    assert state.final_status == "running"
    assert state.resource_specs == []


def test_default_healing_budget():
    state = SentinelState(user_request="req", run_id="r1")
    assert state.heal_attempts < state.max_heal_attempts


def test_invalid_state_missing_required_fields():
    with pytest.raises(ValidationError):
        SentinelState()  # missing user_request and run_id


def test_add_error_appends_with_node_prefix():
    state = SentinelState(user_request="req", run_id="r1")
    state.add_error("architect", "something went wrong")
    assert state.errors == ["[architect] something went wrong"]
