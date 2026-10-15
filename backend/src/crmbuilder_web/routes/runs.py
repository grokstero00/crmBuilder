"""Apply the spec to the portal, follow progress, see the history and roll back."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from crmbuilder.spec import PortalSpec
from crmbuilder_web.deps import (
    BitrixFactory,
    current_user,
    get_bitrix_factory,
    get_cipher,
    get_db,
    get_project,
)
from crmbuilder_web.models import Project, Run, User
from crmbuilder_web.routes.projects import (
    PlanOut,
    _read_portal,
    _require_spec,
    _state,
    build_plan_out,
)
from crmbuilder_web.runs import active_run, rollback_target
from crmbuilder_web.security import Cipher

router = APIRouter(prefix="/projects/{project_id}", tags=["runs"])

JOURNAL_TAIL = 200


class StartRun(BaseModel):
    # The portal name typed by the person, and the plan they reviewed.
    confirm: str = Field(max_length=255)
    fingerprint: str = Field(min_length=1, max_length=64)
    allow_destructive: bool = False


class RunSummary(BaseModel):
    id: int
    kind: str
    status: str
    rollback_of: int | None
    total: int
    done: int
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class RunDetail(RunSummary):
    plan: dict[str, Any] | None
    done_addresses: list[str]
    failed: str | None
    residual: dict[str, Any] | None
    warnings: list[str]
    journal: list[dict[str, Any]]
    journal_total: int
    can_rollback: bool


def _summary(run: Run) -> dict[str, Any]:
    return {
        "id": run.id,
        "kind": run.kind,
        "status": run.status,
        "rollback_of": run.rollback_of,
        "total": len((run.plan or {}).get("changes", [])),
        "done": len(run.done or []),
        "error": run.error,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }


def _detail(run: Run) -> RunDetail:
    journal = run.journal or []
    return RunDetail(
        **_summary(run),
        plan=run.plan,
        done_addresses=run.done or [],
        failed=run.failed,
        residual=run.residual,
        warnings=run.warnings or [],
        journal=journal[-JOURNAL_TAIL:],
        journal_total=len(journal),
        can_rollback=_can_roll_back(run),
    )


def _can_roll_back(run: Run) -> bool:
    # Something may have changed on the portal and its earlier look is known.
    return (
        run.before_spec is not None and bool(run.done) and run.status not in ("pending", "running")
    )


def _get_run(project: Project, run_id: int, db: Session) -> Run:
    run = db.scalar(select(Run).where(Run.id == run_id, Run.project_id == project.id))
    if run is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Запуск не найден")
    return run


def _start(
    request: Request,
    db: Session,
    project: Project,
    user: User,
    body: StartRun,
    desired: PortalSpec,
    kind: str,
    rollback_of: int | None = None,
) -> RunDetail:
    if not project.allow_write:
        raise HTTPException(
            status.HTTP_403_FORBIDDEN,
            "Изменения для этого проекта запрещены. Разрешите их в настройках проекта.",
        )
    if body.confirm.strip() != project.portal:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Для подтверждения введите имя портала: {project.portal}",
        )
    if active_run(db, project.id) is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Для проекта уже идёт применение")
    run = Run(
        project_id=project.id,
        user_id=user.id,
        kind=kind,
        rollback_of=rollback_of,
        status="pending",
        allow_destructive=body.allow_destructive,
        fingerprint=body.fingerprint,
        desired_spec=desired.model_dump(mode="json"),
        done=[],
        journal=[],
        warnings=[],
    )
    db.add(run)
    db.commit()
    runner = request.app.state.runner
    request.app.state.run_executor(lambda run_id=run.id: runner.execute(run_id))
    db.refresh(run)
    return _detail(run)


@router.post("/apply", status_code=status.HTTP_202_ACCEPTED)
def apply_spec(
    body: StartRun,
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RunDetail:
    """Start bringing the portal to the spec. The run re-checks the plan before writing."""
    desired = _require_spec(project)
    return _start(request, db, project, user, body, desired, "apply")


@router.get("/runs")
def list_runs(project: Project = Depends(get_project)) -> list[RunSummary]:
    return [RunSummary(**_summary(run)) for run in project.runs]


@router.get("/runs/{run_id}")
def get_run(
    run_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)
) -> RunDetail:
    return _detail(_get_run(project, run_id, db))


@router.post("/runs/{run_id}/rollback-plan")
def rollback_plan(
    run_id: int,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    cipher: Cipher = Depends(get_cipher),
    factory: BitrixFactory = Depends(get_bitrix_factory),
) -> PlanOut:
    """What would change to return the portal to how it was before this run. Reads only."""
    run = _get_run(project, run_id, db)
    if not _can_roll_back(run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот запуск нельзя откатить")
    current = _read_portal(project, cipher, factory, _state(project))
    return build_plan_out(current, rollback_target(project, run))


@router.post("/runs/{run_id}/rollback", status_code=status.HTTP_202_ACCEPTED)
def rollback(
    run_id: int,
    body: StartRun,
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> RunDetail:
    run = _get_run(project, run_id, db)
    if not _can_roll_back(run):
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот запуск нельзя откатить")
    target = rollback_target(project, run)
    return _start(request, db, project, user, body, target, "rollback", rollback_of=run.id)
