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
        ("rfi_zapros", "process"),  # renamed system stage keyed by its name
        ("zapisan", "process"),  # UC_ code replaced by the stage name
        ("won", "success"),
        ("lose", "failure"),
        ("apology", "failure"),
        ("strannaya", "process"),
    ]
    assert stages[0].color == "#39A8EF"
    assert [s.semantic.value for s in cats[0].stages] == [
        "process",
        "success",
        "failure",
        "failure",  # APOLOGY reported as "apology"
    ]
    assert "C1:UC_Q1W2E3: unknown stage semantics 'weird', treated as process" in result.warnings
    assert not any("APOLOGY" in w for w in result.warnings)

    state = result.state.resources
    assert state["crm.deal_categories.pervichnyy_priem"] == {"id": 1}
    assert state["crm.deal_categories.pervichnyy_priem.stages.zapisan"]["status_id"] == (
        "C1:UC_X7K2P1"
    )


def test_import_fields(portal: FakePortal):
    result = import_portal(portal.client())
    fields = {f.key: f for f in result.spec.crm.fields["deal"]}
    assert set(fields) == {
        "doctor",
        "istochnik_zapisi",
        "logo",
        "bron_kabineta",
        "patsient",
        "staraya_svyaz",
    }

    doctor = fields["doctor"]
    assert (doctor.type, doctor.required, doctor.label) == (FieldType.employee, True, "Врач")

    source = fields["istochnik_zapisi"]
    assert [(v.key, v.value, v.default) for v in source.values] == [
        ("sayt", "Сайт", True),  # md5 XML_ID replaced by the value
        ("phone", "Звонок", False),
    ]
    assert source.settings == {}  # DISPLAY=LIST, LIST_HEIGHT=1 are Bitrix defaults

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


def test_crm_links_to_smart_processes_use_keys(portal: FakePortal):
    result = import_portal(portal.client())
    fields = {f.key: f for f in result.spec.crm.fields["deal"]}
    assert fields["patsient"].settings == {"CONTACT": "Y", "SMART_PROCESS:plan_lecheniya": "Y"}
    assert fields["staraya_svyaz"].settings == {"DYNAMIC_9999": "Y"}
    assert any("unknown smart process DYNAMIC_9999" in w for w in result.warnings)


def test_default_settings_are_dropped(portal: FakePortal):
    fields = {f.key: f for f in import_portal(portal.client()).spec.crm.fields["deal"]}
    assert fields["logo"].settings == {"MAX_ALLOWED_SIZE": 1048576}


def test_reimport_keeps_keys_from_previous_state(portal: FakePortal):
    first = import_portal(portal.client())
    # Pretend the user renamed things in the spec before the portal changed.
    first.state.resources["crm.deal_categories.primary"] = first.state.resources.pop(
        "crm.deal_categories.pervichnyy_priem"
    )
    for address in list(first.state.resources):
        if address.startswith("crm.deal_categories.pervichnyy_priem."):
            new = address.replace("pervichnyy_priem", "primary", 1)
            first.state.resources[new] = first.state.resources.pop(address)
    first.state.resources["crm.fields.deal.source"] = first.state.resources.pop(
        "crm.fields.deal.istochnik_zapisi"
    )

    # Someone renames the category on the portal and adds a field whose name
    # would naturally produce the key "source".
    def categories(params):
        data = {
            2: [
                {"id": 0, "name": "Общая", "sort": 100, "isDefault": "Y"},
                {"id": 1, "name": "Приём (новое имя)", "sort": 200, "isDefault": "N"},
            ]
        }
        return {"result": {"categories": data.get(params["entityTypeId"], [])}}

    portal.on("crm.category.list", categories)
    from .conftest import DEAL_FIELDS, paginate

    extra = {"ID": "99", "FIELD_NAME": "UF_CRM_SOURCE", "USER_TYPE_ID": "string",
             "EDIT_FORM_LABEL": "Новый источник"}  # fmt: skip
    portal.on("crm.deal.userfield.list", lambda p: paginate([*DEAL_FIELDS, extra], p))

    second = import_portal(portal.client(), previous_state=first.state)
    assert [c.key for c in second.spec.crm.deal_categories] == ["obshchaya", "primary"]
    assert second.spec.crm.deal_categories[1].name == "Приём (новое имя)"
    stage_keys = [s.key for s in second.spec.crm.deal_categories[1].stages]
    assert stage_keys[:3] == ["new", "rfi_zapros", "zapisan"]
    fields = {f.key: f for f in second.spec.crm.fields["deal"]}
    assert fields["source"].label == "Источник записи"  # reserved key kept
    assert fields["source_2"].label == "Новый источник"  # newcomer does not steal it
