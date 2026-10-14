from collections.abc import Callable

import pytest

from crmbuilder.apply import Applier
from crmbuilder.importer import import_portal
from crmbuilder.plan import Action, diff_specs
from crmbuilder.spec import (
    Category,
    Department,
    EnumValue,
    Field,
    FieldType,
    PortalSpec,
    Stage,
    StageSemantic,
)

from .fake_bitrix import FakeBitrix, seeded_portal


def prepare(fake: FakeBitrix, edit: Callable[[PortalSpec], None]):
    """Import, edit a copy of the spec, re-import against it and build the plan."""
    first = import_portal(fake.client())
    desired = first.spec.model_copy(deep=True)
    edit(desired)
    desired = PortalSpec.model_validate(desired.model_dump())  # re-run validation
    current = import_portal(fake.client(), previous_state=first.state)
    plan = diff_specs(current.spec, desired)
    journal: list[dict] = []
    applier = Applier(fake.writable_client(), current.spec, desired, current.state, journal.append)
    return applier, plan, desired, journal


def apply_and_verify(fake: FakeBitrix, edit: Callable[[PortalSpec], None]):
    applier, plan, desired, journal = prepare(fake, edit)
    assert applier.unsupported(plan) == []
    result = applier.apply(plan)
    assert result.ok, result.error
    after = import_portal(fake.client(), previous_state=applier.state)
    residual = diff_specs(after.spec, desired)
    assert residual.changes == [], residual.render()
    return plan, applier, journal


def deal_category(spec: PortalSpec, key: str) -> Category:
    return next(c for c in spec.crm.deal_categories if c.key == key)


def deal_field(spec: PortalSpec, key: str) -> Field:
    return next(f for f in spec.crm.fields["deal"] if f.key == key)


def test_stage_rename_create_and_delete():
    fake = seeded_portal()

    def edit(spec):
        stages = deal_category(spec, "obshchaya").stages
        stages[2].name = "Счёт"
        stages.insert(3, Stage(key="peregovory", name="Переговоры", sort=35))
        stages.insert(7, Stage(key="otkaz", name="Отказ", semantic=StageSemantic.failure, sort=75))
        spec.crm.deal_categories[0].stages = [s for s in stages if s.key != "finalnyy_schet"]

    plan, applier, journal = apply_and_verify(fake, edit)
    assert sorted((c.action.value, c.address.rsplit(".", 1)[-1]) for c in plan.changes) == [
        ("create", "otkaz"),
        ("create", "peregovory"),
        ("delete", "finalnyy_schet"),
        ("update", "schet_na_predoplatu"),
    ]
    added = [s for s in fake.statuses if s["NAME"] in ("Переговоры", "Отказ")]
    assert {(s["STATUS_ID"], s["SEMANTICS"]) for s in added} == {("PEREGOVORY", None),
                                                                 ("OTKAZ", "F")}  # fmt: skip
    # Deletes run after creates.
    methods = [e["method"] for e in journal]
    assert methods.index("crm.status.delete") > methods.index("crm.status.add")


def test_new_pipeline_reuses_default_stages():
    fake = seeded_portal()

    def edit(spec):
        spec.crm.deal_categories.append(
            Category(
                key="tender",
                name="Тендеры",
                sort=300,
                stages=[
                    Stage(key="new", name="Новый тендер", sort=10),
                    Stage(key="rfi", name="RFI", sort=20),
                    Stage(key="kp", name="КП отправлено", sort=30),
                    Stage(key="vyigrali", name="Выиграли", semantic=StageSemantic.success, sort=40),
                    Stage(key="proigrali", name="Проиграли", semantic=StageSemantic.failure,
                          sort=50),
                ],
            )
        )  # fmt: skip

    plan, applier, journal = apply_and_verify(fake, edit)
    (cat,) = [c for c in fake.categories[2] if c["name"] == "Тендеры"]
    stages = sorted(
        (s for s in fake.statuses if s["ENTITY_ID"] == f"DEAL_STAGE_{cat['id']}"),
        key=lambda s: int(s["SORT"]),
    )
    assert [(s["NAME"], s["SEMANTICS"]) for s in stages] == [
        ("Новый тендер", None),
        ("RFI", None),
        ("КП отправлено", None),
        ("Выиграли", "S"),
        ("Проиграли", "F"),
    ]
    # Default stages were renamed rather than recreated: no new stage was needed.
    assert [e["method"] for e in journal].count("crm.status.add") == 0
    assert applier.state.get("crm.deal_categories.tender.stages.vyigrali")["status_id"] == (
        f"C{cat['id']}:WON"
    )


def test_fields_and_values():
    fake = seeded_portal()

    def edit(spec):
        source = deal_field(spec, "source")
        source.label = "Источник обращения"
        source.values[0].value = "Сайт компании"
        source.values = [v for v in source.values if v.key != "call"]
        source.values.append(EnumValue(key="expo", value="Выставка", sort=30))
        spec.crm.fields["deal"] = [f for f in spec.crm.fields["deal"] if f.key != "printer"]
        spec.crm.fields["deal"].append(
            Field(
                key="industry",
                label="Отрасль",
                type=FieldType.enumeration,
                required=True,
                values=[
                    EnumValue(key="oil", value="Нефтегаз"),
                    EnumValue(key="mining", value="ГМК"),
                ],
            )
        )
        spec.crm.fields["deal"].append(
            Field(key="warranty", label="Гарантийный случай", type=FieldType.boolean)
        )
        spec.crm.fields["deal"].append(
            Field(key="order", label="Заказ", type=FieldType.crm,
                  settings={"SMART_PROCESS:zakazy": "Y", "COMPANY": "Y"})
        )  # fmt: skip

    apply_and_verify(fake, edit)
    deal_fields = {f["FIELD_NAME"]: f for f in fake.userfields["deal"]}
    assert set(deal_fields) == {
        "UF_CRM_SOURCE",
        "UF_CRM_INDUSTRY",
        "UF_CRM_WARRANTY",
        "UF_CRM_ORDER",
    }
    assert [v["VALUE"] for v in deal_fields["UF_CRM_SOURCE"]["LIST"]] == ["Сайт компании",
                                                                          "Выставка"]  # fmt: skip
    assert deal_fields["UF_CRM_INDUSTRY"]["MANDATORY"] == "Y"
    assert deal_fields["UF_CRM_ORDER"]["SETTINGS"] == {"DYNAMIC_1036": "Y", "COMPANY": "Y"}


def test_settings_change_and_reset_to_default():
    fake = seeded_portal()
    apply_and_verify(fake, lambda s: deal_field(s, "printer").settings.update(ROWS=5))
    assert fake.userfields["deal"][1]["SETTINGS"]["ROWS"] == 5
    apply_and_verify(fake, lambda s: deal_field(s, "printer").settings.clear())
    assert str(fake.userfields["deal"][1]["SETTINGS"]["ROWS"]) == "1"


def test_field_type_change_recreates_field():
    fake = seeded_portal()

    def edit(spec):
        printer = deal_field(spec, "printer")
        printer.type = FieldType.enumeration
        printer.settings = {}
        printer.values = [EnumValue(key="foto", value="Для фото")]

    plan, _, _ = apply_and_verify(fake, edit)
    assert [c.action for c in plan.changes] == [Action.replace]
    printer = next(f for f in fake.userfields["deal"] if f["FIELD_NAME"] == "UF_CRM_PRINTER")
    assert printer["USER_TYPE_ID"] == "enumeration" and printer["ID"] != "58"


def test_departments():
    fake = seeded_portal()

    def edit(spec):
        deps = spec.structure.departments
        deps.append(Department(key="servis", name="Сервис", parent="kompaniya"))
        deps.append(Department(key="vyezdnye", name="Выездные инженеры", parent="servis"))
        spec.structure.departments = [d for d in deps if d.key != "prodazhi"]

    apply_and_verify(fake, edit)
    names = {d["NAME"]: d for d in fake.departments}
    assert set(names) == {"Компания", "Сервис", "Выездные инженеры"}
    assert names["Выездные инженеры"]["PARENT"] == names["Сервис"]["ID"]


def test_smart_process_pipeline_stages():
    fake = seeded_portal()

    def edit(spec):
        (sp,) = spec.crm.smart_processes
        sp.categories[0].stages[1].name = "Проектирование"

    plan, _, _ = apply_and_verify(fake, edit)
    assert plan.changes[0].address == (
        "crm.smart_processes.zakazy.categories.obshchaya_voronka.stages.podgotovka"
    )
    assert any(s["NAME"] == "Проектирование" for s in fake.statuses)


@pytest.mark.parametrize(
    "edit, reason",
    [
        (lambda s: setattr(deal_category(s, "obshchaya").stages[1], "semantic",
                           StageSemantic.failure), "cannot move a stage to another group"),
        (lambda s: deal_category(s, "servis").stages.insert(
            0, Stage(key="won2", name="Ещё успех", semantic=StageSemantic.success)),
         "exactly one success stage"),
        (lambda s: s.crm.smart_processes.append(
            s.crm.smart_processes[0].model_copy(update={"key": "novyy", "title": "Новый"})),
         "smart processes themselves"),
        (lambda s: s.structure.departments.append(Department(key="root2", name="Root 2")),
         "needs a parent"),
    ],
)  # fmt: skip
def test_unsupported_changes_are_reported_before_writing(edit, reason):
    fake = seeded_portal()
    applier, plan, _, _ = prepare(fake, edit)
    problems = applier.unsupported(plan)
    assert any(reason in p.reason for p in problems), [p.reason for p in problems]
    assert fake.writes == []


def test_failure_stops_and_state_keeps_what_was_done():
    fake = seeded_portal()
    fake.busy_statuses.add("C1:EXECUTING")  # a deal sits in this stage

    def edit(spec):
        deal_category(spec, "servis").stages[0].name = "Заявка"
        servis = deal_category(spec, "servis")
        servis.stages = [s for s in servis.stages if s.key != "v_rabote"]
        spec.crm.fields["deal"] = [f for f in spec.crm.fields["deal"] if f.key != "printer"]

    applier, plan, _, journal = prepare(fake, edit)
    result = applier.apply(plan)
    assert not result.ok
    assert result.failed.address == "crm.deal_categories.servis.stages.v_rabote"
    assert "active items" in result.error
    assert [c.address.rsplit(".", 1)[-1] for c in result.done] == ["new"]
    # The field delete after the failure never ran.
    assert any(f["FIELD_NAME"] == "UF_CRM_PRINTER" for f in fake.userfields["deal"])
    assert "error" in journal[-1]
    assert applier.state.get("crm.deal_categories.servis.stages.v_rabote") is not None
