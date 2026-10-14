import pytest

from crmbuilder import cli, templates
from crmbuilder.apply import Applier
from crmbuilder.importer import import_portal
from crmbuilder.plan import Action, diff_specs
from crmbuilder.spec import load_spec

from .fake_bitrix import seeded_portal


def test_templates_are_listed_and_valid():
    names = templates.available()
    assert "clinic" in names and names["clinic"]
    spec = import_portal(seeded_portal().client()).spec
    for name in names:
        templates.merge(spec, name)  # every shipped template merges into a real spec


def test_merge_only_adds():
    spec = import_portal(seeded_portal().client()).spec
    merged, report = templates.merge(spec, "clinic")
    assert "crm.deal_categories.zapis_na_priem" in report.added
    assert "structure.departments.terapevty" in report.added
    plan = diff_specs(spec, merged)
    assert {c.action for c in plan.changes} == {Action.create}
    deps = {d.key: d.parent for d in merged.structure.departments}
    assert deps["registratura"] == "kompaniya"  # "@root" -> top department
    assert deps["terapevty"] == "vrachi"

    again, report2 = templates.merge(merged, "clinic")
    assert report2.added == [] and again == merged


def test_unknown_template():
    spec = import_portal(seeded_portal().client()).spec
    with pytest.raises(ValueError, match="available: clinic"):
        templates.merge(spec, "nope")


def test_clinic_template_applies_cleanly():
    fake = seeded_portal()
    before = import_portal(fake.client())
    desired, _ = templates.merge(before.spec, "clinic")
    plan = diff_specs(before.spec, desired)
    applier = Applier(fake.writable_client(), before.spec, desired, before.state)
    assert applier.unsupported(plan) == []
    assert not plan.destructive
    result = applier.apply(plan)
    assert result.ok, result.error
    after = import_portal(fake.client(), previous_state=applier.state)
    assert diff_specs(after.spec, desired).changes == []
    assert {c["name"] for c in fake.categories[2]} >= {"Запись на приём", "Лечение"}
    assert {d["NAME"] for d in fake.departments} >= {"Терапевты", "Стоматологи"}


def test_cli_template_commands(tmp_path, monkeypatch, capsys):
    from crmbuilder.bitrix import BitrixClient

    fake = seeded_portal()
    monkeypatch.setattr(
        cli,
        "BitrixClient",
        lambda url, **kw: BitrixClient(url, transport=fake.transport(), sleep=lambda _: None),
    )
    spec_path = tmp_path / "portal.yaml"
    assert cli.main(["import", "--webhook", "https://test.bitrix24.ru/rest/1/x/",
                     "-o", str(spec_path)]) == 0  # fmt: skip
    assert cli.main(["template", "list"]) == 0
    assert "clinic" in capsys.readouterr().out
    assert cli.main(["template", "add", "clinic", str(spec_path)]) == 0
    out = capsys.readouterr().out
    assert "+ crm.deal_categories.lechenie" in out
    assert "nothing has changed on the portal" in out
    assert fake.writes == []
    assert any(c.key == "lechenie" for c in load_spec(spec_path).crm.deal_categories)
