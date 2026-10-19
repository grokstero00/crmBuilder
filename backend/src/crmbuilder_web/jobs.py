"""AI design, audit and report jobs: the engine's commands, with progress in the database.

Like a run, a job is created by a request and carried out by ``app.state.run_executor``
(a thread by default). Every state change commits, so the browser can poll the job.
When the designer asks questions, the job waits in the row until another request posts
the answers, the person cancels, or the wait times out and the model decides itself.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
import traceback
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import anthropic
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from crmbuilder import audit, design, report
from crmbuilder.plan import diff_specs
from crmbuilder.spec import PortalSpec
from crmbuilder_web.models import Job, Run

ACTIVE = ("running", "waiting")
# Log entries kept per job: a design has a few dozen, this only bounds a runaway loop.
LOG_LIMIT = 1000

MODEL_LABELS = {
    "claude-haiku-5-5": "Haiku — быстро и дёшево",
    "claude-sonnet-5-5": "Sonnet — баланс",
    "claude-opus-5-5": "Opus — глубже и дороже",
}
FALLBACK_MODEL = "claude-haiku-5-5"
MODEL_ENV = "CRMBUILDER_WEB_MODEL"

AI_UNAVAILABLE = "ИИ недоступен: на сервере не задан ANTHROPIC_API_KEY"
NO_SPEC = "Сначала импортируйте портал"

# factory() -> anthropic.Anthropic or a test double with the same beta.messages.create.
AnthropicFactory = Callable[[], Any]


def ai_available() -> bool:
    return bool(os.environ.get("ANTHROPIC_API_KEY", "").strip())


def default_model() -> str:
    model = os.environ.get(MODEL_ENV, "").strip()
    return model if model in MODEL_LABELS else FALLBACK_MODEL


def spec_fingerprint(spec: dict[str, Any] | None) -> str:
    """Identifies a project spec; JSONB does not keep key order, so keys are sorted."""
    text = json.dumps(spec, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:16]


def active_design(db: Session, project_id: int) -> Job | None:
    return db.scalar(
        select(Job).where(
            Job.project_id == project_id, Job.kind == "design", Job.status.in_(ACTIVE)
        )
    )


def mark_jobs_interrupted(sessions: sessionmaker) -> None:
    """Jobs left active by a stopped server can never finish: say so instead of hanging."""
    with sessions() as db:
        db.execute(
            update(Job)
            .where(Job.status.in_(ACTIVE))
            .values(
                status="interrupted",
                questions=None,
                error="Сервер был перезапущен во время работы",
                finished_at=datetime.now(UTC),
            )  # fmt: skip
        )
        db.commit()


def anthropic_error(exc: BaseException) -> str | None:
    """A message for the person when the Anthropic API fails; None for other errors."""
    if isinstance(exc, anthropic.AuthenticationError):
        return (
            "Ключ Anthropic API не задан или неверен: укажите ANTHROPIC_API_KEY в .env "
            "и перезапустите сервис"
        )
    if isinstance(exc, anthropic.PermissionDeniedError):
        return f"Anthropic API отказал в доступе: {exc.message}"
    if isinstance(exc, anthropic.RateLimitError):
        return "Превышен лимит запросов Anthropic, попробуйте через минуту"
    if isinstance(exc, anthropic.APIStatusError):
        return f"Ошибка Anthropic API {exc.status_code}: {exc.message}"
    if isinstance(exc, anthropic.APIConnectionError):
        hint = ""
        if "CERTIFICATE" in repr(exc.__cause__) or "CERTIFICATE" in str(exc):
            hint = " (проблема с сертификатом: см. BITRIX_CA_BUNDLE в README, он действует и здесь)"
        return f"Нет связи с Anthropic API{hint}: {exc}"
    return None


def usage_dict(usage: design.Usage, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "requests": usage.requests,
        "input_tokens": usage.input_tokens,
        "output_tokens": usage.output_tokens,
        "cache_read_tokens": usage.cache_read_tokens,
        "cache_write_tokens": usage.cache_write_tokens,
        "cost_usd": usage.cost(model),
    }


class JobCancelled(Exception):
    pass


class JobFailed(Exception):
    pass


@contextmanager
def _model_errors() -> Iterator[None]:
    # DesignError, and refusals or broken answers in the AI parts of audit and report.
    try:
        yield
    except RuntimeError as exc:
        raise JobFailed(f"Модель не справилась: {exc}") from exc


class _CancellableClient:
    """Checks for a cancel request before every model call, then passes it on."""

    def __init__(self, client: Any, check: Callable[[], None]) -> None:
        self._client = client
        self._check = check
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs: Any) -> Any:
        self._check()
        return self._client.beta.messages.create(**kwargs)


class JobRunner:
    def __init__(
        self,
        sessions: sessionmaker,
        anthropic_factory: AnthropicFactory,
        poll_interval: float = 1.0,
        answer_timeout: float = 1800,
    ) -> None:
        self.sessions = sessions
        self.factory = anthropic_factory
        self.poll_interval = poll_interval
        self.answer_timeout = answer_timeout

    def execute(self, job_id: int) -> None:
        with self.sessions() as db:
            job = db.get(Job, job_id)
            if job is None:  # the project was deleted before the job started
                return
            steps = {"design": self._design, "audit": self._audit, "report": self._report}
            try:
                job.result = steps[job.kind](db, job)
                # Inside the try: a failed final commit (e.g. text PostgreSQL rejects) must
                # still end the job as failed, not leave it "running" forever.
                self._finish(db, job, "done")
            except JobCancelled:
                db.rollback()
                self._finish(db, job, "cancelled", "Остановлено по вашему запросу")
            except JobFailed as exc:
                db.rollback()
                self._finish(db, job, "failed", str(exc))
            except Exception as exc:  # noqa: BLE001 - a job must always end in a final state
                message = anthropic_error(exc)
                if message is None:
                    traceback.print_exc()
                    message = f"Внутренняя ошибка: {exc}"
                db.rollback()
                self._finish(db, job, "failed", message)

    # --- kinds -----------------------------------------------------------------------

    def _design(self, db: Session, job: Job) -> dict[str, Any]:
        project = job.project
        spec = _spec(project.spec)
        params = job.params
        model = params["model"]
        # The result is compared with exactly this spec, and saved only while it is unchanged.
        job.base_fingerprint = spec_fingerprint(project.spec)
        label = MODEL_LABELS.get(model, model).split(" — ")[0]
        self._log(
            job,
            "info",
            f"Модель {label}. В Claude уходит только структура портала, без сделок и контактов.",
        )
        db.commit()

        def ask(questions: list[str]) -> list[str]:
            return self._ask(db, job, questions)

        with self._client(db, job) as client, _model_errors():
            result = design.design(
                client,
                spec,
                params["brief"],
                ask=ask if params.get("questions") else None,
                report=lambda line: self._on_edit(db, job, line),
                model=model,
                effort=design.DEFAULT_EFFORT,
            )
        proposed = result.spec.model_copy(update={"portal": project.portal})
        job.usage = usage_dict(result.usage, model)
        return {
            "spec": proposed.model_dump(mode="json"),
            "summary": result.summary,
            "edits": result.edits,
            "plan": diff_specs(spec, proposed).to_dict(),
            # The same plain-language sentences as the report's «Что сделано».
            "described": [
                {"section": line.section, "text": line.text}
                for line in report.describe_changes(spec, proposed)
            ],
        }

    def _audit(self, db: Session, job: Job) -> dict[str, Any]:
        spec = _spec(job.project.spec)
        params = job.params
        findings = audit.audit(spec)
        business = ""
        if params.get("ai"):
            model = params["model"]
            with self._client(db, job) as client, _model_errors():
                review = audit.ai_review(
                    client,
                    spec,
                    findings,
                    context=params.get("context", ""),
                    model=model,
                    effort=design.DEFAULT_EFFORT,
                )
            findings = findings + review.findings
            business = review.business_guess
            job.usage = usage_dict(review.usage, model)
        return {
            "findings": [f.to_dict() for f in findings],
            "business_guess": business,
            "markdown": audit.render_markdown(spec.portal, findings, business),
            "counts": {s.value: sum(f.severity is s for f in findings) for s in audit.Severity},
        }

    def _report(self, db: Session, job: Job) -> dict[str, Any]:
        spec = _spec(job.project.spec)
        params = job.params
        base = self._base_run(db, job)
        changes = None
        if base is not None:
            changes = report.describe_changes(PortalSpec.model_validate(base.before_spec), spec)
        ai_part = None
        if params.get("ai"):
            model = params["model"]
            with self._client(db, job) as client, _model_errors():
                ai_part = report.ai_manual_steps(
                    client,
                    spec,
                    changes or [],
                    params["brief"],
                    model=model,
                    effort=design.DEFAULT_EFFORT,
                )
            job.usage = usage_dict(ai_part.usage, model)
        markdown = report.render(spec, changes=changes, title=params.get("title") or "", ai=ai_part)
        return {
            "markdown": markdown,
            "changes": len(changes or []),
            "manual_steps": len(ai_part.manual_steps) if ai_part else 0,
            # The run whose "before" the changes are counted from.
            "since_run_id": base.id if base is not None else None,
        }

    def _base_run(self, db: Session, job: Job) -> Run | None:
        """The given run, or the oldest one with a "before": the portal before the project."""
        query = select(Run).where(Run.project_id == job.project_id, Run.before_spec.is_not(None))
        since = job.params.get("since_run_id")
        if since is None:
            return db.scalar(query.order_by(Run.id).limit(1))
        run = db.scalar(query.where(Run.id == since))
        if run is None:
            raise JobFailed("Запуск, от которого считать изменения, не найден")
        return run

    # --- the designer's callbacks ------------------------------------------------------

    def _on_edit(self, db: Session, job: Job, line: str) -> None:
        # The engine reports "  create <address>" for edits and "  ! <reason>" for rejected ones.
        text = line.strip()
        if text.startswith("!"):
            self._log(job, "error", text.removeprefix("!").strip())
        else:
            self._log(job, "edit", text)
        db.commit()
        self._check_cancel(db, job)

    def _ask(self, db: Session, job: Job, questions: list[str]) -> list[str]:
        job.questions = questions
        job.answers = None
        job.status = "waiting"
        self._log(job, "questions", "\n".join(questions), questions)
        db.commit()

        deadline = time.monotonic() + self.answer_timeout
        while True:
            # A fresh read: the answers and a cancel request come from other requests.
            answers, cancel = db.execute(
                select(Job.answers, Job.cancel_requested).where(Job.id == job.id)
            ).one()
            db.commit()  # end the read transaction instead of holding it while sleeping
            if cancel:
                raise JobCancelled
            if answers is not None:
                break
            if time.monotonic() >= deadline:
                answers = None
                break
            time.sleep(self.poll_interval)

        given = [str(a).strip() for a in (answers or [])][: len(questions)]
        given += [""] * (len(questions) - len(given))
        job.answers = given
        job.questions = None
        job.status = "running"
        if answers is None:
            self._log(job, "info", "Ответов не дождались: модель решит сама")
        else:
            text = "\n".join(a or "(пусть решает модель)" for a in given)
            self._log(job, "answers", text, given)
        db.commit()
        return given

    # --- helpers -----------------------------------------------------------------------

    @contextmanager
    def _client(self, db: Session, job: Job) -> Iterator[Any]:
        try:
            client = self.factory()
        except ValueError as exc:  # the extra CA bundle cannot be loaded
            raise JobFailed(f"Не удалось подготовить подключение к Anthropic API: {exc}") from exc
        try:
            yield _CancellableClient(client, lambda: self._check_cancel(db, job))
        finally:
            close = getattr(client, "close", None)
            if callable(close):
                close()

    def _check_cancel(self, db: Session, job: Job) -> None:
        requested = db.scalar(select(Job.cancel_requested).where(Job.id == job.id))
        db.commit()
        if requested:
            raise JobCancelled

    def _log(self, job: Job, kind: str, text: str, items: list[str] | None = None) -> None:
        if len(job.log) >= LOG_LIMIT:
            return
        entry: dict[str, Any] = {"type": kind, "text": text}
        if items is not None:
            entry["items"] = list(items)
        job.log = [*job.log, entry]

    def _finish(self, db: Session, job: Job, status: str, error: str | None = None) -> None:
        job.status = status
        job.error = error
        if status != "done":
            job.questions = None
        job.finished_at = datetime.now(UTC)
        db.commit()


def _spec(data: dict[str, Any] | None) -> PortalSpec:
    if not data:
        raise JobFailed(NO_SPEC)
    return PortalSpec.model_validate(data)
