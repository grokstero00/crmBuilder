"""Reads a live portal through REST and builds a PortalSpec plus its State."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crmbuilder.bitrix import BitrixClient, BitrixError
from crmbuilder.keys import KeyAllocator, is_meaningful_code
from crmbuilder.spec.models import (
    Category,
    CrmSpec,
    Department,
    EntityType,
    EnumValue,
    Field,
    FieldType,
    PortalSpec,
    SmartProcess,
    Stage,
    StageSemantic,
    StructureSpec,
)
from crmbuilder.state import State

DEAL_ENTITY_TYPE_ID = 2

_USERFIELD_METHODS = {
    EntityType.lead: "crm.lead.userfield.list",
    EntityType.deal: "crm.deal.userfield.list",
    EntityType.contact: "crm.contact.userfield.list",
    EntityType.company: "crm.company.userfield.list",
}

_SEMANTICS = {
    "p": StageSemantic.process,
    "s": StageSemantic.success,
    "f": StageSemantic.failure,
    "process": StageSemantic.process,
    "success": StageSemantic.success,
    "failure": StageSemantic.failure,
    # "Failure analysis" system stage: Bitrix lists it among the failed stages.
    "apology": StageSemantic.failure,
}

# System stage codes whose meaning never changes. Other system codes (PREPARATION,
# EXECUTING, CLIENT...) are routinely renamed into something else, e.g. a stage
# coded PREPAYMENT_INVOICE named "RFI", so those get keys from their names.
_ANCHOR_STAGE_CODES = {"NEW", "WON", "LOSE", "APOLOGY", "SUCCESS", "FAIL"}

_KNOWN_FIELD_TYPES = {t.value for t in FieldType} - {FieldType.other.value}


@dataclass
class ImportResult:
    spec: PortalSpec
    state: State
    warnings: list[str] = field(default_factory=list)


def import_portal(client: BitrixClient, *, lang: str = "ru") -> ImportResult:
    return _Importer(client, lang).run()


def _yes(value: Any) -> bool:
    return value in ("Y", "y", True, 1, "1")


def _int_or_none(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class _Importer:
    def __init__(self, client: BitrixClient, lang: str) -> None:
        self.client = client
        self.lang = lang
        self.state = State(portal=client.portal)
        self.warnings: list[str] = []

    def run(self) -> ImportResult:
        crm = CrmSpec(
            deal_categories=self._deal_categories(),
            fields=self._fields(),
            smart_processes=self._smart_processes(),
        )
        spec = PortalSpec(
            portal=self.client.portal,
            crm=crm,
            structure=StructureSpec(departments=self._departments()),
        )
        return ImportResult(spec=spec, state=self.state, warnings=self.warnings)

    # --- pipelines -------------------------------------------------------

    def _categories(
        self,
        entity_type_id: int,
        address: str,
        stage_entity_id: Callable[[int], str],
        with_stages: bool,
    ) -> list[Category]:
        result = self.client.call("crm.category.list", {"entityTypeId": entity_type_id})
        raw = sorted(result.get("categories", []), key=lambda c: _int_or_none(c.get("sort")) or 0)
        keys = KeyAllocator()
        categories = []
        for item in raw:
            cat_id = int(item["id"])
            key = keys.allocate(item.get("name", ""), fallback=f"category_{cat_id}")
            cat_address = f"{address}.{key}"
            self.state.set(cat_address, id=cat_id)
            stages = (
                self._stages(stage_entity_id(cat_id), f"{cat_address}.stages")
                if with_stages
                else []
            )
            categories.append(
                Category(
                    key=key,
                    name=item.get("name") or key,
                    default=_yes(item.get("isDefault")),
                    sort=_int_or_none(item.get("sort")),
                    stages=stages,
                )
            )
        return categories

    def _stages(self, entity_id: str, address: str) -> list[Stage]:
        items = self.client.iter_list(
            "crm.status.list",
            {"filter": {"ENTITY_ID": entity_id}, "order": {"SORT": "ASC"}},
        )
        keys = KeyAllocator()
        stages = []
        for item in items:
            status_id = str(item["STATUS_ID"])
            code = status_id.rsplit(":", 1)[-1]
            key = keys.allocate(
                code if code in _ANCHOR_STAGE_CODES else "",
                item.get("NAME", ""),
                fallback=f"stage_{item.get('ID', len(stages) + 1)}",
            )
            extra = item.get("EXTRA") or {}
            raw = extra.get("SEMANTICS") or item.get("SEMANTICS") or "process"
            semantic = _SEMANTICS.get(str(raw).lower())
            if code == "APOLOGY":
                semantic = StageSemantic.failure
            elif semantic is None:
                self.warnings.append(
                    f"{status_id}: unknown stage semantics {raw!r}, treated as process"
                )
                semantic = StageSemantic.process
            self.state.set(f"{address}.{key}", status_id=status_id, id=_int_or_none(item.get("ID")))
            stages.append(
                Stage(
                    key=key,
                    name=item.get("NAME") or key,
                    semantic=semantic,
                    sort=_int_or_none(item.get("SORT")),
                    color=extra.get("COLOR") or item.get("COLOR") or None,
                )
            )
        return stages

    def _deal_categories(self) -> list[Category]:
        return self._categories(
            DEAL_ENTITY_TYPE_ID,
            "crm.deal_categories",
            lambda cid: "DEAL_STAGE" if cid == 0 else f"DEAL_STAGE_{cid}",
            with_stages=True,
        )

    def _fields(self) -> dict[EntityType, list[Field]]:
        fields: dict[EntityType, list[Field]] = {}
        for entity, method in _USERFIELD_METHODS.items():
            try:
                items = list(
                    self.client.iter_list(
                        method, {"order": {"SORT": "ASC"}, "filter": {"LANG": self.lang}}
                    )
                )
            except BitrixError as exc:
                self.warnings.append(f"{method}: skipped ({exc.code})")
                continue
            if items:
                fields[entity] = self._entity_fields(entity, items)
        return fields

    def _entity_fields(self, entity: EntityType, items: list[dict]) -> list[Field]:
        keys = KeyAllocator()
        result = []
        for item in items:
            field_name = str(item["FIELD_NAME"])
            code = field_name.removeprefix("UF_CRM_").removeprefix("UF_")
            label = self._label(item) or field_name
            key = keys.allocate(
                code if is_meaningful_code(code) else "", label, fallback=field_name.lower()
            )
            address = f"crm.fields.{entity.value}.{key}"
            self.state.set(address, id=_int_or_none(item.get("ID")), field_name=field_name)

            user_type = str(item.get("USER_TYPE_ID", ""))
            known = user_type in _KNOWN_FIELD_TYPES
            if not known:
                self.warnings.append(f"{field_name}: type {user_type!r} imported as 'other'")
            ftype = FieldType(user_type) if known else FieldType.other

            values = []
            if ftype is FieldType.enumeration:
                values = self._enum_values(item.get("LIST") or [], f"{address}.values")

            result.append(
                Field(
                    key=key,
                    label=label,
                    type=ftype,
                    bitrix_type=None if known else user_type,
                    multiple=_yes(item.get("MULTIPLE")),
                    required=_yes(item.get("MANDATORY")),
                    sort=_int_or_none(item.get("SORT")),
                    values=values,
                    settings=_compact(item.get("SETTINGS")),
                )
            )
        return result

    def _enum_values(self, items: list[dict], address: str) -> list[EnumValue]:
        keys = KeyAllocator()
        values = []
        for item in items:
            xml_id = str(item.get("XML_ID") or "")
            key = keys.allocate(
                xml_id if is_meaningful_code(xml_id) else "",
                str(item.get("VALUE", "")),
                fallback=f"value_{item.get('ID', len(values) + 1)}",
            )
            self.state.set(f"{address}.{key}", id=_int_or_none(item.get("ID")))
            values.append(
                EnumValue(
                    key=key,
                    value=str(item.get("VALUE", "")),
                    default=_yes(item.get("DEF")),
                    sort=_int_or_none(item.get("SORT")),
                )
            )
        return values

    def _label(self, item: dict) -> str:
        for name in ("EDIT_FORM_LABEL", "LIST_COLUMN_LABEL", "LIST_FILTER_LABEL"):
            label = item.get(name)
            if isinstance(label, dict):
                label = label.get(self.lang) or next((v for v in label.values() if v), "")
            if label:
                return str(label)
        return ""

    def _smart_processes(self) -> list[SmartProcess]:
        try:
            types = list(self.client.iter_list("crm.type.list", items_key="types"))
        except BitrixError as exc:
            self.warnings.append(f"crm.type.list: skipped ({exc.code})")
            return []
        keys = KeyAllocator()
        result = []
        for item in types:
            entity_type_id = int(item["entityTypeId"])
            code = str(item.get("code") or "")
            key = keys.allocate(code, item.get("title", ""), fallback=f"smart_{entity_type_id}")
            address = f"crm.smart_processes.{key}"
            self.state.set(address, id=int(item["id"]), entity_type_id=entity_type_id)
            stages_enabled = _yes(item.get("isStagesEnabled"))
            categories = self._categories(
                entity_type_id,
                f"{address}.categories",
                lambda cid, etid=entity_type_id: f"DYNAMIC_{etid}_STAGE_{cid}",
                with_stages=stages_enabled,
            )
            result.append(
                SmartProcess(
                    key=key,
                    title=item.get("title") or key,
                    code=code or None,
                    categories_enabled=_yes(item.get("isCategoriesEnabled")),
                    stages_enabled=stages_enabled,
                    client_enabled=_yes(item.get("isClientEnabled")),
                    categories=categories,
                )
            )
        return result

    def _departments(self) -> list[Department]:
        try:
            items = list(self.client.iter_list("department.get", {"sort": "SORT", "order": "ASC"}))
        except BitrixError as exc:
            self.warnings.append(f"department.get: skipped ({exc.code})")
            return []
        keys = KeyAllocator()
        key_by_id = {
            str(item["ID"]): keys.allocate(
                item.get("NAME", ""), fallback=f"department_{item['ID']}"
            )
            for item in items
        }
        departments = []
        for item in items:
            dep_id = str(item["ID"])
            key = key_by_id[dep_id]
            self.state.set(f"structure.departments.{key}", id=int(dep_id))
            parent = str(item["PARENT"]) if item.get("PARENT") else None
            if parent is not None and parent not in key_by_id:
                self.warnings.append(f"department {dep_id}: parent {parent} not found, detached")
                parent = None
            departments.append(
                Department(
                    key=key,
                    name=item.get("NAME") or key,
                    parent=key_by_id.get(parent) if parent else None,
                    sort=_int_or_none(item.get("SORT")),
                )
            )
        return departments


def _compact(settings: Any) -> dict:
    """Drop empty values so YAML stays readable without losing meaningful ones."""
    if not isinstance(settings, dict):
        return {}
    return {k: v for k, v in settings.items() if v is not None and v != "" and v != [] and v != {}}
