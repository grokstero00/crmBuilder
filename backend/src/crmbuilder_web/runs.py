"""Apply and rollback runs: the CLI's apply flow, with progress kept in the database.

A run is created by a request and carried out in the background (a thread by default;
``app.state.run_executor`` decides). Every step commits, so the browser can poll the
run for progress, and the project's state always matches what was really done.
"""

from __future__ import annotations

import hashlib
import traceback
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select, update
from sqlalchemy.orm import Session, sessionmaker

from crmbuilder import automation
from crmbuilder.apply import Applier
from crmbuilder.bitrix import BitrixError
from crmbuilder.importer import import_portal
from crmbuilder.plan import Change, Plan, diff_specs
from crmbuilder.spec import PortalSpec
from crmbuilder.state import State
from crmbuilder_web.models import Project, Run
from crmbuilder_web.security import Cipher

ACTIVE = ("pending", "running")
# Journal entries kept per run: enough to debug, bounded for very large plans.
JOURNAL_LIMIT = 2000

Executor = Callable[[Callable[[], None]], None]


def fingerprint(plan: Plan) -> str:
    """Identifies a plan, so a run only goes ahead if the portal still needs exactly it."""
    return hashlib.sha256(plan.to_json().encode()).hexdigest()[:16]


def active_run(db: Session, project_id: int) -> Run | None:
    return db.scalar(select(Run).where(Run.project_id == project_id, Run.status.in_(ACTIVE)))


def mark_interrupted(sessions: sessionmaker) -> None:
    """Runs left active by a stopped server can never finish: say so instead of hanging."""
    with sessions() as db:
        db.execute(
            update(Run)
            .where(Run.status.in_(ACTIVE))
            .values(
                status="interrupted",
                error="Сервер был перезапущен во время применения",
                finished_at=datetime.now(UTC),
            )  # fmt: skip
        )
        db.commit()


def rollback_target(project: Project, run: Run) -> PortalSpec:
    """The portal as it was before ``run``, keeping today's robot rules where they fit."""
    restored = PortalSpec.model_validate(run.before_spec)
    current = PortalSpec.model_validate(project.spec) if project.spec else None
    restored, _ = automation.carry_over(restored, current)
    return restored


class RunFailed(Exception):
    pass


class Runner:
    def __init__(self, sessions: sessionmaker, cipher: Cipher, bitrix_factory) -> None:
        self.sessions = sessions
        self.cipher = cipher
        self.factory = bitrix_factory

    def execute(self, run_id: int) -> None:
        with self.sessions() as db:
            run = db.get(Run, run_id)
            try:
                self._execute(db, run)
            except RunFailed as exc:
                db.rollback()  # drop anything half-written by the failed step
                self._finish(db, run, "failed", str(exc))
            except Exception as exc:  # noqa: BLE001 - a run must always end in a final state
                traceback.print_exc()
                db.rollback()
                self._finish(db, run, "failed", f"Внутренняя ошибка: {exc}")

    # --- steps -----------------------------------------------------------------------

    def _execute(self, db: Session, run: Run) -> None:
        project = run.project
        if not project.allow_write:
            raise RunFailed("Изменения для этого проекта запрещены в настройках")
        webhook = self.cipher.decrypt(project.webhook_encrypted)
        desired = PortalSpec.model_validate(run.desired_spec)
        state = State.model_validate(project.state) if project.state else None

        before = self._read(webhook, project.lang, state)
        plan = diff_specs(before.spec, desired)
        run.before_spec = before.spec.model_dump(mode="json")
        run.before_state = before.state.model_dump(mode="json")
        run.plan = plan.to_dict()
        run.warnings = list(before.warnings)
        project.state = before.state.model_dump(mode="json")
        db.commit()

        if fingerprint(plan) != run.fingerprint:
            raise RunFailed(
                "Портал или спецификация изменились после построения плана. "
                "Постройте план заново и проверьте его."
            )
        if not plan.changes:
            self._finish(db, run, "unchanged")
            return

        writer = self.factory(webhook, writable=True)
        try:
            applier = Applier(writer, before.spec, desired, before.state)
            problems = applier.unsupported(plan)
            if problems:
                lines = "; ".join(f"{p.change.address}: {p.reason}" for p in problems)
                raise RunFailed(f"Эти изменения нельзя применить: {lines}")
            if plan.destructive and not run.allow_destructive:
                raise RunFailed(
                    "В плане есть опасные изменения (удаление или пересоздание): "
                    "подтвердите их отдельной галочкой"
                )
            run.status = "running"
            db.commit()
            applier.journal = lambda entry: self._journal(db, run, entry)
            applier.progress = lambda change, status: self._progress(db, run, change, status)
            try:
                result = applier.apply(plan)
            finally:
                # Whatever happened, keep the state that matches the portal.
                project.state = applier.state.model_dump(mode="json")
                db.commit()
        finally:
            writer.close()

        after = self._read(webhook, project.lang, applier.state)
        project.state = after.state.model_dump(mode="json")
        residual = diff_specs(after.spec, desired)
        run.residual = residual.to_dict() if residual.changes else None
        run.warnings = list(run.warnings) + result.warnings
        if run.kind == "rollback" and result.ok:
            project.spec = desired.model_dump(mode="json")
        if not result.ok:
            self._finish(db, run, "failed", f"{result.failed.address}: {result.error}")
        elif residual.changes:
            self._finish(db, run, "diverged", "После применения портал всё ещё отличается от плана")
        else:
            self._finish(db, run, "verified")

    def _read(self, webhook: str, lang: str, state: State | None):
        client = self.factory(webhook, writable=False)
        try:
            return import_portal(client, lang=lang, previous_state=state)
        except BitrixError as exc:
            raise RunFailed(f"Ошибка API Битрикс24: {exc}") from exc
        except httpx.HTTPError as exc:
            raise RunFailed(f"Портал недоступен: {exc}") from exc
        finally:
            client.close()

    def _journal(self, db: Session, run: Run, entry: dict[str, Any]) -> None:
        if len(run.journal) >= JOURNAL_LIMIT:
            return
        entry = {"time": datetime.now(UTC).isoformat(timespec="seconds"), **entry}
        run.journal = [*run.journal, _jsonable(entry)]
        db.commit()

    def _progress(self, db: Session, run: Run, change: Change, status: str) -> None:
        if status == "done":
            run.done = [*run.done, change.address]
        else:
            run.failed = change.address
        db.commit()

    def _finish(self, db: Session, run: Run, status: str, error: str | None = None) -> None:
        run.status = status
        run.error = error
        run.finished_at = datetime.now(UTC)
        db.commit()


def _jsonable(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(k): _jsonable(v) for k, v in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(v) for v in value]
    if isinstance(value, str | int | float | bool) or value is None:
        return value
    return str(value)
