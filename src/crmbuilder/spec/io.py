from __future__ import annotations

from pathlib import Path

import yaml

from crmbuilder.spec.models import PortalSpec


def load_spec(path: str | Path) -> PortalSpec:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or {}
    return PortalSpec.model_validate(data)


def dump_spec(spec: PortalSpec, path: str | Path) -> None:
    data = spec.model_dump(mode="json", exclude_defaults=True, exclude={"version"})
    data = {"version": spec.version, **data}
    text = yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)
    Path(path).write_text(text, encoding="utf-8")
