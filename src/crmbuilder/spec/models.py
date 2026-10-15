"""Declarative description of a Bitrix24 portal.

Every object carries a stable human-readable ``key``. Bitrix IDs (category IDs,
``C1:NEW`` stage IDs, ``UF_CRM_...`` field names) never appear here: they live in
the state file, which maps spec addresses to remote IDs.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Annotated

from pydantic import BaseModel, ConfigDict, model_validator
from pydantic import Field as PydanticField

SPEC_VERSION = 1

Key = Annotated[str, PydanticField(pattern=r"^[a-z][a-z0-9_]*$", max_length=64)]


class _Model(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StageSemantic(StrEnum):
    process = "process"
    success = "success"
    failure = "failure"


class FieldType(StrEnum):
    string = "string"
    integer = "integer"
    double = "double"
    boolean = "boolean"
    date = "date"
    datetime = "datetime"
    enumeration = "enumeration"
    employee = "employee"
    crm = "crm"
    file = "file"
    url = "url"
    money = "money"
    address = "address"
    iblock_element = "iblock_element"
    iblock_section = "iblock_section"
    # Anything the schema does not model yet is imported as-is so nothing is lost.
    other = "other"


class EntityType(StrEnum):
    lead = "lead"
    deal = "deal"
    contact = "contact"
    company = "company"


def _ensure_unique(items: list, what: str) -> None:
    seen: set[str] = set()
    for item in items:
        if item.key in seen:
            raise ValueError(f"duplicate {what} key: {item.key!r}")
        seen.add(item.key)


class Stage(_Model):
    key: Key
    name: str
    semantic: StageSemantic = StageSemantic.process
    sort: int | None = None
    color: str | None = None


class Category(_Model):
    key: Key
    name: str
    default: bool = False
    sort: int | None = None
    stages: list[Stage] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def _check_stages(self) -> Category:
        _ensure_unique(self.stages, f"stage in category {self.key!r}")
        return self


class EnumValue(_Model):
    key: Key
    value: str
    default: bool = False
    sort: int | None = None


class Field(_Model):
    key: Key
    label: str
    type: FieldType
    # Original Bitrix USER_TYPE_ID, kept only when ``type`` is ``other``.
    bitrix_type: str | None = None
    multiple: bool = False
    required: bool = False
    sort: int | None = None
    values: list[EnumValue] = PydanticField(default_factory=list)
    settings: dict = PydanticField(default_factory=dict)

    @model_validator(mode="after")
    def _check_values(self) -> Field:
        if self.values and self.type is not FieldType.enumeration:
            raise ValueError(f"field {self.key!r}: values are only allowed for enumeration")
        if self.type is FieldType.other and not self.bitrix_type:
            raise ValueError(f"field {self.key!r}: type 'other' requires bitrix_type")
        _ensure_unique(self.values, f"value in field {self.key!r}")
        return self


class SmartProcess(_Model):
    key: Key
    title: str
    code: str | None = None
    categories_enabled: bool = False
    stages_enabled: bool = False
    client_enabled: bool = False
    # Robots and triggers on the stages are only available when this is on.
    automation_enabled: bool = False
    categories: list[Category] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def _check_categories(self) -> SmartProcess:
        _ensure_unique(self.categories, f"category in smart process {self.key!r}")
        return self


class CrmSpec(_Model):
    deal_categories: list[Category] = PydanticField(default_factory=list)
    fields: dict[EntityType, list[Field]] = PydanticField(default_factory=dict)
    smart_processes: list[SmartProcess] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> CrmSpec:
        _ensure_unique(self.deal_categories, "deal category")
        if sum(c.default for c in self.deal_categories) > 1:
            raise ValueError("only one deal category can be default")
        for entity, fields in self.fields.items():
            _ensure_unique(fields, f"{entity.value} field")
        _ensure_unique(self.smart_processes, "smart process")
        return self


class Department(_Model):
    key: Key
    name: str
    parent: Key | None = None
    sort: int | None = None


class StructureSpec(_Model):
    departments: list[Department] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def _check(self) -> StructureSpec:
        _ensure_unique(self.departments, "department")
        keys = {d.key for d in self.departments}
        for dep in self.departments:
            if dep.parent is not None and dep.parent not in keys:
                raise ValueError(f"department {dep.key!r}: unknown parent {dep.parent!r}")
        return self


class AutomationKind(StrEnum):
    robot = "robot"
    trigger = "trigger"


class RobotAction(StrEnum):
    task = "task"
    notify = "notify"
    sms = "sms"
    email = "email"
    activity = "activity"
    change_responsible = "change_responsible"
    change_stage = "change_stage"
    update_fields = "update_fields"
    create_document = "create_document"
    comment = "comment"
    webhook = "webhook"
    other = "other"


class TriggerEvent(StrEnum):
    webform = "webform"
    call = "call"
    missed_call = "missed_call"
    email = "email"
    openline = "openline"
    payment = "payment"
    field_changed = "field_changed"
    webhook = "webhook"
    other = "other"


class AutomationRule(_Model):
    """A robot or trigger on a stage.

    Bitrix24 REST cannot read or write stage robots, so these rules are never
    applied: they describe what a person sets up in the UI and feed the report
    with step-by-step instructions. A robot runs when an item enters ``stage``;
    a trigger moves an item to ``stage`` when its event happens.
    """

    key: Key
    # Spec address of the stage, e.g. crm.deal_categories.sales.stages.new
    stage: str
    kind: AutomationKind = AutomationKind.robot
    action: RobotAction | None = None
    event: TriggerEvent | None = None
    title: str
    when: str | None = None
    condition: str | None = None
    responsible: str | None = None
    details: str | None = None
    # Set by a person once the rule is configured on the portal.
    done: bool = False

    @model_validator(mode="after")
    def _check_kind(self) -> AutomationRule:
        if self.kind is AutomationKind.robot:
            if self.action is None or self.event is not None:
                raise ValueError(f"robot {self.key!r}: set action, not event")
        elif self.event is None or self.action is not None:
            raise ValueError(f"trigger {self.key!r}: set event, not action")
        return self


class PortalSpec(_Model):
    version: int = SPEC_VERSION
    portal: str
    crm: CrmSpec = PydanticField(default_factory=CrmSpec)
    structure: StructureSpec = PydanticField(default_factory=StructureSpec)
    automation: list[AutomationRule] = PydanticField(default_factory=list)

    @model_validator(mode="after")
    def _check_version(self) -> PortalSpec:
        if self.version != SPEC_VERSION:
            raise ValueError(f"unsupported spec version {self.version}, expected {SPEC_VERSION}")
        return self

    @model_validator(mode="after")
    def _check_automation(self) -> PortalSpec:
        _ensure_unique(self.automation, "automation rule")
        for rule in self.automation:
            problem = automation_target_problem(self, rule.stage)
            if problem:
                raise ValueError(f"automation rule {rule.key!r}: {problem}")
        return self


def resolve_stage(
    spec: PortalSpec, address: str
) -> tuple[SmartProcess | None, Category, Stage] | None:
    """Find the stage a spec address points to: (smart process or None, pipeline, stage)."""
    parts = address.split(".")
    if len(parts) == 5 and parts[:2] == ["crm", "deal_categories"] and parts[3] == "stages":
        sp, categories, cat_key, stage_key = None, spec.crm.deal_categories, parts[2], parts[4]
    elif (
        len(parts) == 7
        and parts[:2] == ["crm", "smart_processes"]
        and parts[3] == "categories"
        and parts[5] == "stages"
    ):
        sp = next((s for s in spec.crm.smart_processes if s.key == parts[2]), None)
        if sp is None:
            return None
        categories, cat_key, stage_key = sp.categories, parts[4], parts[6]
    else:
        return None
    category = next((c for c in categories if c.key == cat_key), None)
    stage = next((s for s in category.stages if s.key == stage_key), None) if category else None
    return (sp, category, stage) if stage else None


def automation_target_problem(spec: PortalSpec, address: str) -> str:
    found = resolve_stage(spec, address)
    if found is None:
        return (
            f"stage {address!r} not found; use crm.deal_categories.<pipeline>.stages.<stage> "
            "or crm.smart_processes.<sp>.categories.<pipeline>.stages.<stage>"
        )
    sp = found[0]
    if sp is not None and not sp.automation_enabled:
        return f"smart process {sp.key!r} needs automation_enabled: true for robots and triggers"
    return ""
