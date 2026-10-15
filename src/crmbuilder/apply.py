"""Carry out a plan on a live portal.

Nothing is written unless every change in the plan can be carried out: changes
Bitrix24 cannot make (moving a stage to another group) or that are not supported
yet (smart processes themselves) are reported up front by ``unsupported()``.

Changes run creates first and deletes last. Every REST call goes to the journal.
The first error stops the run; ``state`` always reflects what was really done.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from crmbuilder.bitrix import BitrixClient
from crmbuilder.importer.portal import DEAL_ENTITY_TYPE_ID, SMART_PROCESS_LINK_PREFIX
from crmbuilder.plan import Action, Change, Plan
from crmbuilder.spec.defaults import FIELD_SETTING_DEFAULTS, strip_defaults
from crmbuilder.spec.models import (
    Category,
    Department,
    EntityType,
    Field,
    FieldType,
    PortalSpec,
    Stage,
    StageSemantic,
)
from crmbuilder.state import State

Journal = Callable[[dict[str, Any]], None]

_KEY = r"[a-z][a-z0-9_]*"
_CATEGORY = rf"(?P<scope>crm\.deal_categories|crm\.smart_processes\.(?P<sp>{_KEY})\.categories)\.(?P<cat>{_KEY})"  # noqa: E501
_PATTERNS = {
    "category": re.compile(rf"^{_CATEGORY}$"),
    "stage": re.compile(rf"^{_CATEGORY}\.stages\.(?P<stage>{_KEY})$"),
    "field": re.compile(rf"^crm\.fields\.(?P<entity>[a-z]+)\.(?P<field>{_KEY})$"),
    "value": re.compile(
        rf"^(?P<field_address>crm\.fields\.(?P<entity>[a-z]+)\.(?P<field>{_KEY}))"
        rf"\.values\.(?P<value>{_KEY})$"
    ),
    "smart_process": re.compile(rf"^crm\.smart_processes\.(?P<sp>{_KEY})$"),
    "department": re.compile(rf"^structure\.departments\.(?P<dep>{_KEY})$"),
}

_SEMANTICS_CODE = {StageSemantic.process: "", StageSemantic.success: "S",
                   StageSemantic.failure: "F"}  # fmt: skip
_ANCHOR_CODES = {"NEW", "WON", "LOSE", "APOLOGY", "SUCCESS", "FAIL"}


class ApplyError(RuntimeError):
    pass


@dataclass
class Unsupported:
    change: Change
    reason: str


@dataclass
class ApplyResult:
    done: list[Change] = field(default_factory=list)
    failed: Change | None = None
    error: str = ""
    warnings: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.failed is None


def _parse(address: str) -> tuple[str, dict[str, str]]:
    for kind, pattern in _PATTERNS.items():
        if match := pattern.match(address):
            return kind, {k: v for k, v in match.groupdict().items() if v is not None}
    raise ApplyError(f"unknown address {address}")


def _yn(value: bool) -> str:
    return "Y" if value else "N"


def _code(key: str, limit: int) -> str:
    return re.sub(r"[^A-Z0-9_]", "_", key.upper())[:limit].rstrip("_") or "X"


class Applier:
    def __init__(
        self,
        client: BitrixClient,
        current: PortalSpec,
        desired: PortalSpec,
        state: State,
        journal: Journal | None = None,
    ) -> None:
        self.client = client
        self.current = current
        self.desired = desired
        self.state = state
        self.journal = journal or (lambda entry: None)
        self.result = ApplyResult()
        self._handled: set[str] = set()
        self._planned: set[str] = set()
        self._created_sps: set[str] = set()

    # --- spec lookups -------------------------------------------------------------

    @staticmethod
    def _categories(spec: PortalSpec, parts: dict) -> list[Category]:
        if "sp" not in parts:
            return spec.crm.deal_categories
        sp = next((s for s in spec.crm.smart_processes if s.key == parts["sp"]), None)
        return sp.categories if sp else []

    def _category(self, spec: PortalSpec, parts: dict) -> Category | None:
        return next((c for c in self._categories(spec, parts) if c.key == parts["cat"]), None)

    def _stage(self, spec: PortalSpec, parts: dict) -> Stage | None:
        category = self._category(spec, parts)
        if category is None:
            return None
        return next((s for s in category.stages if s.key == parts["stage"]), None)

    @staticmethod
    def _field(spec: PortalSpec, parts: dict) -> Field | None:
        fields = spec.crm.fields.get(EntityType(parts["entity"]), [])
        return next((f for f in fields if f.key == parts["field"]), None)

    @staticmethod
    def _department(spec: PortalSpec, key: str) -> Department | None:
        return next((d for d in spec.structure.departments if d.key == key), None)

    def _entity_type_id(self, parts: dict) -> int | None:
        if "sp" not in parts:
            return DEAL_ENTITY_TYPE_ID
        remote = self.state.get(f"crm.smart_processes.{parts['sp']}")
        return int(remote["entity_type_id"]) if remote else None

    def _sp_available(self, sp_key: str) -> bool:
        """The smart process exists on the portal or is created earlier in this plan."""
        address = f"crm.smart_processes.{sp_key}"
        return self.state.get(address) is not None or address in self._created_sps

    def _desired_sp(self, sp_key: str):
        return next((s for s in self.desired.crm.smart_processes if s.key == sp_key), None)

    def _stages_managed(self, parts: dict) -> bool:
        if "sp" not in parts:
            return True
        sp = next((s for s in self.desired.crm.smart_processes if s.key == parts["sp"]), None)
        return bool(sp and sp.stages_enabled)

    def _remote_id(self, address: str, identity: str = "id") -> Any:
        remote = self.state.get(address)
        if remote is None or remote.get(identity) is None:
            raise ApplyError(f"{address}: not found in the state file")
        return remote[identity]

    # --- checks before writing ----------------------------------------------------------

    def unsupported(self, plan: Plan) -> list[Unsupported]:
        self._note_plan(plan)
        problems = []
        for change in plan.changes:
            reason = self._unsupported_reason(change)
            if reason:
                problems.append(Unsupported(change, reason))
        return problems

    def _note_plan(self, plan: Plan) -> None:
        self._planned = {c.address for c in plan.changes}
        self._created_sps = {
            c.address
            for c in plan.changes
            if c.action is Action.create and _parse(c.address)[0] == "smart_process"
        }

    def _smart_process_reason(self, change: Change, sp_key: str) -> str:
        if change.action is Action.delete:
            return ""
        sp = self._desired_sp(sp_key)
        if change.action is Action.update and "stages_enabled" in change.attributes:
            return (
                "turning stages of an existing smart process on or off is not supported: "
                "change it in Bitrix24 and run import"
            )
        if len(sp.categories) > 1 and not sp.categories_enabled:
            return "several pipelines need categories_enabled: true"
        if change.action is Action.create:
            defaults = [c for c in sp.categories if c.default]
            if len(defaults) != 1:
                return (
                    "a new smart process always gets one default pipeline from Bitrix24: "
                    "mark exactly one category with default: true"
                )
            if not sp.stages_enabled and any(c.stages for c in sp.categories):
                return "stages are listed but stages_enabled is false"
            if sp.stages_enabled and not defaults[0].stages:
                return "stages_enabled is true: list the stages of the default pipeline"
        return ""

    def _unsupported_reason(self, change: Change) -> str:
        kind, parts = _parse(change.address)
        attrs = change.attributes
        if kind == "smart_process":
            return self._smart_process_reason(change, parts["sp"])
        if kind in ("category", "stage") and "sp" in parts and not self._sp_available(parts["sp"]):
            return f"smart process {parts['sp']!r} does not exist on the portal"
        if kind == "category":
            new_sp = "sp" in parts and f"crm.smart_processes.{parts['sp']}" in self._created_sps
            if (
                change.action is Action.create
                and self._category(self.desired, parts).default
                and not new_sp  # the default pipeline of a new smart process comes from Bitrix
            ):
                return "a new pipeline cannot be made the default one"
            if change.action is Action.update and "default" in attrs:
                return "changing the default pipeline is not supported"
        if kind == "stage":
            if change.action is Action.update and "semantic" in attrs:
                return (
                    "Bitrix24 cannot move a stage to another group (in progress / success / "
                    "failure): create a new stage and delete this one"
                )
            category_exists = self.state.get(f"{parts['scope']}.{parts['cat']}") is not None
            stage = self._stage(self.desired, parts)
            if (
                change.action is Action.create
                and category_exists
                and stage.semantic is StageSemantic.success
            ):
                return "a pipeline has exactly one success stage: rename the existing one"
        if kind == "department" and change.action is Action.create:
            dep = self._department(self.desired, parts["dep"])
            if dep.parent is None:
                return "a new department needs a parent department"
        if kind == "field" and change.action is not Action.delete:
            desired = self._field(self.desired, parts)
            for setting in desired.settings:
                if setting.startswith(SMART_PROCESS_LINK_PREFIX):
                    sp_key = setting.removeprefix(SMART_PROCESS_LINK_PREFIX)
                    if not self._sp_available(sp_key):
                        return f"links to smart process {sp_key!r}, which does not exist"
        return ""

    # --- running -------------------------------------------------------------------

    def apply(self, plan: Plan) -> ApplyResult:
        self._note_plan(plan)
        for change in sorted(plan.changes, key=self._order):
            if change.address in self._handled:
                self.result.done.append(change)
                continue
            try:
                self._run(change, plan)
            except Exception as exc:  # noqa: BLE001 - every failure must stop the run
                self.result.failed = change
                self.result.error = str(exc)
                break
            self._handled.add(change.address)
            self.result.done.append(change)
        return self.result

    def _order(self, change: Change) -> tuple:
        kind, parts = _parse(change.address)
        if kind == "department":
            dep = self._department(self.desired, parts["dep"]) or self._department(
                self.current, parts["dep"]
            )
            depth = self._depth(dep)
            if change.action is Action.create:
                return (0, depth)
            if change.action is Action.delete:
                return (8, -depth)
            return (4, 0)
        if kind == "smart_process":
            return {Action.create: (0.5,), Action.update: (1,)}.get(change.action, (9,))
        if change.action is Action.delete:
            return {"stage": (5,), "value": (3,), "field": (6,), "category": (7,)}.get(kind, (9,))
        return {"category": (1,), "stage": (2,), "field": (3,), "value": (3,)}.get(kind, (9,))

    def _depth(self, dep: Department | None) -> int:
        depth, seen = 0, set()
        while dep is not None and dep.parent and dep.parent not in seen:
            seen.add(dep.parent)
            depth += 1
            dep = self._department(self.desired, dep.parent) or self._department(
                self.current, dep.parent
            )
        return depth

    def _call(self, method: str, params: dict[str, Any]) -> Any:
        entry: dict[str, Any] = {"method": method, "params": params}
        try:
            result = self.client.call(method, params)
        except Exception as exc:
            entry["error"] = str(exc)
            self.journal(entry)
            raise
        entry["result"] = result
        self.journal(entry)
        return result

    def _run(self, change: Change, plan: Plan) -> None:
        kind, parts = _parse(change.address)
        handler = getattr(self, f"_{change.action.value}_{kind}", None)
        if kind in ("field", "value"):
            field_address = f"crm.fields.{parts['entity']}.{parts['field']}"
            return self._field_changes(field_address, parts, plan)
        if handler is None:
            raise ApplyError(f"{change.action.value} of {kind} is not supported")
        return handler(change, parts)

    # --- pipelines -------------------------------------------------------------------

    def _create_category(self, change: Change, parts: dict) -> None:
        desired = self._category(self.desired, parts)
        entity_type_id = self._entity_type_id(parts)
        fields: dict[str, Any] = {"name": desired.name}
        if desired.sort is not None:
            fields["sort"] = desired.sort
        result = self._call("crm.category.add", {"entityTypeId": entity_type_id, "fields": fields})
        cat_id = int(result["category"]["id"])
        address = change.address
        self.state.set(address, id=cat_id)
        # Stages listed under a new pipeline are created here, from its default stages.
        prefix = f"{address}.stages."
        self._handled.update(a for a in self._planned if a.startswith(prefix))
        if self._stages_managed(parts):
            self._reconcile_new_stages(address, entity_type_id, cat_id, desired.stages)

    def _reconcile_new_stages(
        self, address: str, entity_type_id: int, cat_id: int, desired: list[Stage]
    ) -> None:
        """Turn the default stages Bitrix created for a new pipeline into ``desired``."""
        entity_id = _stage_entity(entity_type_id, cat_id)
        auto = list(
            self.client.iter_list(
                "crm.status.list", {"filter": {"ENTITY_ID": entity_id}, "order": {"SORT": "ASC"}}
            )
        )
        matched: dict[str, dict] = {}
        free = list(auto)

        def take(stage: Stage, predicate) -> None:
            item = next((a for a in free if predicate(a)), None)
            if item is not None:
                matched[stage.key] = item
                free.remove(item)

        for stage in desired:  # same system code: new -> NEW, won -> WON...
            if stage.key.upper() in _ANCHOR_CODES:
                take(stage, lambda a, s=stage: _status_code(a) == s.key.upper())
        for stage in desired:  # then pair the rest by group, in order
            if stage.key not in matched:
                take(stage, lambda a, s=stage: _semantic(a) is s.semantic)

        for stage in desired:
            stage_address = f"{address}.stages.{stage.key}"
            item = matched.get(stage.key)
            if item is None:
                self._add_stage(stage_address, entity_id, stage, {_status_code(a) for a in auto})
                continue
            fields = {}
            if item.get("NAME") != stage.name:
                fields["NAME"] = stage.name
            if stage.sort is not None and str(item.get("SORT")) != str(stage.sort):
                fields["SORT"] = stage.sort
            if stage.color is not None and item.get("COLOR") != stage.color:
                fields["COLOR"] = stage.color
            if fields:
                self._call("crm.status.update", {"id": int(item["ID"]), "fields": fields})
            self.state.set(stage_address, status_id=item["STATUS_ID"], id=int(item["ID"]))
        for item in free:  # leftovers of a brand-new, empty pipeline
            self._call("crm.status.delete", {"id": int(item["ID"]), "params": {"FORCED": "Y"}})

    def _update_category(self, change: Change, parts: dict) -> None:
        desired = self._category(self.desired, parts)
        fields: dict[str, Any] = {}
        if "name" in change.attributes:
            fields["name"] = desired.name
        if "sort" in change.attributes:
            fields["sort"] = desired.sort
        self._call(
            "crm.category.update",
            {
                "entityTypeId": self._entity_type_id(parts),
                "id": self._remote_id(change.address),
                "fields": fields,
            },
        )

    def _delete_category(self, change: Change, parts: dict) -> None:
        self._call(
            "crm.category.delete",
            {"entityTypeId": self._entity_type_id(parts), "id": self._remote_id(change.address)},
        )
        self.state.drop(change.address)

    # --- smart processes -----------------------------------------------------------------

    def _create_smart_process(self, change: Change, parts: dict) -> None:
        sp = self._desired_sp(parts["sp"])
        fields = {
            "title": sp.title,
            "code": sp.code or sp.key,
            "isStagesEnabled": _yn(sp.stages_enabled),
            "isCategoriesEnabled": _yn(sp.categories_enabled),
            "isClientEnabled": _yn(sp.client_enabled),
            "isAutomationEnabled": _yn(sp.automation_enabled),
        }
        created = self._call("crm.type.add", {"fields": fields})["type"]
        entity_type_id = int(created["entityTypeId"])
        self.state.set(change.address, id=int(created["id"]), entity_type_id=entity_type_id)

        # Bitrix24 creates the default pipeline itself: turn it into the desired one.
        desired = next(c for c in sp.categories if c.default)
        result = self.client.call("crm.category.list", {"entityTypeId": entity_type_id})
        auto = result.get("categories", [])
        default = next((c for c in auto if c.get("isDefault") == "Y"), auto[0] if auto else None)
        if default is None:
            raise ApplyError(f"{change.address}: Bitrix24 created no default pipeline")
        cat_id = int(default["id"])
        cat_address = f"{change.address}.categories.{desired.key}"
        self.state.set(cat_address, id=cat_id)
        fields = {}
        if default.get("name") != desired.name:
            fields["name"] = desired.name
        if desired.sort is not None and str(default.get("sort")) != str(desired.sort):
            fields["sort"] = desired.sort
        if fields:
            self._call(
                "crm.category.update",
                {"entityTypeId": entity_type_id, "id": cat_id, "fields": fields},
            )
        self._handled.add(cat_address)
        prefix = f"{cat_address}.stages."
        self._handled.update(a for a in self._planned if a.startswith(prefix))
        if sp.stages_enabled:
            self._reconcile_new_stages(cat_address, entity_type_id, cat_id, desired.stages)

    def _update_smart_process(self, change: Change, parts: dict) -> None:
        sp = self._desired_sp(parts["sp"])
        names = {
            "title": ("title", lambda v: v),
            "code": ("code", lambda v: v or ""),
            "client_enabled": ("isClientEnabled", _yn),
            "categories_enabled": ("isCategoriesEnabled", _yn),
            "automation_enabled": ("isAutomationEnabled", _yn),
        }
        fields = {names[a][0]: names[a][1](getattr(sp, a)) for a in change.attributes if a in names}
        self._call("crm.type.update", {"id": self._remote_id(change.address), "fields": fields})

    def _delete_smart_process(self, change: Change, parts: dict) -> None:
        self._call("crm.type.delete", {"id": self._remote_id(change.address)})
        self.state.drop(change.address)

    # --- stages -------------------------------------------------------------------------

    def _create_stage(self, change: Change, parts: dict) -> None:
        category_address = f"{parts['scope']}.{parts['cat']}"
        cat_id = int(self._remote_id(category_address))
        entity_id = _stage_entity(self._entity_type_id(parts), cat_id)
        self._add_stage(change.address, entity_id, self._stage(self.desired, parts))

    def _add_stage(
        self, address: str, entity_id: str, stage: Stage, taken: set[str] | None = None
    ) -> None:
        category_address = address.rsplit(".stages.", 1)[0]
        taken = set(taken or ()) | {
            status_id.rsplit(":", 1)[-1]
            for status_id in self.state.children(f"{category_address}.stages", "status_id")
        }
        base = _code(stage.key, 20)
        code, n = base, 2
        while code in taken:
            code = f"{base[:17]}_{n}"
            n += 1
        fields: dict[str, Any] = {
            "ENTITY_ID": entity_id,
            "STATUS_ID": code,
            "NAME": stage.name,
            "SEMANTICS": _SEMANTICS_CODE[stage.semantic],
        }
        if stage.sort is not None:
            fields["SORT"] = stage.sort
        if stage.color is not None:
            fields["COLOR"] = stage.color
        new_id = int(self._call("crm.status.add", {"fields": fields}))
        created = self._call("crm.status.get", {"id": new_id})
        self.state.set(address, status_id=created["STATUS_ID"], id=new_id)

    def _update_stage(self, change: Change, parts: dict) -> None:
        desired = self._stage(self.desired, parts)
        names = {"name": "NAME", "sort": "SORT", "color": "COLOR"}
        fields = {names[a]: getattr(desired, a) for a in change.attributes if a in names}
        self._call("crm.status.update", {"id": self._remote_id(change.address), "fields": fields})

    def _delete_stage(self, change: Change, parts: dict) -> None:
        self._call(
            "crm.status.delete",
            {"id": self._remote_id(change.address), "params": {"FORCED": "Y"}},
        )
        self.state.drop(change.address)

    # --- user fields --------------------------------------------------------------------

    def _field_changes(self, field_address: str, parts: dict, plan: Plan) -> None:
        """All changes of one field and its values go out together."""
        if field_address in self._handled:
            return
        entity = parts["entity"]
        own = next((c for c in plan.changes if c.address == field_address), None)
        value_changes = [c for c in plan.changes if c.address.startswith(field_address + ".")]
        desired = self._field(self.desired, parts)

        if own is not None and own.action is Action.delete:
            self._call(f"crm.{entity}.userfield.delete", {"id": self._remote_id(field_address)})
            self.state.drop(field_address)
        elif own is not None and own.action is Action.replace:
            self._call(f"crm.{entity}.userfield.delete", {"id": self._remote_id(field_address)})
            self.state.drop(field_address)
            self._add_field(field_address, entity, desired)
        elif own is not None and own.action is Action.create:
            self._add_field(field_address, entity, desired)
        else:
            self._update_field(field_address, entity, desired, own, value_changes)

        self._handled.add(field_address)
        self._handled.update(c.address for c in value_changes)

    def _settings(self, settings: dict) -> dict:
        result = {}
        for key, value in settings.items():
            if key.startswith(SMART_PROCESS_LINK_PREFIX):
                sp_key = key.removeprefix(SMART_PROCESS_LINK_PREFIX)
                remote = self._remote_id(f"crm.smart_processes.{sp_key}", "entity_type_id")
                key = f"DYNAMIC_{remote}"
            result[key] = value
        return result

    def _add_field(self, address: str, entity: str, desired: Field) -> None:
        taken = set(self.state.children(f"crm.fields.{entity}", "field_name"))
        base = _code(desired.key, 30)
        code, n = base, 2
        while f"UF_CRM_{code}" in taken:
            code = f"{base[:27]}_{n}"
            n += 1
        fields: dict[str, Any] = {
            "FIELD_NAME": code,
            "USER_TYPE_ID": desired.bitrix_type or desired.type.value,
            "LABEL": desired.label,
            "EDIT_FORM_LABEL": desired.label,
            "LIST_COLUMN_LABEL": desired.label,
            "LIST_FILTER_LABEL": desired.label,
            "MULTIPLE": _yn(desired.multiple),
            "MANDATORY": _yn(desired.required),
        }
        if desired.sort is not None:
            fields["SORT"] = desired.sort
        if desired.settings:
            fields["SETTINGS"] = self._settings(desired.settings)
        if desired.type is FieldType.enumeration:
            fields["LIST"] = [self._new_value(v) for v in desired.values]
        new_id = int(self._call(f"crm.{entity}.userfield.add", {"fields": fields}))
        created = self._call(f"crm.{entity}.userfield.get", {"id": new_id})
        self.state.set(address, id=new_id, field_name=created["FIELD_NAME"])
        self._record_values(address, desired, created)

    @staticmethod
    def _new_value(value) -> dict[str, Any]:
        item: dict[str, Any] = {
            "VALUE": value.value,
            "DEF": _yn(value.default),
            "XML_ID": value.key,
        }
        if value.sort is not None:
            item["SORT"] = value.sort
        return item

    def _record_values(self, address: str, desired: Field, remote: dict) -> None:
        items = remote.get("LIST") or []
        by_xml = {str(i.get("XML_ID")): i for i in items}
        by_value = {str(i.get("VALUE")): i for i in items}
        for value in desired.values:
            value_address = f"{address}.values.{value.key}"
            if self.state.get(value_address) is not None:
                continue
            item = by_xml.get(value.key) or by_value.get(value.value)
            if item is not None:
                self.state.set(value_address, id=int(item["ID"]))

    def _update_field(
        self,
        address: str,
        entity: str,
        desired: Field,
        own: Change | None,
        value_changes: list[Change],
    ) -> None:
        fields: dict[str, Any] = {}
        attrs = own.attributes if own else {}
        if "label" in attrs:
            for name in ("EDIT_FORM_LABEL", "LIST_COLUMN_LABEL", "LIST_FILTER_LABEL"):
                fields[name] = desired.label
        if "required" in attrs:
            fields["MANDATORY"] = _yn(desired.required)
        if "sort" in attrs:
            fields["SORT"] = desired.sort
        if "settings" in attrs:
            current_settings, _ = attrs["settings"]
            payload = self._settings(strip_defaults(desired.type, desired.settings))
            defaults = FIELD_SETTING_DEFAULTS.get(desired.type, {})
            # Bitrix only overwrites settings that are passed: reset removed ones explicitly.
            for key in current_settings or {}:
                translated = next(iter(self._settings({key: None})))
                if translated in payload:
                    continue
                if key in defaults:
                    payload[translated] = defaults[key]
                else:
                    self.result.warnings.append(
                        f"{address}: setting {key} cannot be reset through the API, "
                        "remove it in Bitrix24"
                    )
            fields["SETTINGS"] = payload

        values = {v.key: v for v in desired.values}
        items = []
        for change in value_changes:
            key = change.address.rsplit(".", 1)[-1]
            if change.action is Action.create:
                items.append(self._new_value(values[key]))
            elif change.action is Action.update:
                value = values[key]
                item = {"ID": self._remote_id(change.address), "VALUE": value.value,
                        "DEF": _yn(value.default)}  # fmt: skip
                if value.sort is not None:
                    item["SORT"] = value.sort
                items.append(item)
            elif change.action is Action.delete:
                items.append({"ID": self._remote_id(change.address), "DEL": "Y"})
                self.state.drop(change.address)
        if items:
            fields["LIST"] = items
        if not fields:
            return
        field_id = self._remote_id(address)
        self._call(f"crm.{entity}.userfield.update", {"id": field_id, "fields": fields})
        if any(c.action is Action.create for c in value_changes):
            remote = self._call(f"crm.{entity}.userfield.get", {"id": field_id})
            self._record_values(address, desired, remote)

    # --- departments ------------------------------------------------------------------

    def _create_department(self, change: Change, parts: dict) -> None:
        dep = self._department(self.desired, parts["dep"])
        params: dict[str, Any] = {
            "NAME": dep.name,
            "PARENT": self._remote_id(f"structure.departments.{dep.parent}"),
        }
        if dep.sort is not None:
            params["SORT"] = dep.sort
        new_id = int(self._call("department.add", params))
        self.state.set(change.address, id=new_id)

    def _update_department(self, change: Change, parts: dict) -> None:
        dep = self._department(self.desired, parts["dep"])
        params: dict[str, Any] = {"ID": self._remote_id(change.address)}
        if "name" in change.attributes:
            params["NAME"] = dep.name
        if "sort" in change.attributes:
            params["SORT"] = dep.sort
        if "parent" in change.attributes:
            if dep.parent is None:
                raise ApplyError("a department cannot be moved to the top level")
            params["PARENT"] = self._remote_id(f"structure.departments.{dep.parent}")
        self._call("department.update", params)

    def _delete_department(self, change: Change, parts: dict) -> None:
        self._call("department.delete", {"ID": self._remote_id(change.address)})
        self.state.drop(change.address)


def _stage_entity(entity_type_id: int, cat_id: int) -> str:
    if entity_type_id == DEAL_ENTITY_TYPE_ID:
        return "DEAL_STAGE" if cat_id == 0 else f"DEAL_STAGE_{cat_id}"
    return f"DYNAMIC_{entity_type_id}_STAGE_{cat_id}"


def _status_code(item: dict) -> str:
    return str(item["STATUS_ID"]).rsplit(":", 1)[-1]


def _semantic(item: dict) -> StageSemantic:
    extra = item.get("EXTRA") or {}
    raw = str(extra.get("SEMANTICS") or item.get("SEMANTICS") or "process").lower()
    if raw in ("s", "success"):
        return StageSemantic.success
    if raw in ("f", "failure", "apology") or _status_code(item) == "APOLOGY":
        return StageSemantic.failure
    return StageSemantic.process
