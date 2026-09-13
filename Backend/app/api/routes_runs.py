from __future__ import annotations

import logging
import uuid

from fastapi import APIRouter, BackgroundTasks, HTTPException

from app.api.schemas import RunCreateRequest, RunCreateResponse, RunResult, RunStatus
from app.graph.graph import run_pipeline_sync
from app.persistence import run_repository
from app.rag.ingest import ingest_application_source
from app.utils.logging import run_log_store

logger = logging.getLogger("cloud_pilot")

router = APIRouter()


@router.post("/runs", response_model=RunCreateResponse, status_code=202)
def create_run(payload: RunCreateRequest, background_tasks: BackgroundTasks) -> RunCreateResponse:
    run_id = uuid.uuid4().hex[:12]
    run_repository.create_run(run_id, payload.request, payload.constraints or "")

    background_tasks.add_task(
        _execute_run,
        run_id=run_id,
        user_request=payload.request,
        constraints=payload.constraints or "",
        application_source=payload.application_source,
    )

    return RunCreateResponse(run_id=run_id, status=RunStatus.QUEUED)


@router.get("/runs", response_model=list[RunResult])
def list_runs() -> list[RunResult]:
    return run_repository.list_runs()


@router.get("/runs/{run_id}", response_model=RunResult)
def get_run(run_id: str) -> RunResult:
    result = run_repository.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return result


@router.get("/runs/{run_id}/logs")
def get_run_logs(run_id: str) -> list[dict]:
    return run_log_store.get(run_id)


@router.get("/runs/{run_id}/status")
def get_run_status(run_id: str) -> dict:
    result = run_repository.get_run(run_id)
    if result is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id} not found")
    return {
        "run_id": run_id,
        "status": result.status,
        "current_node": result.current_node,
        "heal_attempts": result.heal_attempts,
    }


def _execute_run(run_id: str, user_request: str, constraints: str, application_source: str | None) -> None:
    """Runs the full LangGraph pipeline. Executed in a background thread by
    FastAPI's BackgroundTasks so the POST /runs request never blocks."""
    run_repository.update_run(run_id, status=RunStatus.RUNNING, current_node="architect")

    if application_source:
        try:
            ingest_application_source(application_source, run_id)
        except (FileNotFoundError, NotADirectoryError) as exc:
            run_repository.update_run(run_id, status=RunStatus.FAILED, extra_errors=[str(exc)])
            return
        except Exception as exc:  # noqa: BLE001 - RAG ingestion must never crash a run
            logger.warning("Application source ingestion failed for run %s: %s", run_id, exc)
            run_repository.update_run(run_id, extra_errors=[f"Application source ingestion failed: {exc}"])

    try:
        final_state = run_pipeline_sync(user_request, constraints, run_id)
    except Exception as exc:  # noqa: BLE001 - guarantee the run always resolves to a terminal status
        logger.exception("Pipeline crashed for run %s", run_id)
        run_repository.update_run(run_id, status=RunStatus.FAILED, extra_errors=[f"Pipeline crashed: {exc}"])
        return

    status = RunStatus.SUCCESS if final_state.final_status == "success" else RunStatus.FAILED
    result = RunResult(
        run_id=run_id,
        status=status,
        heal_attempts=final_state.heal_attempts,
        resources=final_state.resource_specs,
        terraform_files=final_state.terraform_files.as_dict(),
        terraform_generation_method=final_state.terraform_files.generated_by,
        terraform_generation_notes=final_state.terraform_files.generation_notes,
        policy_results=final_state.policy_result.model_dump() if final_state.policy_result else {},
        terraform_plan=final_state.plan_outputs.model_dump() if final_state.plan_outputs else {},
        heal_history=final_state.heal_history,
        workspace=final_state.workspace_path,
        errors=final_state.errors,
        request=user_request,
        constraints=constraints,
        current_node="healing_decision",
    )
    run_repository.update_run(
        run_id,
        status=status,
        current_node="done",
        heal_attempts=final_state.heal_attempts,
        result=result,
    )
