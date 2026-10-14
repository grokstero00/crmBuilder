from crmbuilder.spec import FieldType
from crmbuilder.spec.defaults import strip_defaults


def test_boolean_defaults_from_a_live_portal_are_dropped():
    # Exactly what Bitrix24 returned for a yes/no field created without settings.
    live = {"DEFAULT_VALUE": 0, "DISPLAY": "CHECKBOX", "LABEL": ["", ""]}
    assert strip_defaults(FieldType.boolean, live) == {}
    assert strip_defaults(FieldType.boolean, {"DEFAULT_VALUE": "1"}) == {"DEFAULT_VALUE": "1"}
    assert strip_defaults(FieldType.boolean, {"DISPLAY": "RADIO"}) == {"DISPLAY": "RADIO"}


def test_blank_containers_are_dropped():
    settings = {"LABEL": {"0": "", "1": ""}, "LIST": [], "X": [None, ""], "KEEP": ["", "y"]}
    assert strip_defaults(FieldType.string, settings) == {"KEEP": ["", "y"]}


def test_zero_is_not_blank():
    # 0 is meaningful unless it is the type's default.
    assert strip_defaults(FieldType.string, {"ROWS": 0}) == {"ROWS": 0}
    assert strip_defaults(FieldType.string, {"MAX_LENGTH": 0}) == {}
