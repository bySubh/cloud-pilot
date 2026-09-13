from app.agents.architect import ArchitectAgent
from app.terraform.generator import generate_terraform_files
from app.models.schemas import ResourceSpec


def _types(resources):
    return {r.resource_type for r in resources}


def test_storage_only_request_yields_only_bucket():
    agent = ArchitectAgent()
    resources, errors = agent.run(
        "Create a GCP Cloud Storage bucket with versioning enabled and uniform bucket-level access."
    )
    assert errors == []
    assert _types(resources) == {"google_storage_bucket"}


def test_compute_only_request_yields_only_compute():
    agent = ArchitectAgent()
    resources, errors = agent.run("Create a Compute Engine VM.")
    assert errors == []
    assert _types(resources) == {"google_compute_instance"}


def test_database_only_request_yields_only_sql():
    agent = ArchitectAgent()
    resources, errors = agent.run("Create a PostgreSQL database using Cloud SQL.")
    assert errors == []
    assert _types(resources) == {"google_sql_database_instance"}


def test_vm_and_bucket_request_yields_exactly_those_two():
    agent = ArchitectAgent()
    resources, errors = agent.run("Create a Compute Engine VM and a Cloud Storage bucket.")
    assert errors == []
    assert _types(resources) == {"google_compute_instance", "google_storage_bucket"}


def test_terraform_generated_from_bucket_only_spec_has_no_sql_or_vm():
    specs = [ResourceSpec(resource_type="google_storage_bucket", purpose="storage", reason="test")]
    files = generate_terraform_files(specs)
    assert 'resource "google_storage_bucket"' in files.main_tf
    assert 'resource "google_sql_database_instance"' not in files.main_tf
    assert 'resource "google_compute_instance"' not in files.main_tf