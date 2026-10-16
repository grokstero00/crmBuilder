"""AI designer, audit and client report: start a job, follow it, answer, save, download."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from crmbuilder import design
from crmbuilder.spec import PortalSpec
from crmbuilder_web.deps import current_user, get_db, get_project
from crmbuilder_web.jobs import (
    ACTIVE,
    AI_UNAVAILABLE,
    MODEL_LABELS,
    active_design,
    ai_available,
    default_model,
    spec_fingerprint,
)
from crmbuilder_web.models import Job, Project, Run, User
from crmbuilder_web.routes.projects import ProjectOut, _out, _require_spec

router = APIRouter(prefix="/projects/{project_id}", tags=["jobs"])
# Not tied to a project: what the AI features can use on this server.
ai_router = APIRouter(tags=["jobs"])

JOBS_LISTED = 50

Kind = Literal["design", "audit", "report"]
Answer = Annotated[str, Field(max_length=5000)]


# --- schemas -------------------------------------------------------------------------


class ModelOption(BaseModel):
    id: str
    label: str


class AiConfig(BaseModel):
    available: bool
    default_model: str
    models: list[ModelOption]


class DesignIn(BaseModel):
    brief: str = Field(min_length=1, max_length=20000)
    model: str | None = None
    # Off: the model never stops to ask and decides on assumptions.
    questions: bool = True


class AuditIn(BaseModel):
    ai: bool = False
    context: str = Field(default="", max_length=5000)
    model: str | None = None


class ReportIn(BaseModel):
    title: str = Field(default="", max_length=200)
    # Count "what was done" from the portal before this run; default: before the first one.
    since_run_id: int | None = None
    ai: bool = False
    brief: str = Field(default="", max_length=20000)
    model: str | None = None


class AnswersIn(BaseModel):
    answers: list[Answer] = Field(max_length=100)


class JobSummary(BaseModel):
    id: int
    kind: str
    status: str
    created_at: datetime
    finished_at: datetime | None
    error: str | None
    cost_usd: float | None
    title: str


class JobDetail(JobSummary):
    params: dict[str, Any]
    questions: list[str] | None
    log: list[dict[str, Any]]
    result: dict[str, Any] | None
    usage: dict[str, Any] | None
    saved: bool


class SavedOut(BaseModel):
    project: ProjectOut


# --- helpers -------------------------------------------------------------------------


def _title(job: Job) -> str:
    params = job.params or {}
    if job.kind == "design":
        brief = " ".join(str(params.get("brief", "")).split())
        return brief[:80] + ("…" if len(brief) > 80 else "")
    if job.kind == "audit":
        return "Аудит с ИИ" if params.get("ai") else "Аудит"
    return str(params.get("title") or "").strip() or "Отчёт"


def _summary(job: Job) -> dict[str, Any]:
    return {
        "id": job.id,
        "kind": job.kind,
        "status": job.status,
        "created_at": job.created_at,
        "finished_at": job.finished_at,
        "error": job.error,
        "cost_usd": (job.usage or {}).get("cost_usd"),
        "title": _title(job),
    }


def _detail(job: Job) -> JobDetail:
    return JobDetail(
        **_summary(job),
        params=job.params or {},
        # Hidden once answered: the job picks the answers up within a poll interval.
        questions=job.questions if job.status == "waiting" and job.answers is None else None,
        log=job.log or [],
        result=job.result,
        usage=job.usage,
        saved=job.saved_at is not None,
    )


def _get_job(project: Project, job_id: int, db: Session) -> Job:
    job = db.scalar(select(Job).where(Job.id == job_id, Job.project_id == project.id))
    if job is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Задача не найдена")
    return job


def _model(requested: str | None) -> str:
    model = (requested or "").strip() or default_model()
    if model not in design.PRICES:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Неизвестная модель: {model}")
    return model


def _require_ai() -> None:
    if not ai_available():
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, AI_UNAVAILABLE)


def _start(
    request: Request,
    db: Session,
    project: Project,
    user: User,
    kind: str,
    params: dict[str, Any],
    base_fingerprint: str | None = None,
) -> JobDetail:
    job = Job(
        project_id=project.id,
        user_id=user.id,
        kind=kind,
        status="running",
        params=params,
        base_fingerprint=base_fingerprint,
        cancel_requested=False,
        log=[],
    )
    db.add(job)
    db.commit()
    runner = request.app.state.job_runner
    request.app.state.run_executor(lambda job_id=job.id: runner.execute(job_id))
    db.refresh(job)
    return _detail(job)


def _filename(kind: str, portal: str, moment: datetime) -> str:
    # Header values must be ASCII; portal names normally are, this only guards the rest.
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", portal)
    return f"{kind}-{safe}-{moment:%Y-%m-%d}.md"


# --- routes --------------------------------------------------------------------------


@ai_router.get("/ai")
def ai_config(user: User = Depends(current_user)) -> AiConfig:
    return AiConfig(
        available=ai_available(),
        default_model=default_model(),
        models=[ModelOption(id=m, label=label) for m, label in MODEL_LABELS.items()],
    )


@router.post("/design", status_code=status.HTTP_202_ACCEPTED)
def start_design(
    body: DesignIn,
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobDetail:
    """Let Claude edit a copy of the spec from a brief. Nothing changes until it is saved."""
    _require_spec(project)
    if not body.brief.strip():
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Опишите задачу (ТЗ)")
    model = _model(body.model)
    _require_ai()
    if active_design(db, project.id) is not None:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Проектировщик уже работает над этим проектом"
        )
    params = {"brief": body.brief.strip(), "model": model, "questions": body.questions}
    return _start(request, db, project, user, "design", params, spec_fingerprint(project.spec))


@router.post("/audit", status_code=status.HTTP_202_ACCEPTED)
def start_audit(
    body: AuditIn,
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobDetail:
    """Check the current spec by the rules and, optionally, with Claude."""
    _require_spec(project)
    model = _model(body.model)
    if body.ai:
        _require_ai()
    params = {"ai": body.ai, "context": body.context.strip(), "model": model}
    return _start(request, db, project, user, "audit", params)


@router.post("/report", status_code=status.HTTP_202_ACCEPTED)
def start_report(
    body: ReportIn,
    request: Request,
    project: Project = Depends(get_project),
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
) -> JobDetail:
    """The hand-over document: what was done since a run, the structure, manual steps."""
    _require_spec(project)
    model = _model(body.model)
    if body.ai and not body.brief.strip():
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, "Для ручных шагов нужен текст ТЗ"
        )
    if body.ai:
        _require_ai()
    if body.since_run_id is not None:
        run = db.scalar(
            select(Run).where(Run.id == body.since_run_id, Run.project_id == project.id)
        )
        if run is None or run.before_spec is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, "Запуск, от которого считать изменения, не найден"
            )
    params = {
        "title": body.title.strip(),
        "since_run_id": body.since_run_id,
        "ai": body.ai,
        "brief": body.brief.strip(),
        "model": model,
    }
    return _start(request, db, project, user, "report", params)


@router.get("/jobs")
def list_jobs(
    kind: Kind | None = None,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
) -> list[JobSummary]:
    query = select(Job).where(Job.project_id == project.id)
    if kind is not None:
        query = query.where(Job.kind == kind)
    jobs = db.scalars(query.order_by(Job.id.desc()).limit(JOBS_LISTED))
    return [JobSummary(**_summary(job)) for job in jobs]


@router.get("/jobs/{job_id}")
def get_job(
    job_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)
) -> JobDetail:
    return _detail(_get_job(project, job_id, db))


@router.post("/jobs/{job_id}/answers")
def answer_job(
    job_id: int,
    body: AnswersIn,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
) -> JobDetail:
    """Answers to the designer's questions; empty ones let the model decide itself."""
    job = _get_job(project, job_id, db)
    # Answers already posted and not yet picked up count as "not waiting" too.
    if job.status != "waiting" or not job.questions or job.answers is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Модель сейчас не ждёт ответов")
    if len(body.answers) != len(job.questions):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"Нужно ответов: {len(job.questions)}, получено: {len(body.answers)}",
        )
    # Only this column: the job's own thread owns the status and the log.
    job.answers = [a.strip() for a in body.answers]
    db.commit()
    return _detail(job)


@router.post("/jobs/{job_id}/cancel")
def cancel_job(
    job_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)
) -> JobDetail:
    """Ask the job to stop; it does at its next step (an edit, a question or a model call)."""
    job = _get_job(project, job_id, db)
    if job.status not in ACTIVE:
        raise HTTPException(status.HTTP_409_CONFLICT, "Задача уже завершена")
    job.cancel_requested = True
    db.commit()
    return _detail(job)


@router.post("/jobs/{job_id}/save")
def save_design(
    job_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)
) -> SavedOut:
    """Put the designer's result into the project spec. The portal is not touched."""
    job = _get_job(project, job_id, db)
    if job.kind != "design" or job.status != "done" or not job.result:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Сохранить можно только готовый результат проектировщика"
        )
    if job.saved_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Этот результат уже сохранён")
    if spec_fingerprint(project.spec) != job.base_fingerprint:
        raise HTTPException(
            status.HTTP_409_CONFLICT,
            "Спецификация изменилась после запуска проектировщика: запустите его заново",
        )
    try:
        spec = PortalSpec.model_validate({**job.result["spec"], "portal": project.portal})
    except (KeyError, TypeError, ValidationError) as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, f"Результат проектировщика не прошёл проверку: {exc}"
        ) from exc
    project.spec = spec.model_dump(mode="json")
    job.saved_at = datetime.now(UTC)
    db.commit()
    return SavedOut(project=_out(project))


@router.get("/jobs/{job_id}/download")
def download_job(
    job_id: int, project: Project = Depends(get_project), db: Session = Depends(get_db)
) -> Response:
    """The audit or report as a Markdown file for the client."""
    job = _get_job(project, job_id, db)
    if job.kind not in ("audit", "report") or job.status != "done" or not job.result:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Скачать можно только готовый аудит или отчёт"
        )
    name = _filename(job.kind, project.portal, job.finished_at or job.created_at)
    return Response(
        content=str(job.result.get("markdown", "")),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )
