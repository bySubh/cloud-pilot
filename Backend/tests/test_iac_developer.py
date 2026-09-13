from __future__ import annotations

import json

from app.agents.iac_developer import IacDeveloperAgent
from app.llm.vertex import LLMClient, LLMError, MockLLM
from app.models.schemas import GenerationMethod, ResourceSpec, TerraformFiles


class _StubLLM(LLMClient):
    kind = GenerationMethod.VERTEX_LLM

    def __init__(self, response: str | None = None, captured: dict | None = None, raise_error: bool = False):
        self._response = response
        self._captured = captured
        self._raise_error = raise_error

    def generate_text(self, system_prompt: str, user_prompt: str) -> str:
        if self._captured is not None:
            self._captured["system_prompt"] = system_prompt
            self._captured["user_prompt"] = user_prompt
        if self._raise_error:
            raise LLMError("simulated Vertex failure")
        return self._response


_BUCKET_SPEC = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]

_VALID_LLM_RESPONSE = json.dumps(
    {
        "main_tf": 'resource "google_storage_bucket" "b" {\n'
        '  name                        = var.bucket_name\n'
        "  uniform_bucket_level_access = true\n"
        '  public_access_prevention    = "enforced"\n'
        "}\n",
        "variables_tf": 'variable "bucket_name" {\n  type = string\n  default = "demo-bucket"\n}\n'
        'variable "project_id" {\n  type = string\n  default = "demo-project"\n}\n'
        'variable "region" {\n  type = string\n  default = "us-central1"\n}\n',
        "providers_tf": 'provider "google" {\n  project = var.project_id\n  region = var.region\n}\n',
        "terraform_tfvars_example": 'bucket_name = "demo-bucket"\n',
    }
)


def test_valid_llm_output_is_used_and_labeled_vertex_llm():
    agent = IacDeveloperAgent(llm_client=_StubLLM(response=_VALID_LLM_RESPONSE))
    files, warnings = agent.generate(resource_specs=_BUCKET_SPEC)

    assert files.generated_by == GenerationMethod.VERTEX_LLM
    assert "google_storage_bucket" in files.main_tf
    assert any("vertex_llm" in w for w in warnings)


def test_llm_error_falls_back_to_deterministic_generator():
    agent = IacDeveloperAgent(llm_client=_StubLLM(raise_error=True))
    files, warnings = agent.generate(resource_specs=_BUCKET_SPEC)

    assert files.generated_by == GenerationMethod.DETERMINISTIC_FALLBACK
    assert "google_storage_bucket" in files.main_tf
    assert any("falling back" in w.lower() or "deterministic_fallback" in w for w in warnings)


def test_malformed_json_falls_back_to_deterministic_generator():
    agent = IacDeveloperAgent(llm_client=_StubLLM(response="not valid json at all"))
    files, warnings = agent.generate(resource_specs=_BUCKET_SPEC)

    assert files.generated_by == GenerationMethod.DETERMINISTIC_FALLBACK


def test_structurally_invalid_llm_output_falls_back():
    # Missing the requested resource block entirely -> should fail
    # validate_terraform_files() and trigger the deterministic fallback.
    bad_response = json.dumps(
        {
            "main_tf": "# no resources here\n",
            "variables_tf": 'variable "project_id" {\n  type = string\n  default = "demo-project"\n}\n',
            "providers_tf": 'provider "google" {\n  project = var.project_id\n}\n',
            "terraform_tfvars_example": "# empty\n",
        }
    )
    agent = IacDeveloperAgent(llm_client=_StubLLM(response=bad_response))
    files, warnings = agent.generate(resource_specs=_BUCKET_SPEC)

    assert files.generated_by == GenerationMethod.DETERMINISTIC_FALLBACK
    assert "google_storage_bucket" in files.main_tf  # the fallback actually produced the resource


def test_offline_mock_llm_produces_valid_labeled_terraform():
    agent = IacDeveloperAgent(llm_client=MockLLM())
    files, warnings = agent.generate(resource_specs=_BUCKET_SPEC)

    assert files.generated_by == GenerationMethod.MOCK_LLM
    assert "google_storage_bucket" in files.main_tf
    assert 'provider "google"' in files.providers_tf


def test_every_declared_variable_has_a_default():
    agent = IacDeveloperAgent(llm_client=MockLLM())
    specs = [
        ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test"),
        ResourceSpec(resource_type="google_sql_database_instance", purpose="db", reason="test"),
        ResourceSpec(resource_type="google_compute_instance", purpose="compute", reason="test"),
    ]
    files, _ = agent.generate(resource_specs=specs)

    import re

    for match in re.finditer(r'variable\s+"(\w+)"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', files.variables_tf, re.DOTALL):
        assert "default" in match.group(2), f"variable {match.group(1)} has no default"


def test_healing_prompt_includes_previous_terraform_and_errors():
    captured: dict = {}
    stub = _StubLLM(response=_VALID_LLM_RESPONSE, captured=captured)
    agent = IacDeveloperAgent(llm_client=stub)

    previous_files = TerraformFiles(main_tf="# old main.tf content", variables_tf="# old vars")
    agent.generate(
        resource_specs=_BUCKET_SPEC,
        previous_terraform_files=previous_files,
        previous_terraform_errors=["terraform plan failed: bad config"],
        previous_policy_violations=["bucket is public"],
    )

    assert "PREVIOUS TERRAFORM FILES" in captured["user_prompt"]
    assert "old main.tf content" in captured["user_prompt"]
    assert "bad config" in captured["user_prompt"]
    assert "bucket is public" in captured["user_prompt"]
