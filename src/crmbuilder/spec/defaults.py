"""Bitrix24 user field settings that are equivalent to leaving the setting out.

Only values Bitrix itself falls back to when a setting is absent are listed, so
dropping them never changes how a field behaves. Anything else is kept.
"""

from __future__ import annotations

from typing import Any

from crmbuilder.spec.models import FieldType

_NO_DEFAULT_DATE = {"TYPE": "NONE", "VALUE": ""}

FIELD_SETTING_DEFAULTS: dict[FieldType, dict[str, Any]] = {
    FieldType.string: {"SIZE": 20, "ROWS": 1, "MIN_LENGTH": 0, "MAX_LENGTH": 0},
    FieldType.integer: {"SIZE": 20, "MIN_VALUE": 0, "MAX_VALUE": 0},
    FieldType.double: {"SIZE": 20, "MIN_VALUE": 0, "MAX_VALUE": 0},
    FieldType.file: {
        "SIZE": 20,
        "LIST_WIDTH": 0,
        "LIST_HEIGHT": 0,
        "MAX_SHOW_SIZE": 0,
        "MAX_ALLOWED_SIZE": 0,
        "TARGET_BLANK": "Y",
    },
    FieldType.enumeration: {"DISPLAY": "LIST", "LIST_HEIGHT": 1, "SHOW_NO_VALUE": "Y"},
    FieldType.date: {"DEFAULT_VALUE": _NO_DEFAULT_DATE},
    FieldType.datetime: {"DEFAULT_VALUE": _NO_DEFAULT_DATE},
}


def _same(a: Any, b: Any) -> bool:
    # Bitrix returns numbers as strings or ints depending on the method.
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_same(a[k], b[k]) for k in a)
    if isinstance(a, dict) or isinstance(b, dict):
        return False
    return str(a) == str(b)


def strip_defaults(field_type: FieldType, settings: dict[str, Any]) -> dict[str, Any]:
    defaults = FIELD_SETTING_DEFAULTS.get(field_type, {})
    return {
        k: v
        for k, v in settings.items()
        if v is not None
        and v != ""
        and v != []
        and v != {}
        and not (k in defaults and _same(v, defaults[k]))
    }
