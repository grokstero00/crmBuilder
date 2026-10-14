"""State file: maps spec addresses to remote Bitrix IDs.

Addresses are dotted paths mirroring the spec, e.g.::

    crm.deal_categories.primary_visit                 -> {"id": 1}
    crm.deal_categories.primary_visit.stages.new      -> {"status_id": "C1:NEW"}
    crm.fields.deal.doctor                            -> {"id": 57, "field_name": "UF_CRM_DOCTOR"}
    crm.fields.deal.visit_source.values.site          -> {"id": 12}
    crm.smart_processes.treatment_plan                -> {"id": 3, "entity_type_id": 1036}
    structure.departments.reception                   -> {"id": 4}
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field

STATE_VERSION = 1


class State(BaseModel):
    version: int = STATE_VERSION
    portal: str
    updated_at: str = Field(default_factory=lambda: datetime.now(UTC).isoformat(timespec="seconds"))
    resources: dict[str, dict[str, Any]] = Field(default_factory=dict)

    def set(self, address: str, **remote: Any) -> None:
        self.resources[address] = remote

    def get(self, address: str) -> dict[str, Any] | None:
        return self.resources.get(address)


def load_state(path: str | Path) -> State:
    return State.model_validate_json(Path(path).read_text(encoding="utf-8"))


def dump_state(state: State, path: str | Path) -> None:
    data = state.model_dump(mode="json")
    data["resources"] = dict(sorted(data["resources"].items()))
    text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
    Path(path).write_text(text, encoding="utf-8")
