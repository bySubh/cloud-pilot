from pathlib import Path

import pytest

from app.models.schemas import ResourceSpec
from app.terraform.generator import generate_terraform_files
from app.terraform.runner import TerraformNotInstalledError, run_init_and_plan
from app.terraform.workspace import (
    UnsafeWorkspacePathError,
    create_workspace,
    read_terraform_files,
    write_terraform_files,
)


def test_workspace_creation_is_isolated_per_run(tmp_workspace):
    ws1 = create_workspace("run-a")
    ws2 = create_workspace("run-b")
    assert ws1 != ws2
    assert ws1.exists() and ws2.exists()
    assert ws1.parent == tmp_workspace


def test_write_and_read_terraform_files_roundtrip(tmp_workspace):
    ws = create_workspace("run-c")
    specs = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]
    files = generate_terraform_files(specs)
    write_terraform_files(ws, files)

    read_back = read_terraform_files(ws)
    assert "google_storage_bucket" in read_back.main_tf
    assert read_back.providers_tf == files.providers_tf


def test_write_rejects_unsafe_filenames(tmp_workspace):
    ws = create_workspace("run-d")
    from app.terraform.workspace import _validate_within_workspace

    with pytest.raises(UnsafeWorkspacePathError):
        _validate_within_workspace(ws, "../../etc/passwd")


def test_generator_produces_secure_bucket_defaults():
    specs = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]
    files = generate_terraform_files(specs)
    assert "uniform_bucket_level_access = true" in files.main_tf
    assert 'public_access_prevention    = "enforced"' in files.main_tf


def test_generator_produces_secure_sql_defaults():
    specs = [ResourceSpec(resource_type="google_sql_database_instance", purpose="db", reason="test")]
    files = generate_terraform_files(specs)
    assert "ipv4_enabled    = false" in files.main_tf
    assert "require_ssl     = true" in files.main_tf


def test_generator_flags_unsupported_resource_types():
    specs = [ResourceSpec(resource_type="google_bigquery_dataset", purpose="warehouse", reason="test")]
    files = generate_terraform_files(specs)
    assert "not yet supported" in files.main_tf


def test_generator_gives_every_variable_a_default_even_previously_required_ones():
    """Regression test: project_id, vpc_network_self_link and
    service_account_email used to have no default, which would make
    `terraform plan -input=false` fail outright."""
    specs = [
        ResourceSpec(resource_type="google_sql_database_instance", purpose="db", reason="test"),
        ResourceSpec(resource_type="google_compute_instance", purpose="compute", reason="test"),
    ]
    files = generate_terraform_files(specs)

    import re

    for match in re.finditer(r'variable\s+"(\w+)"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', files.variables_tf, re.DOTALL):
        assert "default" in match.group(2), f"variable {match.group(1)} has no default"


def test_ensure_terraform_files_are_plan_safe_patches_missing_defaults():
    from app.models.schemas import TerraformFiles
    from app.terraform.generator import ensure_terraform_files_are_plan_safe

    files = TerraformFiles(
        main_tf='resource "google_storage_bucket" "b" {\n  name = var.bucket_name\n}\n',
        variables_tf='variable "bucket_name" {\n  type = string\n}\n',
        providers_tf='provider "google" {\n  project = var.project_id\n}\n',
        terraform_tfvars_example="",
    )
    patched = ensure_terraform_files_are_plan_safe(files)
    assert "default" in patched.variables_tf
    assert "auto-added" in patched.variables_tf


def test_validate_terraform_files_detects_missing_resource_block():
    from app.models.schemas import TerraformFiles
    from app.terraform.generator import validate_terraform_files

    specs = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]
    files = TerraformFiles(
        main_tf="# nothing here\n",
        variables_tf='variable "project_id" {\n  type = string\n  default = "x"\n}\n',
        providers_tf='provider "google" {\n  project = var.project_id\n}\n',
        terraform_tfvars_example="",
    )
    problems = validate_terraform_files(files, specs)
    assert any("google_storage_bucket" in p for p in problems)


def test_validate_terraform_files_accepts_well_formed_output():
    from app.terraform.generator import validate_terraform_files

    specs = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]
    files = generate_terraform_files(specs)
    problems = validate_terraform_files(files, specs)
    assert problems == []


def test_terraform_runner_agent_uses_mock_mode_when_binary_missing(tmp_workspace, monkeypatch):
    import shutil

    from app.agents.terraform_runner import TerraformRunnerAgent
    from app.models.schemas import TerraformExecutionMode
    from app.terraform.workspace import create_workspace, write_terraform_files

    monkeypatch.setattr(shutil, "which", lambda _: None)

    ws = create_workspace("run-mock")
    specs = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]
    write_terraform_files(ws, generate_terraform_files(specs))

    outcome, errors = TerraformRunnerAgent().run(ws)
    assert outcome.mode == TerraformExecutionMode.MOCK
    assert outcome.success is True
    assert any("mock" in e.lower() for e in errors)


def test_terraform_runner_agent_reports_unavailable_when_mock_disabled(tmp_workspace, monkeypatch):
    import shutil

    from app.agents.terraform_runner import TerraformRunnerAgent
    from app.config import get_settings
    from app.models.schemas import TerraformExecutionMode
    from app.terraform.workspace import create_workspace

    monkeypatch.setattr(shutil, "which", lambda _: None)
    monkeypatch.setenv("SENTINEL_ALLOW_MOCK_TERRAFORM", "0")
    get_settings.cache_clear()

    ws = create_workspace("run-unavailable")
    outcome, errors = TerraformRunnerAgent().run(ws)

    assert outcome.mode == TerraformExecutionMode.UNAVAILABLE
    assert outcome.success is False
    assert any("not performed" in e or "not found" in e for e in errors)

    get_settings.cache_clear()


def test_mock_runner_detects_unbalanced_braces(tmp_workspace):
    from app.terraform.mock_runner import run_mock_init_and_plan
    from app.terraform.workspace import create_workspace

    ws = create_workspace("run-broken")
    (ws / "main.tf").write_text('resource "google_storage_bucket" "b" {\n  name = "b"\n')  # missing closing brace
    (ws / "variables.tf").write_text("")
    (ws / "providers.tf").write_text('provider "google" {}\n')

    outcome = run_mock_init_and_plan(ws)
    assert outcome.success is False
    assert "unbalanced braces" in outcome.plan_result.stderr


def test_terraform_runner_raises_clear_error_when_binary_missing(tmp_workspace, monkeypatch):
    import shutil

    monkeypatch.setattr(shutil, "which", lambda _: None)
    ws = create_workspace("run-e")
    with pytest.raises(TerraformNotInstalledError):
        run_init_and_plan(ws)


def test_terraform_command_uses_argument_array_not_shell(tmp_workspace, monkeypatch):
    """Guard against command-injection regressions: subprocess.run must
    always be called with a list, never shell=True."""
    import app.terraform.runner as runner_module

    captured = {}

    def _fake_run(command, cwd, capture_output, text, timeout):
        captured["command"] = command
        captured["shell_used"] = False

        class _Result:
            stdout = "ok"
            stderr = ""
            returncode = 0

        return _Result()

    monkeypatch.setattr(runner_module.shutil, "which", lambda _: "/usr/bin/terraform")
    monkeypatch.setattr(runner_module.subprocess, "run", _fake_run)

    ws = create_workspace("run-f")
    outcome = run_init_and_plan(ws)
    assert isinstance(captured["command"], list)
    assert outcome.success is True
