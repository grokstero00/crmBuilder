import pytest
from pydantic import ValidationError

from crmbuilder import automation, cli, templates
from crmbuilder.apply import Applier
from crmbuilder.design import EditError, SpecEditor
from crmbuilder.importer import import_portal
from crmbuilder.plan import Action, diff_specs
from crmbuilder.report import render
from crmbuilder.spec import AutomationRule, PortalSpec, dump_spec, load_spec

from .fake_bitrix import seeded_portal

DEAL_NEW = "crm.deal_categories.obshchaya.stages.new"
SP_NEW = "crm.smart_processes.zakazy.categories.obshchaya_voronka.stages.new"


def _spec(**changes) -> PortalSpec:
    spec = import_portal(seeded_portal().client()).spec
    return spec.model_copy(update=changes)


def _rule(**kw) -> dict:
    return {"key": "call_back", "stage": DEAL_NEW, "action": "task", "title": "Перезвонить", **kw}


def _with_rules(spec: PortalSpec, *rules: dict) -> PortalSpec:
    data = spec.model_dump(mode="json")
    data["automation"] = list(rules)
    return PortalSpec.model_validate(data)


def test_rule_kind_needs_matching_type():
    with pytest.raises(ValidationError, match="set action, not event"):
        AutomationRule.model_validate(_rule(action=None, event="webform"))
    with pytest.raises(ValidationError, match="set event, not action"):
        AutomationRule.model_validate(_rule(kind="trigger"))
    rule = AutomationRule.model_validate(_rule(kind="trigger", action=None, event="webform"))
    assert rule.event == "webform"


def test_rule_stage_must_exist():
    spec = _spec()
    assert _with_rules(spec, _rule()).automation[0].stage == DEAL_NEW
    with pytest.raises(ValidationError, match="stage 'crm.deal_categories.obshchaya.stages.x'"):
        _with_rules(spec, _rule(stage="crm.deal_categories.obshchaya.stages.x"))
    with pytest.raises(ValidationError, match="needs automation_enabled"):
        _with_rules(spec, _rule(stage=SP_NEW))
    with pytest.raises(ValidationError, match="duplicate automation rule"):
        _with_rules(spec, _rule(), _rule())


def test_automation_enabled_is_imported_planned_and_applied():
    fake = seeded_portal()
    before = import_portal(fake.client())
    assert before.spec.crm.smart_processes[0].automation_enabled is False
    data = before.spec.model_dump(mode="json")
    data["crm"]["smart_processes"][0]["automation_enabled"] = True
    data["automation"] = [_rule(stage=SP_NEW)]
    desired = PortalSpec.model_validate(data)

    plan = diff_specs(before.spec, desired)
    # Only the switch is a portal change: the rule itself is never applied.
    assert [(c.action, c.address) for c in plan.changes] == [
        (Action.update, "crm.smart_processes.zakazy")
    ]
    applier = Applier(fake.writable_client(), before.spec, desired, before.state)
    assert applier.unsupported(plan) == []
    assert applier.apply(plan).ok
    assert fake.writes[-1][1]["fields"] == {"isAutomationEnabled": "Y"}
    after = import_portal(fake.client(), previous_state=applier.state)
    assert after.spec.crm.smart_processes[0].automation_enabled is True
    assert diff_specs(after.spec, desired).changes == []


def test_carry_over_drops_rules_of_missing_stages():
    spec = _spec()
    servis = "crm.deal_categories.servis.stages.new"
    old = _with_rules(spec, _rule(), _rule(key="other", stage=servis))
    data = spec.model_dump(mode="json")
    data["crm"]["deal_categories"] = data["crm"]["deal_categories"][:1]
    new = PortalSpec.model_validate(data)
    merged, warnings = automation.carry_over(new, old)
    assert [r.key for r in merged.automation] == ["call_back"]
    assert len(warnings) == 1 and "'other' dropped" in warnings[0]
    assert automation.carry_over(new, None) == (new, [])


def test_pending_reminder():
    spec = _with_rules(_spec(), _rule(), _rule(key="done_one", done=True))
    text = automation.render_pending(spec)
    assert "1 robot/trigger rule(s)" in text
    assert "call_back: Перезвонить" in text and "done_one" not in text
    assert automation.render_pending(_spec()) == ""


def test_instructions_follow_stage_order():
    spec = _with_rules(
        _spec(),
        _rule(key="late", stage="crm.deal_categories.obshchaya.stages.won", action="sms",
              title="Спасибо за покупку", when="через 1 день", details="Спасибо!"),
        _rule(key="form", kind="trigger", action=None, event="webform",
              title="Заявка с сайта", done=True),
    )  # fmt: skip
    text = "\n".join(automation.render_instructions(spec))
    assert "### Сделки, воронка «Общая»" in text
    assert "выберите воронку «Общая»" in text  # the portal has two deal pipelines
    first = text.index("Стадия «Новая» — триггер «Заполнение CRM-формы»: Заявка с сайта")
    second = text.index("⬜ Стадия «Сделка успешна» — робот «Отправить SMS клиенту»")
    assert first < second
    assert "✅ Стадия «Новая»" in text
    assert "   - Когда: через 1 день" in text and "   - Настройки: Спасибо!" in text
    assert automation.render_instructions(_spec()) == []

    report = render(spec, changes=None)
    assert "## Роботы и триггеры" in report
    assert report.index("## Роботы и триггеры") < report.index("## Структура CRM")


def test_smart_process_instructions():
    data = _spec().model_dump(mode="json")
    data["crm"]["smart_processes"][0]["automation_enabled"] = True
    data["automation"] = [_rule(stage=SP_NEW)]
    text = "\n".join(automation.render_instructions(PortalSpec.model_validate(data)))
    assert "### Смарт-процесс «Заказы»" in text
    assert "CRM → Смарт-процессы → «Заказы» и нажмите «Роботы»" in text


def test_editor_creates_rules_and_protects_their_stages():
    editor = SpecEditor(_spec())
    editor.create("automation.remind", {"stage": SP_NEW, "action": "notify", "title": "Сообщить"})
    spec = editor.spec
    assert spec.automation[0].key == "remind"
    assert spec.crm.smart_processes[0].automation_enabled is True  # switched on for the rule

    with pytest.raises(EditError, match="automation rule 'remind'"):
        editor.delete(SP_NEW)
    with pytest.raises(EditError, match="set action, not event"):
        editor.create("automation.bad", {"stage": DEAL_NEW, "title": "x"})
    editor.update("automation.remind", {"done": True})
    assert editor.spec.automation[0].done is True
    editor.delete("automation.remind")
    assert editor.spec.automation == []


def test_clinic_template_brings_robots():
    spec = _spec()
    merged, report = templates.merge(spec, "clinic")
    assert "automation.napominanie_o_prieme" in report.added
    assert {r.key for r in merged.automation} >= {"zvonok_novoy_zayavke", "forma_s_sayta"}

    # The spec already has a pipeline with this key but other stages: rules are skipped.
    data = spec.model_dump(mode="json")
    data["crm"]["deal_categories"].append(
        {"key": "zapis_na_priem", "name": "Запись", "stages": [{"key": "new", "name": "Новая"}]}
    )
    merged, report = templates.merge(PortalSpec.model_validate(data), "clinic")
    assert "automation.napominanie_o_prieme" in report.skipped
    assert "automation.zvonok_novoy_zayavke" in report.added


def test_cli_keeps_rules_on_reimport_and_reminds(tmp_path, monkeypatch, capsys):
    from crmbuilder.bitrix import BitrixClient

    fake = seeded_portal()
    monkeypatch.setattr(
        cli,
        "BitrixClient",
        lambda url, **kw: BitrixClient(url, transport=fake.transport(), sleep=lambda _: None),
    )
    hook = ["--webhook", "https://test.bitrix24.ru/rest/1/x/"]
    spec_path = tmp_path / "portal.yaml"
    assert cli.main(["import", *hook, "-o", str(spec_path)]) == 0
    dump_spec(_with_rules(load_spec(spec_path), _rule()), spec_path)
    capsys.readouterr()

    assert cli.main(["plan", str(spec_path), *hook]) == 0
    out = capsys.readouterr().out
    assert "no changes" in out
    assert "Manual: 1 robot/trigger rule(s)" in out

    assert cli.main(["import", *hook, "-o", str(spec_path)]) == 0
    assert "automation:      1 rule(s) kept" in capsys.readouterr().out
    assert [r.key for r in load_spec(spec_path).automation] == ["call_back"]
