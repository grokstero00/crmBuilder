"""Compare the current portal spec with the desired one and list the changes.

Objects are matched by key. A key present only in the desired spec is created,
one present only in the current spec is deleted, and attributes that differ
become an update. Field type and multiplicity cannot be changed in Bitrix24, so
such edits are planned as a replace (delete + create), which loses data.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from crmbuilder.spec.defaults import strip_defaults
from crmbuilder.spec.models import (
    Category,
    Department,
    EnumValue,
    Field,
    PortalSpec,
    SmartProcess,
    Stage,
)


class Action(StrEnum):
    create = "create"
    update = "update"
    delete = "delete"
    replace = "replace"


_SYMBOLS = {Action.create: "+", Action.update: "~", Action.delete: "-", Action.replace: "!"}

# Attributes a person may leave out of the desired spec without asking for a change.
_OPTIONAL = {"sort", "color", "code"}

_DELETE_NOTES = {
    "deal_categories": "deals in this pipeline must be moved or will be lost",
    "stages": "items in this stage must be moved first",
    "fields": "data stored in this field will be lost",
    "values": "records with this value will lose it",
    "smart_processes": "all items of this smart process will be deleted",
    "categories": "items in this pipeline must be moved or will be lost",
    "departments": "employees of this department must be reassigned",
}


@dataclass
class Change:
    action: Action
    address: str
    # attribute -> (current, desired); for a create only the desired side is set.
    attributes: dict[str, tuple[Any, Any]] = field(default_factory=dict)
    note: str = ""

    @property
    def destructive(self) -> bool:
        return self.action in (Action.delete, Action.replace)

    def to_dict(self) -> dict[str, Any]:
        return {
            "action": self.action.value,
            "address": self.address,
            "destructive": self.destructive,
            "attributes": {
                name: {"current": cur, "desired": des}
                for name, (cur, des) in self.attributes.items()
            },
            "note": self.note,
        }


@dataclass
class Plan:
    portal: str
    changes: list[Change] = field(default_factory=list)

    def count(self, action: Action) -> int:
        return sum(c.action is action for c in self.changes)

    @property
    def destructive(self) -> list[Change]:
        return [c for c in self.changes if c.destructive]

    def to_dict(self) -> dict[str, Any]:
        return {
            "portal": self.portal,
            "summary": {a.value: self.count(a) for a in Action},
            "changes": [c.to_dict() for c in self.changes],
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, indent=2)

    def render(self) -> str:
        if not self.changes:
            return f"Plan for {self.portal}: no changes, the portal matches the spec."
        lines = [f"Plan for {self.portal}", ""]
        for change in self.changes:
            symbol = _SYMBOLS[change.action]
            lines.append(f"  {symbol} {change.action.value:<8}{change.address}")
            for name, (cur, des) in change.attributes.items():
                if change.action is Action.create:
                    lines.append(f"        {name}: {_fmt(des)}")
                else:
                    lines.append(f"        {name}: {_fmt(cur)} -> {_fmt(des)}")
            if change.note:
                marker = "WARNING" if change.destructive else "note"
                lines.append(f"        {marker}: {change.note}")
        summary = ", ".join(
            f"{self.count(a)} to {a.value}" for a in Action if self.count(a) or a is Action.create
        )
        lines += ["", f"Plan: {summary}."]
        if self.destructive:
            lines.append(
                f"{len(self.destructive)} destructive change(s) marked with - or ! can lose data."
            )
        return "\n".join(lines)


def _fmt(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False)


def diff_specs(current: PortalSpec, desired: PortalSpec) -> Plan:
    return _Differ(Plan(portal=current.portal)).run(current, desired)


class _Differ:
    def __init__(self, plan: Plan) -> None:
        self.plan = plan

    def run(self, current: PortalSpec, desired: PortalSpec) -> Plan:
        self._keyed(
            "crm.deal_categories",
            current.crm.deal_categories,
            desired.crm.deal_categories,
            self._category,
        )
        entities = sorted(set(current.crm.fields) | set(desired.crm.fields), key=lambda e: e.value)
        for entity in entities:
            self._keyed(
                f"crm.fields.{entity.value}",
                current.crm.fields.get(entity, []),
                desired.crm.fields.get(entity, []),
                self._field,
            )
        self._keyed(
            "crm.smart_processes",
            current.crm.smart_processes,
            desired.crm.smart_processes,
            self._smart_process,
        )
        self._keyed(
            "structure.departments",
            current.structure.departments,
            desired.structure.departments,
            self._department,
        )
        return self.plan

    # --- generic -----------------------------------------------------------

    def _keyed(self, address: str, current: list, desired: list, compare) -> None:
        """Match two lists of keyed objects; ``compare(addr, cur | None, des)``."""
        current_by_key = {item.key: item for item in current}
        desired_keys = {item.key for item in desired}
        for item in desired:
            compare(f"{address}.{item.key}", current_by_key.get(item.key), item)
        collection = address.rsplit(".", 1)[-1]
        for item in current:
            if item.key not in desired_keys:
                self.plan.changes.append(
                    Change(
                        Action.delete,
                        f"{address}.{item.key}",
                        note=_DELETE_NOTES.get(collection, "")
                        or _DELETE_NOTES.get(address.split(".")[1], ""),
                    )
                )

    def _attributes(self, current: Any, desired: Any, names: list[str]) -> dict:
        changed = {}
        for name in names:
            des = getattr(desired, name)
            if current is None:
                if not (des is None or des is False or des == [] or des == {}):
                    changed[name] = (None, des)
                continue
            cur = getattr(current, name)
            if des is None and name in _OPTIONAL:
                continue
            if cur != des:
                changed[name] = (cur, des)
        return changed

    def _record(self, address: str, current: Any, attributes: dict, note: str = "") -> None:
        if current is None:
            self.plan.changes.append(Change(Action.create, address, attributes, note))
        elif attributes:
            self.plan.changes.append(Change(Action.update, address, attributes, note))

    # --- per object type ------------------------------------------------------

    def _category(self, address: str, current: Category | None, desired: Category) -> None:
        attrs = self._attributes(current, desired, ["name", "default", "sort"])
        self._record(address, current, attrs)
        self._keyed(
            f"{address}.stages", current.stages if current else [], desired.stages, self._stage
        )

    def _stage(self, address: str, current: Stage | None, desired: Stage) -> None:
        attrs = self._attributes(current, desired, ["name", "semantic", "sort", "color"])
        for name, (cur, des) in list(attrs.items()):
            # Enums print better as plain strings.
            attrs[name] = (getattr(cur, "value", cur), getattr(des, "value", des))
        self._record(address, current, attrs)

    def _field(self, address: str, current: Field | None, desired: Field) -> None:
        names = ["label", "type", "bitrix_type", "multiple", "required", "sort"]
        attrs = self._attributes(current, desired, names)
        for name, (cur, des) in list(attrs.items()):
            attrs[name] = (getattr(cur, "value", cur), getattr(des, "value", des))

        cur_settings = strip_defaults(current.type, current.settings) if current else {}
        des_settings = strip_defaults(desired.type, desired.settings)
        if cur_settings != des_settings and (current is not None or des_settings):
            attrs["settings"] = (cur_settings if current else None, des_settings)

        immutable = [n for n in ("type", "bitrix_type", "multiple") if n in attrs]
        if current is not None and immutable:
            self.plan.changes.append(
                Change(
                    Action.replace,
                    address,
                    attrs,
                    note=f"Bitrix24 cannot change {', '.join(immutable)} of an existing field: "
                    "it will be deleted and created again, its data will be lost",
                )
            )
            # The new field gets all values from scratch; no per-value diff needed.
            return
        self._record(address, current, attrs)
        self._keyed(
            f"{address}.values", current.values if current else [], desired.values, self._value
        )

    def _value(self, address: str, current: EnumValue | None, desired: EnumValue) -> None:
        attrs = self._attributes(current, desired, ["value", "default", "sort"])
        self._record(address, current, attrs)

    def _smart_process(
        self, address: str, current: SmartProcess | None, desired: SmartProcess
    ) -> None:
        names = ["title", "code", "categories_enabled", "stages_enabled", "client_enabled"]
        self._record(address, current, self._attributes(current, desired, names))
        self._keyed(
            f"{address}.categories",
            current.categories if current else [],
            desired.categories,
            self._category,
        )

    def _department(self, address: str, current: Department | None, desired: Department) -> None:
        attrs = self._attributes(current, desired, ["name", "parent", "sort"])
        self._record(address, current, attrs)
