import json

from crmbuilder.plan import Action, diff_specs
from crmbuilder.spec import PortalSpec


def spec(**crm) -> PortalSpec:
    structure = crm.pop("structure", {})
    return PortalSpec.model_validate(
        {"portal": "x.bitrix24.ru", "crm": crm, "structure": structure}
    )


BASE = {
    "deal_categories": [
        {"key": "sales", "name": "Продажи", "default": True, "sort": 100,
         "stages": [
             {"key": "new", "name": "Новая", "sort": 10},
             {"key": "invoice", "name": "Cчёт", "sort": 20},
             {"key": "won", "name": "Успех", "semantic": "success", "sort": 30},
         ]},
    ],
    "fields": {"deal": [
        {"key": "source", "label": "Источник", "type": "enumeration",
         "values": [{"key": "site", "value": "Сайт"}, {"key": "call", "value": "Звонок"}]},
        {"key": "printer", "label": "Тип принтера", "type": "string"},
    ]},
}  # fmt: skip


def changes(plan):
    return [(c.action.value, c.address) for c in plan.changes]


def test_identical_specs_have_no_changes():
    plan = diff_specs(spec(**BASE), spec(**BASE))
    assert plan.changes == []
    assert "no changes" in plan.render()


def test_update_create_and_delete():
    desired = json.loads(json.dumps(BASE))
    stages = desired["deal_categories"][0]["stages"]
    stages[1]["name"] = "Счёт"  # fix the Latin C
    stages.insert(2, {"key": "contract", "name": "Договор", "sort": 25})
    desired["fields"]["deal"].pop()  # drop "printer"
    desired["fields"]["deal"][0]["values"].append({"key": "expo", "value": "Выставка"})

    plan = diff_specs(spec(**BASE), spec(**desired))
    assert changes(plan) == [
        ("update", "crm.deal_categories.sales.stages.invoice"),
        ("create", "crm.deal_categories.sales.stages.contract"),
        ("create", "crm.fields.deal.source.values.expo"),
        ("delete", "crm.fields.deal.printer"),
    ]
    assert plan.changes[0].attributes == {"name": ("Cчёт", "Счёт")}
    assert plan.changes[1].attributes == {
        "name": (None, "Договор"),
        "semantic": (None, "process"),
        "sort": (None, 25),
    }
    assert plan.changes[3].destructive
    assert "data stored in this field will be lost" in plan.changes[3].note


def test_new_category_lists_its_stages():
    desired = json.loads(json.dumps(BASE))
    desired["deal_categories"].append(
        {"key": "service", "name": "Сервис",
         "stages": [{"key": "new", "name": "Заявка"}, {"key": "won", "name": "Готово",
                                                       "semantic": "success"}]}
    )  # fmt: skip
    plan = diff_specs(spec(**BASE), spec(**desired))
    assert changes(plan) == [
        ("create", "crm.deal_categories.service"),
        ("create", "crm.deal_categories.service.stages.new"),
        ("create", "crm.deal_categories.service.stages.won"),
    ]
    assert plan.changes[0].attributes == {"name": (None, "Сервис")}  # no "default: false"
    assert plan.changes[2].attributes["semantic"] == (None, "success")


def test_deleted_category_is_one_destructive_change():
    desired = json.loads(json.dumps(BASE))
    desired["deal_categories"] = []
    plan = diff_specs(spec(**BASE), spec(**desired))
    assert changes(plan) == [("delete", "crm.deal_categories.sales")]
    assert "deals in this pipeline" in plan.changes[0].note


def test_field_type_change_is_a_replace():
    desired = json.loads(json.dumps(BASE))
    desired["fields"]["deal"][1].update(type="enumeration", values=[{"key": "a", "value": "A"}])
    plan = diff_specs(spec(**BASE), spec(**desired))
    (change,) = plan.changes
    assert change.action is Action.replace
    assert change.attributes["type"] == ("string", "enumeration")
    assert "cannot change type" in change.note


def test_omitted_optional_attributes_are_not_changes():
    desired = json.loads(json.dumps(BASE))
    for stage in desired["deal_categories"][0]["stages"]:
        stage.pop("sort")
    desired["deal_categories"][0].pop("sort")
    assert diff_specs(spec(**BASE), spec(**desired)).changes == []


def test_default_settings_do_not_count_as_changes():
    current = json.loads(json.dumps(BASE))
    desired = json.loads(json.dumps(BASE))
    desired["fields"]["deal"][1]["settings"] = {"SIZE": 20, "ROWS": 1}
    assert diff_specs(spec(**current), spec(**desired)).changes == []
    desired["fields"]["deal"][1]["settings"] = {"ROWS": 5}
    (change,) = diff_specs(spec(**current), spec(**desired)).changes
    assert change.attributes == {"settings": ({}, {"ROWS": 5})}


def test_departments_and_smart_processes():
    current = spec(
        smart_processes=[{"key": "orders", "title": "Заказы"}],
        structure={"departments": [{"key": "hq", "name": "HQ"},
                                   {"key": "sales", "name": "Продажи", "parent": "hq"}]},
    )  # fmt: skip
    desired = spec(
        smart_processes=[{"key": "orders", "title": "Заказы", "stages_enabled": True}],
        structure={"departments": [{"key": "hq", "name": "HQ"}]},
    )
    plan = diff_specs(current, desired)
    assert changes(plan) == [
        ("update", "crm.smart_processes.orders"),
        ("delete", "structure.departments.sales"),
    ]
    assert "employees" in plan.changes[1].note


def test_render_and_json():
    desired = json.loads(json.dumps(BASE))
    desired["deal_categories"][0]["stages"][1]["name"] = "Счёт"
    desired["fields"]["deal"].pop()
    plan = diff_specs(spec(**BASE), spec(**desired))

    text = plan.render()
    assert "~ update  crm.deal_categories.sales.stages.invoice" in text
    assert 'name: "Cчёт" -> "Счёт"' in text
    assert "- delete  crm.fields.deal.printer" in text
    assert "Plan: 0 to create, 1 to update, 1 to delete." in text
    assert "1 destructive change(s)" in text

    data = json.loads(plan.to_json())
    assert data["summary"] == {"create": 0, "update": 1, "delete": 1, "replace": 0}
    assert data["changes"][1]["destructive"] is True
