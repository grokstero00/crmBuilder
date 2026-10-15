"""Projects: a portal with its webhook, plus import, spec and plan on top of the engine."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import yaml
from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

from crmbuilder import automation
from crmbuilder.bitrix import BitrixClient, BitrixError, normalize_webhook_url
from crmbuilder.importer import ImportResult, import_portal
from crmbuilder.plan import diff_specs
from crmbuilder.spec import PortalSpec, spec_from_yaml, spec_to_yaml
from crmbuilder.state import State
from crmbuilder_web.deps import (
    BitrixFactory,
    current_user,
    get_bitrix_factory,
    get_cipher,
    get_db,
    get_project,
)
from crmbuilder_web.models import Project, User
from crmbuilder_web.security import Cipher

router = APIRouter(prefix="/projects", tags=["projects"])


# --- schemas -------------------------------------------------------------------------


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    webhook: str = Field(min_length=1, max_length=500)


class ProjectUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=200)
    webhook: str | None = Field(default=None, min_length=1, max_length=500)


class Counts(BaseModel):
    deal_categories: int = 0
    fields: int = 0
    smart_processes: int = 0
    departments: int = 0
    automation: int = 0
    automation_pending: int = 0


class ProjectOut(BaseModel):
    id: int
    name: str
    portal: str
    imported_at: datetime | None
    created_at: datetime
    counts: Counts | None


class ImportOut(BaseModel):
    project: ProjectOut
    warnings: list[str]


class SpecOut(BaseModel):
    spec: dict[str, Any]
    yaml: str


class SpecIn(BaseModel):
    yaml: str = Field(max_length=2_000_000)


class ManualRule(BaseModel):
    key: str
    title: str
    stage: str


class PlanOut(BaseModel):
    plan: dict[str, Any]
    manual: list[ManualRule]
    warnings: list[str]


# --- helpers -------------------------------------------------------------------------


def _spec(project: Project) -> PortalSpec | None:
    return PortalSpec.model_validate(project.spec) if project.spec else None


def _counts(spec: PortalSpec | None) -> Counts | None:
    if spec is None:
        return None
    return Counts(
        deal_categories=len(spec.crm.deal_categories),
        fields=sum(len(f) for f in spec.crm.fields.values()),
        smart_processes=len(spec.crm.smart_processes),
        departments=len(spec.structure.departments),
        automation=len(spec.automation),
        automation_pending=len(automation.pending(spec)),
    )


def _out(project: Project) -> ProjectOut:
    return ProjectOut(
        id=project.id,
        name=project.name,
        portal=project.portal,
        imported_at=project.imported_at,
        created_at=project.created_at,
        counts=_counts(_spec(project)),
    )


def _portal_of(webhook: str) -> tuple[str, str]:
    try:
        url = normalize_webhook_url(webhook)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Неверный адрес вебхука: нужен вид https://<портал>.bitrix24.ru/rest/<id>/<код>/",
        ) from exc
    return url, httpx.URL(url).host


def _read_portal(
    project: Project, cipher: Cipher, factory: BitrixFactory, previous: State | None
) -> ImportResult:
    """Read the portal through its webhook (read-only), turning failures into HTTP errors."""
    client: BitrixClient = factory(cipher.decrypt(project.webhook_encrypted))
    try:
        return import_portal(client, lang=project.lang, previous_state=previous)
    except BitrixError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, f"Ошибка API Битрикс24: {exc}") from exc
    except httpx.HTTPError as exc:
        message = f"Портал недоступен: {exc}"
        if "CERTIFICATE_VERIFY_FAILED" in str(exc):
            message += (
                ". Сертификат портала не принят сервером: обычно это антивирус или "
                "корпоративный прокси, проверяющий HTTPS (см. README, «Ошибка SSL»)."
            )
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, message) from exc
    finally:
        client.close()


def _state(project: Project) -> State | None:
    return State.model_validate(project.state) if project.state else None


# --- routes --------------------------------------------------------------------------


@router.get("")
def list_projects(user: User = Depends(current_user), db: Session = Depends(get_db)):
    projects = db.scalars(
        select(Project).where(Project.owner_id == user.id).order_by(Project.created_at.desc())
    )
    return [_out(p) for p in projects]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    db: Session = Depends(get_db),
    cipher: Cipher = Depends(get_cipher),
) -> ProjectOut:
    webhook, portal = _portal_of(body.webhook)
    project = Project(
        owner_id=user.id,
        name=body.name.strip(),
        portal=portal,
        webhook_encrypted=cipher.encrypt(webhook),
    )
    db.add(project)
    db.commit()
    return _out(project)


@router.get("/{project_id}")
def get_project_detail(project: Project = Depends(get_project)) -> ProjectOut:
    return _out(project)


@router.patch("/{project_id}")
def update_project(
    body: ProjectUpdate,
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    cipher: Cipher = Depends(get_cipher),
) -> ProjectOut:
    if body.name is not None:
        project.name = body.name.strip()
    if body.webhook is not None:
        webhook, portal = _portal_of(body.webhook)
        if project.spec and portal != project.portal:
            raise HTTPException(
                status.HTTP_409_CONFLICT,
                f"Вебхук от другого портала ({portal}), а проект относится к {project.portal}. "
                "Для другого портала создайте новый проект.",
            )
        project.portal = portal
        project.webhook_encrypted = cipher.encrypt(webhook)
    db.commit()
    return _out(project)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(project: Project = Depends(get_project), db: Session = Depends(get_db)) -> None:
    db.delete(project)
    db.commit()


@router.post("/{project_id}/import")
def import_project(
    project: Project = Depends(get_project),
    db: Session = Depends(get_db),
    cipher: Cipher = Depends(get_cipher),
    factory: BitrixFactory = Depends(get_bitrix_factory),
) -> ImportOut:
    result = _read_portal(project, cipher, factory, _state(project))
    # Robots cannot be read from the portal: keep the rules of the previous spec.
    spec, dropped = automation.carry_over(result.spec, _spec(project))
    project.spec = spec.model_dump(mode="json")
    project.state = result.state.model_dump(mode="json")
    project.imported_at = datetime.now(UTC)
    db.commit()
    return ImportOut(project=_out(project), warnings=result.warnings + dropped)


def _require_spec(project: Project) -> PortalSpec:
    spec = _spec(project)
    if spec is None:
        raise HTTPException(status.HTTP_409_CONFLICT, "Сначала импортируйте портал")
    return spec


@router.get("/{project_id}/spec")
def get_spec(project: Project = Depends(get_project)) -> SpecOut:
    spec = _require_spec(project)
    return SpecOut(spec=spec.model_dump(mode="json"), yaml=spec_to_yaml(spec))


@router.put("/{project_id}/spec")
def put_spec(
    body: SpecIn, project: Project = Depends(get_project), db: Session = Depends(get_db)
) -> SpecOut:
    _require_spec(project)
    try:
        spec = spec_from_yaml(body.yaml)
    except yaml.YAMLError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, f"Ошибка в YAML: {exc}") from exc
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()
        )
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Спецификация не прошла проверку: {problems}"
        ) from exc
    if spec.portal != project.portal:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            f"В спецификации портал {spec.portal}, а проект относится к {project.portal}",
        )
    project.spec = spec.model_dump(mode="json")
    db.commit()
    return SpecOut(spec=project.spec, yaml=spec_to_yaml(spec))


@router.post("/{project_id}/plan")
def plan_project(
    project: Project = Depends(get_project),
    cipher: Cipher = Depends(get_cipher),
    factory: BitrixFactory = Depends(get_bitrix_factory),
) -> PlanOut:
    """What would change on the portal to match the spec. Reads only; nothing is written."""
    desired = _require_spec(project)
    current = _read_portal(project, cipher, factory, _state(project))
    plan = diff_specs(current.spec, desired)
    manual = [
        ManualRule(key=r.key, title=r.title, stage=r.stage) for r in automation.pending(desired)
    ]
    return PlanOut(plan=plan.to_dict(), manual=manual, warnings=current.warnings)
