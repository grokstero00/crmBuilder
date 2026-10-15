from __future__ import annotations

from pathlib import Path

import yaml

from crmbuilder.spec.models import PortalSpec


def spec_from_yaml(text: str) -> PortalSpec:
    return PortalSpec.model_validate(yaml.safe_load(text) or {})


def spec_to_yaml(spec: PortalSpec) -> str:
    data = spec.model_dump(mode="json", exclude_defaults=True, exclude={"version"})
    data = {"version": spec.version, **data}
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


def load_spec(path: str | Path) -> PortalSpec:
    return spec_from_yaml(Path(path).read_text(encoding="utf-8"))


def dump_spec(spec: PortalSpec, path: str | Path) -> None:
    Path(path).write_text(spec_to_yaml(spec), encoding="utf-8")
