"""Ready-made configurations merged into a portal spec (``crmbuilder template add``).

A template uses the spec format without ``portal``/``version`` plus a
``description``. In departments, ``parent: "@root"`` means the portal's top
department. Merging only adds objects whose keys are new; anything already in
the spec is left untouched, so a template never removes or rewrites settings.
Automation rules point at stages by address, so they work when the template's
pipelines are added (or the spec already has the same keys).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from importlib import resources
from typing import Any

import yaml

from crmbuilder.spec.models import PortalSpec, automation_target_problem

ROOT_PARENT = "@root"


@dataclass
class MergeReport:
    added: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)


def _files():
    return resources.files(__package__)


def available() -> dict[str, str]:
    """Template name -> description."""
    result = {}
    for entry in sorted(_files().iterdir(), key=lambda e: e.name):
        if entry.name.endswith(".yaml"):
            data = yaml.safe_load(entry.read_text(encoding="utf-8")) or {}
            result[entry.name.removesuffix(".yaml")] = data.get("description", "")
    return result


def load(name: str) -> dict[str, Any]:
    entry = _files() / f"{name}.yaml"
    if not entry.is_file():
        names = ", ".join(available()) or "none"
        raise ValueError(f"unknown template {name!r}; available: {names}")
    return yaml.safe_load(entry.read_text(encoding="utf-8")) or {}


def merge(spec: PortalSpec, name: str) -> tuple[PortalSpec, MergeReport]:
    template = load(name)
    data = spec.model_dump(mode="json")
    report = MergeReport()

    def add(items: list[dict], new: list[dict], address: str) -> None:
        keys = {item["key"] for item in items}
        for item in new:
            target = f"{address}.{item['key']}"
            if item["key"] in keys:
                report.skipped.append(target)
            else:
                items.append(item)
                report.added.append(target)

    crm = template.get("crm", {})
    add(data["crm"]["deal_categories"], crm.get("deal_categories", []), "crm.deal_categories")
    for entity, fields in crm.get("fields", {}).items():
        add(data["crm"]["fields"].setdefault(entity, []), fields, f"crm.fields.{entity}")

    departments = template.get("structure", {}).get("departments", [])
    if departments:
        existing = data["structure"]["departments"]
        root = next((d["key"] for d in existing if d.get("parent") is None), None)
        if root is None:
            raise ValueError(
                "the spec has no departments: run `crmbuilder import` first, the template "
                "attaches its departments to the portal's top department"
            )
        for dep in departments:
            if dep.get("parent") == ROOT_PARENT:
                dep["parent"] = root
        add(existing, departments, "structure.departments")

    # A rule is only added when its stage exists in the merged spec.
    merged = PortalSpec.model_validate(data)
    rules = []
    for rule in template.get("automation", []):
        if automation_target_problem(merged, rule.get("stage", "")):
            report.skipped.append(f"automation.{rule['key']}")
        else:
            rules.append(rule)
    add(data["automation"], rules, "automation")

    return PortalSpec.model_validate(data), report
