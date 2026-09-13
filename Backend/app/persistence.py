"""
Run persistence.

Uses SQLAlchemy. If DATABASE_URL is configured (typically PostgreSQL), that
is used. Otherwise, falls back to a local SQLite file under
Backend/workspace/cloud_pilot.db so the system remains fully runnable with
zero external services in local development.

Only run metadata/results are stored - never secrets or credentials.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from sqlalchemy import Column, DateTime, Integer, String, Text, create_engine
from sqlalchemy.orm import Session, declarative_base, sessionmaker

from app.config import WORKSPACE_ROOT, get_settings
from app.models.schemas import RunResult, RunStatus

logger = logging.getLogger("cloud_pilot")

Base = declarative_base()


class RunRecord(Base):
    __tablename__ = "runs"

    run_id = Column(String, primary_key=True)
    request = Column(Text, nullable=False)
    constraints = Column(Text, default="")
    status = Column(String, nullable=False, default=RunStatus.QUEUED.value)
    current_node = Column(String, default="")
    heal_attempts = Column(Integer, default=0)
    result_json = Column(Text, default="{}")
    errors_json = Column(Text, default="[]")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def _build_engine():
    settings = get_settings()
    if settings.database_url:
        try:
            engine = create_engine(settings.database_url, pool_pre_ping=True)
            engine.connect().close()
            logger.info("Using configured DATABASE_URL for run persistence")
            return engine
        except Exception as exc:  # noqa: BLE001
            logger.warning("DATABASE_URL unreachable (%s); falling back to local SQLite", exc)

    WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
    sqlite_path = WORKSPACE_ROOT / "cloud_pilot.db"
    logger.info("Using local SQLite fallback for run persistence at %s", sqlite_path)
    return create_engine(f"sqlite:///{sqlite_path}", connect_args={"check_same_thread": False})


_engine = None
_SessionLocal: sessionmaker | None = None


def init_db() -> None:
    global _engine, _SessionLocal
    if _engine is not None:
        return
    _engine = _build_engine()
    Base.metadata.create_all(_engine)
    _SessionLocal = sessionmaker(bind=_engine)


def get_session() -> Session:
    if _SessionLocal is None:
        init_db()
    assert _SessionLocal is not None
    return _SessionLocal()


class RunRepository:
    def create_run(self, run_id: str, request: str, constraints: str) -> None:
        with get_session() as session:
            record = RunRecord(
                run_id=run_id,
                request=request,
                constraints=constraints,
                status=RunStatus.QUEUED.value,
                result_json=json.dumps({}),
                errors_json=json.dumps([]),
            )
            session.add(record)
            session.commit()

    def update_run(
        self,
        run_id: str,
        status: RunStatus | None = None,
        current_node: str | None = None,
        heal_attempts: int | None = None,
        result: RunResult | None = None,
        extra_errors: list[str] | None = None,
    ) -> None:
        with get_session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return
            if status is not None:
                record.status = status.value
            if current_node is not None:
                record.current_node = current_node
            if heal_attempts is not None:
                record.heal_attempts = heal_attempts
            if result is not None:
                record.result_json = result.model_dump_json()
            if extra_errors:
                existing = json.loads(record.errors_json or "[]")
                existing.extend(extra_errors)
                record.errors_json = json.dumps(existing)
            record.updated_at = datetime.now(timezone.utc)
            session.commit()

    def get_run(self, run_id: str) -> RunResult | None:
        with get_session() as session:
            record = session.get(RunRecord, run_id)
            if record is None:
                return None
            return self._to_run_result(record)

    def list_runs(self, limit: int = 100) -> list[RunResult]:
        with get_session() as session:
            records = (
                session.query(RunRecord).order_by(RunRecord.created_at.desc()).limit(limit).all()
            )
            return [self._to_run_result(r) for r in records]

    @staticmethod
    def _to_run_result(record: RunRecord) -> RunResult:
        stored_result = json.loads(record.result_json or "{}")
        base = {
            "run_id": record.run_id,
            "status": record.status,
            "heal_attempts": record.heal_attempts,
            "request": record.request,
            "constraints": record.constraints or "",
            "current_node": record.current_node or "",
            "created_at": record.created_at.isoformat() if record.created_at else "",
            "updated_at": record.updated_at.isoformat() if record.updated_at else "",
            "errors": json.loads(record.errors_json or "[]"),
        }
        base.update({k: v for k, v in stored_result.items() if k in RunResult.model_fields})
        return RunResult.model_validate(base)


run_repository = RunRepository()
