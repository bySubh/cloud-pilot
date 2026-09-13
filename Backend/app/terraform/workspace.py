"""
Isolated Terraform workspace management.

Every run gets its own directory under Backend/workspace/tf-run-<ts>-<id>/.
Workspaces are never reused between runs, and all paths are validated to
stay inside the workspace root to prevent path traversal.
"""
from __future__ import annotations

import time
from pathlib import Path

from app.config import WORKSPACE_ROOT
from app.models.schemas import TerraformFiles

_SAFE_FILENAMES = {
    "main.tf",
    "variables.tf",
    "providers.tf",
    "terraform.tfvars.example",
}


class UnsafeWorkspacePathError(Exception):
    pass


def create_workspace(run_id: str) -> Path:
    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    timestamp = time.strftime("%Y%m%d%H%M%S")
    safe_run_id = _sanitize_run_id(run_id)
    workspace_dir = WORKSPACE_ROOT / f"tf-run-{timestamp}-{safe_run_id}"
    workspace_dir.mkdir(parents=True, exist_ok=False)
    return workspace_dir


def _sanitize_run_id(run_id: str) -> str:
    # Keep only characters safe for a directory name; strip path separators
    # and anything that could enable traversal.
    return "".join(c for c in run_id if c.isalnum() or c in ("-", "_")) or "run"


def _validate_within_workspace(workspace_dir: Path, filename: str) -> Path:
    if filename not in _SAFE_FILENAMES:
        raise UnsafeWorkspacePathError(f"Refusing to write unexpected filename: {filename}")
    target = (workspace_dir / filename).resolve()
    if workspace_dir.resolve() not in target.parents and target != workspace_dir.resolve():
        raise UnsafeWorkspacePathError(f"Path traversal detected for {filename}")
    return target


def write_terraform_files(workspace_dir: Path, files: TerraformFiles) -> None:
    for filename, content in files.as_dict().items():
        target = _validate_within_workspace(workspace_dir, filename)
        target.write_text(content, encoding="utf-8")


def read_terraform_files(workspace_dir: Path) -> TerraformFiles:
    def _read(name: str) -> str:
        path = workspace_dir / name
        return path.read_text(encoding="utf-8") if path.exists() else ""

    return TerraformFiles(
        main_tf=_read("main.tf"),
        variables_tf=_read("variables.tf"),
        providers_tf=_read("providers.tf"),
        terraform_tfvars_example=_read("terraform.tfvars.example"),
    )
