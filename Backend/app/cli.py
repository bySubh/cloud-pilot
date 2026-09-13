"""
CLI entry point for running a single Cloud Pilot pipeline execution without
the API/frontend - useful for local debugging.

Usage:
    python -m app.cli "Create infrastructure for a web app needing \
PostgreSQL and object storage" --constraints "use secure defaults"
"""
from __future__ import annotations

import argparse
import json
import uuid

from app.config import ensure_directories
from app.graph.graph import run_pipeline_sync
from app.rag.ingest import ingest_infra_templates


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the Cloud Pilot pipeline once, locally.")
    parser.add_argument("request", help="Plain-English infrastructure request")
    parser.add_argument("--constraints", default="", help="Optional constraints")
    args = parser.parse_args()

    ensure_directories()
    ingest_infra_templates()

    run_id = uuid.uuid4().hex[:12]
    final_state = run_pipeline_sync(args.request, args.constraints, run_id)

    print(f"\n=== Run {run_id}: {final_state.final_status.upper()} ===\n")
    print(f"Heal attempts: {final_state.heal_attempts}/{final_state.max_heal_attempts}")
    print(f"Resources: {[r.resource_type for r in final_state.resource_specs]}")
    print(f"Terraform success: {final_state.terraform_success}")
    print(f"Policy success: {final_state.policy_success}")
    if final_state.errors:
        print(f"Errors: {final_state.errors}")
    print("\n--- main.tf ---\n")
    print(final_state.terraform_files.main_tf)
    print("\n--- Workspace ---\n")
    print(final_state.workspace_path)
    print("\n--- Heal history ---\n")
    print(json.dumps([h.model_dump() for h in final_state.heal_history], indent=2))


if __name__ == "__main__":
    main()
