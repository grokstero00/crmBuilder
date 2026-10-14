from crmbuilder.importer import import_portal
from crmbuilder.spec import FieldType, PortalSpec

from .conftest import FakePortal


def test_import_deal_categories_and_stages(portal: FakePortal):
    result = import_portal(portal.client())
    cats = result.spec.crm.deal_categories
    assert [c.key for c in cats] == ["obshchaya", "pervichnyy_priem"]  # ordered by sort
    assert cats[0].default and not cats[1].default

    stages = cats[1].stages
    assert [(s.key, s.semantic.value) for s in stages] == [
        ("new", "process"),
        ("zapisan", "process"),  # UC_ code replaced by the stage name
        ("won", "success"),
        ("lose", "failure"),
    ]
    assert stages[0].color == "#39A8EF"
    assert [s.semantic.value for s in cats[0].stages] == ["process", "success", "failure"]

    state = result.state.resources
    assert state["crm.deal_categories.pervichnyy_priem"] == {"id": 1}
    assert state["crm.deal_categories.pervichnyy_priem.stages.zapisan"]["status_id"] == (
        "C1:UC_X7K2P1"
    )


def test_import_fields(portal: FakePortal):
    result = import_portal(portal.client())
    fields = {f.key: f for f in result.spec.crm.fields["deal"]}
    assert set(fields) == {"doctor", "istochnik_zapisi", "bron_kabineta"}

    doctor = fields["doctor"]
    assert (doctor.type, doctor.required, doctor.label) == (FieldType.employee, True, "Врач")

    source = fields["istochnik_zapisi"]
    assert [(v.key, v.value, v.default) for v in source.values] == [
        ("sayt", "Сайт", True),  # md5 XML_ID replaced by the value
        ("phone", "Звонок", False),
    ]
    assert source.settings == {"DISPLAY": "LIST", "LIST_HEIGHT": 1}

    booking = fields["bron_kabineta"]
    assert (booking.type, booking.bitrix_type) == (FieldType.other, "resourcebooking")

    state = result.state.resources
    assert state["crm.fields.deal.istochnik_zapisi"] == {
        "id": 58,
        "field_name": "UF_CRM_1712345678",
    }
    assert state["crm.fields.deal.istochnik_zapisi.values.phone"] == {"id": 2}
    assert any("resourcebooking" in w for w in result.warnings)
    assert any("crm.lead.userfield.list: skipped" in w for w in result.warnings)


def test_import_smart_processes(portal: FakePortal):
    result = import_portal(portal.client())
    (sp,) = result.spec.crm.smart_processes
    assert (sp.key, sp.stages_enabled, sp.client_enabled) == ("plan_lecheniya", True, True)
    (cat,) = sp.categories
    assert [(s.key, s.semantic.value) for s in cat.stages] == [
        ("new", "process"),
        ("success", "success"),
    ]
    assert result.state.resources["crm.smart_processes.plan_lecheniya"] == {
        "id": 3,
        "entity_type_id": 1036,
    }


def test_import_departments(portal: FakePortal):
    result = import_portal(portal.client())
    deps = {d.key: d for d in result.spec.structure.departments}
    assert deps["klinika"].parent is None
    assert deps["registratura"].parent == "klinika"
    assert result.state.resources["structure.departments.vrachi"] == {"id": 5}


def test_imported_spec_is_valid(portal: FakePortal):
    spec = import_portal(portal.client()).spec
    assert PortalSpec.model_validate(spec.model_dump(mode="json")) == spec
