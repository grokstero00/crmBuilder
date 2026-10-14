import pytest
from pydantic import ValidationError

from crmbuilder.spec import PortalSpec, dump_spec, load_spec


def _spec(**crm) -> dict:
    return {"portal": "x.bitrix24.ru", "crm": crm}


def test_round_trip(tmp_path):
    spec = PortalSpec.model_validate(
        _spec(
            deal_categories=[
                {"key": "sales", "name": "Продажи", "default": True,
                 "stages": [{"key": "new", "name": "Новая"},
                            {"key": "won", "name": "Успех", "semantic": "success"}]}
            ],
            fields={"deal": [{"key": "source", "label": "Источник", "type": "enumeration",
                              "values": [{"key": "site", "value": "Сайт"}]}]},
        )
    )  # fmt: skip
    path = tmp_path / "portal.yaml"
    dump_spec(spec, path)
    text = path.read_text(encoding="utf-8")
    assert text.startswith("version: 1\n")
    assert "Продажи" in text  # unicode is not escaped
    assert load_spec(path) == spec


@pytest.mark.parametrize(
    "crm, message",
    [
        ({"deal_categories": [{"key": "a", "name": "A"}, {"key": "a", "name": "B"}]},
         "duplicate deal category"),
        ({"deal_categories": [{"key": "a", "name": "A", "default": True},
                              {"key": "b", "name": "B", "default": True}]},
         "only one deal category"),
        ({"deal_categories": [{"key": "Bad Key", "name": "A"}]}, "should match pattern"),
        ({"fields": {"deal": [{"key": "f", "label": "F", "type": "string",
                               "values": [{"key": "v", "value": "V"}]}]}},
         "only allowed for enumeration"),
        ({"fields": {"deal": [{"key": "f", "label": "F", "type": "other"}]}},
         "requires bitrix_type"),
        ({"fields": {"invoice": []}}, "Input should be"),
        ({"pipelines": []}, "Extra inputs"),
    ],
)  # fmt: skip
def test_invalid_specs_are_rejected(crm, message):
    with pytest.raises(ValidationError, match=message):
        PortalSpec.model_validate(_spec(**crm))


def test_unknown_department_parent_is_rejected():
    with pytest.raises(ValidationError, match="unknown parent"):
        PortalSpec.model_validate(
            {"portal": "x", "structure": {"departments": [{"key": "a", "name": "A",
                                                           "parent": "zzz"}]}}
        )  # fmt: skip


def test_unsupported_version_is_rejected():
    with pytest.raises(ValidationError, match="unsupported spec version"):
        PortalSpec.model_validate({"version": 99, "portal": "x"})
