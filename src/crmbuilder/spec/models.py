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


class PortalSpec(_Model):
    version: int = SPEC_VERSION
    portal: str
    crm: CrmSpec = PydanticField(default_factory=CrmSpec)
    structure: StructureSpec = PydanticField(default_factory=StructureSpec)

    @model_validator(mode="after")
    def _check_version(self) -> PortalSpec:
        if self.version != SPEC_VERSION:
            raise ValueError(f"unsupported spec version {self.version}, expected {SPEC_VERSION}")
        return self
