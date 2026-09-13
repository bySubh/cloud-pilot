from app.agents.architect import ArchitectAgent
from app.llm.vertex import LLMClient, LLMError


class _StubLLM(LLMClient):
    def __init__(self, response):
        self._response = response

    def generate_text(self, system_prompt, user_prompt):
        return self._response


def test_valid_output_parsing():
    agent = ArchitectAgent(llm_client=None)  # uses MockLLM via fixture
    resources, errors = agent.run("Create infrastructure needing PostgreSQL and object storage")
    assert errors == []
    types = {r.resource_type for r in resources}
    assert "google_sql_database_instance" in types
    assert "google_storage_bucket" in types


def test_malformed_output_is_handled_gracefully():
    agent = ArchitectAgent(llm_client=_StubLLM("not json at all"))
    resources, errors = agent.run("Create infrastructure")
    assert resources == []
    assert len(errors) == 1
    assert "malformed" in errors[0] or "did not return valid JSON" in errors[0] or "failed" in errors[0]


def test_missing_resource_fields_is_handled_gracefully():
    agent = ArchitectAgent(llm_client=_StubLLM('{"resources": [{"resource_type": "google_storage_bucket"}]}'))
    resources, errors = agent.run("Create infrastructure")
    assert resources == []
    assert len(errors) == 1


def test_llm_error_is_handled_gracefully():
    class _FailingLLM(LLMClient):
        def generate_text(self, system_prompt, user_prompt):
            raise LLMError("boom")

    agent = ArchitectAgent(llm_client=_FailingLLM())
    resources, errors = agent.run("Create infrastructure")
    assert resources == []
    assert "Architect LLM call failed" in errors[0]
