"""
Deterministic, secure-by-default Terraform generation.

The IaC Developer agent (app/agents/iac_developer.py) is LLM-powered for
resource *naming/selection nuance*, but the actual HCL emission goes through
this deterministic generator. This keeps output syntactically valid and
secure by construction, and makes healing tractable: a known set of
violation/error signatures maps to a known fix, instead of hoping an LLM
free-writes correct HCL under pressure.

Supported resource types (see honesty-in-scope: we do not claim to support
every GCP resource):
  * google_storage_bucket
  * google_sql_database_instance (+ google_sql_database)
  * google_compute_instance
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.models.schemas import ResourceSpec, TerraformFiles

SUPPORTED_RESOURCE_TYPES = {
    "google_storage_bucket",
    "google_sql_database_instance",
    "google_compute_instance",
}


@dataclass
class HealingContext:
    """Feedback fed back into generation during a healing attempt."""

    terraform_errors: list[str]
    policy_violations: list[str]


def _slugify(name: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", name.strip().lower()).strip("-")
    return slug or "resource"


def _placeholder_default_for_type(var_type: str) -> str:
    var_type = var_type.strip().lower()
    if var_type == "number":
        return "0"
    if var_type == "bool":
        return "false"
    if var_type.startswith("list") or var_type.startswith("tuple"):
        return "[]"
    if var_type.startswith("map") or var_type.startswith("object"):
        return "{}"
    return '"demo-placeholder"'


_VARIABLE_BLOCK_RE = re.compile(r'variable\s+"(\w+)"\s*\{([^{}]*(?:\{[^{}]*\}[^{}]*)*)\}', re.DOTALL)
_TYPE_LINE_RE = re.compile(r'type\s*=\s*([^\n]+)')


def ensure_terraform_files_are_plan_safe(files: TerraformFiles) -> TerraformFiles:
    """
    Defense-in-depth safety net applied to ANY Terraform bundle - whether it
    came from a real LLM, the offline mock LLM, or the deterministic
    generator. Terraform is always executed with `-input=false`, so any
    required variable without a default would make `terraform plan` fail
    outright (not merely produce a policy/plan error we can heal from in a
    useful way). This function rewrites `variables.tf` in place, injecting a
    clearly-marked placeholder `default` into any variable block that is
    missing one, so validation can always proceed non-interactively.
    """
    variables_tf = files.variables_tf
    if not variables_tf.strip():
        return files

    def _patch(match: re.Match) -> str:
        var_name = match.group(1)
        body = match.group(2)
        if re.search(r'\bdefault\s*=', body):
            return match.group(0)
        type_match = _TYPE_LINE_RE.search(body)
        var_type = type_match.group(1).strip() if type_match else "string"
        placeholder = _placeholder_default_for_type(var_type)
        patched_body = body.rstrip() + f"\n  default     = {placeholder}  # auto-added: demo placeholder, override for real deployment\n"
        return f'variable "{var_name}" {{{patched_body}}}'

    patched = _VARIABLE_BLOCK_RE.sub(_patch, variables_tf)
    if patched == variables_tf:
        return files
    return files.model_copy(update={"variables_tf": patched})


_ALLOWED_RESOURCE_BLOCK_TYPES = SUPPORTED_RESOURCE_TYPES | {"google_sql_database"}


def validate_terraform_files(files: TerraformFiles, resource_specs: list[ResourceSpec]) -> list[str]:
    """
    Structural sanity checks applied to Terraform produced by an LLM (real
    or mock) before it is trusted. This is NOT a substitute for `terraform
    validate`/`plan` - it exists to catch obviously-broken or hallucinated
    LLM output early so the IaC Developer agent can fall back to the
    deterministic generator instead of handing garbage to the runner.
    Returns a list of problem descriptions; empty means "looks plausible".
    """
    problems: list[str] = []

    for field_name in ("main_tf", "variables_tf", "providers_tf", "terraform_tfvars_example"):
        if not getattr(files, field_name).strip():
            problems.append(f"{field_name} is empty")

    for field_name in ("main_tf", "variables_tf", "providers_tf"):
        content = getattr(files, field_name)
        if content.count("{") != content.count("}"):
            problems.append(f"{field_name} has unbalanced braces")

    if 'provider "google"' not in files.providers_tf:
        problems.append('providers.tf is missing a provider "google" block')

    expected_types = {spec.resource_type for spec in resource_specs if spec.resource_type in SUPPORTED_RESOURCE_TYPES}
    found_types = set(re.findall(r'resource\s+"(\w+)"', files.main_tf))

    for expected in expected_types:
        if expected not in found_types:
            problems.append(f"main.tf is missing an expected resource block for {expected}")

    for found in found_types:
        if found not in _ALLOWED_RESOURCE_BLOCK_TYPES:
            problems.append(f"main.tf contains unsupported/unexpected resource type {found}")

    declared_vars = set(re.findall(r'variable\s+"(\w+)"', files.variables_tf))
    referenced_vars = set(re.findall(r'var\.(\w+)', files.main_tf + files.providers_tf))
    missing_declarations = referenced_vars - declared_vars
    if missing_declarations:
        problems.append(f"references var.{{{', '.join(sorted(missing_declarations))}}} with no matching variable declaration")

    return problems


def generate_terraform_files(
    resource_specs: list[ResourceSpec],
    constraints: str = "",
    healing_context: HealingContext | None = None,
) -> TerraformFiles:
    unsupported = [r for r in resource_specs if r.resource_type not in SUPPORTED_RESOURCE_TYPES]

    main_blocks: list[str] = []
    variable_blocks: list[str] = []
    tfvars_lines: list[str] = [
        '# Demo defaults are baked into variables.tf so `terraform plan -input=false`',
        '# never prompts interactively. Override all of these for a real deployment.',
        'project_id     = "demo-project"',
        'region         = "us-central1"',
        'zone           = "us-central1-a"',
    ]
    declared_variables: set[str] = set()

    def add_variable(name: str, description: str, default: str | None = None, var_type: str = "string") -> None:
        if name in declared_variables:
            return
        declared_variables.add(name)
        default_line = f"\n  default     = {default}" if default is not None else ""
        variable_blocks.append(
            f'variable "{name}" {{\n  description = "{description}"\n  type        = {var_type}{default_line}\n}}'
        )

    add_variable("project_id", "GCP project ID to deploy into", default='"demo-project"')
    add_variable("region", "GCP region", default='"us-central1"')
    add_variable("zone", "GCP zone", default='"us-central1-a"')

    require_private_network = False

    for spec in resource_specs:
        name = _slugify(spec.name_hint or spec.purpose or spec.resource_type)

        if spec.resource_type == "google_storage_bucket":
            var_name = f"{name}_name".replace("-", "_")
            add_variable(var_name, f"Name of the GCS bucket for: {spec.purpose}")
            tfvars_lines.append(f'{var_name}     = "{name}-bucket-CHANGE-ME"')
            main_blocks.append(_bucket_block(name, var_name))

        elif spec.resource_type == "google_sql_database_instance":
            require_private_network = True
            instance_var = f"{name}_instance_name".replace("-", "_")
            db_var = f"{name}_database_name".replace("-", "_")
            tier_var = f"{name}_db_tier".replace("-", "_")
            add_variable(instance_var, f"Name of the Cloud SQL instance for: {spec.purpose}")
            add_variable(db_var, "Name of the application database", default='"appdb"')
            add_variable(tier_var, "Cloud SQL machine tier", default='"db-f1-micro"')
            add_variable(
                "vpc_network_self_link",
                "Self link of the VPC network used for private IP Cloud SQL access (demo default - override for real deployment)",
                default='"projects/demo-project/global/networks/default"',
            )
            tfvars_lines.append(f'{instance_var} = "{name}-pg-CHANGE-ME"')
            main_blocks.append(_cloud_sql_block(name, instance_var, db_var, tier_var))

        elif spec.resource_type == "google_compute_instance":
            instance_var = f"{name}_instance_name".replace("-", "_")
            machine_var = f"{name}_machine_type".replace("-", "_")
            add_variable(instance_var, f"Name of the compute instance for: {spec.purpose}")
            add_variable(machine_var, "GCE machine type", default='"e2-small"')
            add_variable("network", "VPC network name", default='"default"')
            add_variable("subnetwork", "VPC subnetwork name", default='"default"')
            add_variable(
                "service_account_email",
                "Service account email attached to the VM (demo default - override for real deployment)",
                default='"demo-service-account@demo-project.iam.gserviceaccount.com"',
            )
            tfvars_lines.append(f'{instance_var} = "{name}-vm-CHANGE-ME"')
            main_blocks.append(_compute_instance_block(name, instance_var, machine_var))

    if require_private_network:
        tfvars_lines.append('vpc_network_self_link = "projects/CHANGE-ME/global/networks/default"')

    if healing_context:
        main_blocks.append(_healing_comment_block(healing_context))

    if unsupported:
        main_blocks.append(_unsupported_comment_block(unsupported))

    if not main_blocks:
        main_blocks.append("# No resources were generated - no supported resource specs were provided.")

    providers_tf = _providers_tf()
    main_tf = "\n\n".join(main_blocks) + "\n"
    variables_tf = "\n\n".join(variable_blocks) + "\n"
    tfvars_example = "\n".join(tfvars_lines) + "\n"

    result = TerraformFiles(
        main_tf=main_tf,
        variables_tf=variables_tf,
        providers_tf=providers_tf,
        terraform_tfvars_example=tfvars_example,
    )
    return ensure_terraform_files_are_plan_safe(result)


def _providers_tf() -> str:
    return (
        'terraform {\n'
        '  required_version = ">= 1.5.0"\n'
        '  required_providers {\n'
        '    google = {\n'
        '      source  = "hashicorp/google"\n'
        '      version = "~> 5.0"\n'
        "    }\n"
        "  }\n"
        "}\n\n"
        'provider "google" {\n'
        "  project = var.project_id\n"
        "  region  = var.region\n"
        "}\n"
    )


def _bucket_block(name: str, var_name: str) -> str:
    resource_name = name.replace("-", "_")
    return (
        f'resource "google_storage_bucket" "{resource_name}" {{\n'
        f"  name                        = var.{var_name}\n"
        "  location                    = var.region\n"
        "  force_destroy               = false\n"
        "  uniform_bucket_level_access = true\n"
        '  public_access_prevention    = "enforced"\n\n'
        "  versioning {\n"
        "    enabled = true\n"
        "  }\n"
        "}"
    )


def _cloud_sql_block(name: str, instance_var: str, db_var: str, tier_var: str) -> str:
    resource_name = name.replace("-", "_")
    return (
        f'resource "google_sql_database_instance" "{resource_name}" {{\n'
        f"  name             = var.{instance_var}\n"
        '  database_version = "POSTGRES_15"\n'
        "  region           = var.region\n\n"
        "  settings {\n"
        f"    tier = var.{tier_var}\n\n"
        "    ip_configuration {\n"
        "      ipv4_enabled    = false\n"
        "      private_network = var.vpc_network_self_link\n"
        "      require_ssl     = true\n"
        "    }\n\n"
        "    backup_configuration {\n"
        "      enabled                        = true\n"
        "      point_in_time_recovery_enabled = true\n"
        "    }\n"
        "  }\n\n"
        "  deletion_protection = true\n"
        "}\n\n"
        f'resource "google_sql_database" "{resource_name}_db" {{\n'
        f"  name     = var.{db_var}\n"
        f"  instance = google_sql_database_instance.{resource_name}.name\n"
        "}"
    )


def _compute_instance_block(name: str, instance_var: str, machine_var: str) -> str:
    resource_name = name.replace("-", "_")
    return (
        f'resource "google_compute_instance" "{resource_name}" {{\n'
        f"  name         = var.{instance_var}\n"
        f"  machine_type = var.{machine_var}\n"
        "  zone         = var.zone\n\n"
        "  boot_disk {\n"
        "    initialize_params {\n"
        '      image = "debian-cloud/debian-12"\n'
        "    }\n"
        "  }\n\n"
        "  network_interface {\n"
        "    network    = var.network\n"
        "    subnetwork = var.subnetwork\n"
        "  }\n\n"
        "  shielded_instance_config {\n"
        "    enable_secure_boot          = true\n"
        "    enable_vtpm                 = true\n"
        "    enable_integrity_monitoring = true\n"
        "  }\n\n"
        "  metadata = {\n"
        '    enable-oslogin = "TRUE"\n'
        "  }\n\n"
        "  service_account {\n"
        "    email  = var.service_account_email\n"
        '    scopes = ["cloud-platform"]\n'
        "  }\n"
        "}"
    )


def _healing_comment_block(ctx: HealingContext) -> str:
    lines = ["# --- Healing notes (informational, not executable) ---"]
    for err in ctx.terraform_errors[:5]:
        lines.append(f"# terraform error addressed: {err.splitlines()[0][:120]}")
    for violation in ctx.policy_violations[:5]:
        lines.append(f"# policy violation addressed: {violation[:120]}")
    return "\n".join(f"# {line}" if not line.startswith("#") else line for line in lines)


def _unsupported_comment_block(unsupported: list[ResourceSpec]) -> str:
    lines = ["# The following resource types were requested by the Architect but are"]
    lines.append("# not yet supported by the Cloud Pilot Terraform generator, and were skipped:")
    for spec in unsupported:
        lines.append(f"#   - {spec.resource_type} ({spec.purpose})")
    return "\n".join(lines)
