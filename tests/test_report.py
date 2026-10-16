import json
from datetime import date
from types import SimpleNamespace

import pytest

from crmbuilder import cli
from crmbuilder.bitrix import BitrixClient
from crmbuilder.importer import import_portal
from crmbuilder.report import ai_manual_steps, describe_changes, render
from crmbuilder.spec import (
    Category,
    Department,
    EntityType,
    EnumValue,
    Field,
    FieldType,
    PortalSpec,
    Stage,
    StageSemantic,
    load_spec,
)

from .fake_bitrix import seeded_portal


@pytest.fixture
def before():
    return import_portal(seeded_portal().client()).spec


def edited(spec, change):
    copy = spec.model_copy(deep=True)
    change(copy)
    return PortalSpec.model_validate(copy.model_dump())  # as after loading a real file


def texts(before, after):
    return [line.text for line in describe_changes(before, after)]


def test_pipeline_changes(before):
    def change(spec):
        main = spec.crm.deal_categories[0]
        main.stages[0].name = "Новая заявка"
        main.stages.insert(2, Stage(key="kp", name="КП", sort=25))
        main.stages = [s for s in main.stages if s.key != "finalnyy_schet"]
        spec.crm.deal_categories.append(Category(key="tender", name="Тендеры", stages=[
            Stage(key="new", name="Заявка", sort=10),
            Stage(key="won", name="Выиграли", semantic=StageSemantic.success, sort=20),
        ]))  # fmt: skip
        spec.crm.deal_categories[1].name = "Сервисный отдел"
        spec.crm.deal_categories.append(Category(key="audit", name="Аудит", stages=[
            Stage(key="new", name="Начало", sort=10),
            Stage(key="won", name="Готово", semantic=StageSemantic.success, sort=20),
            Stage(key="lose", name="Отказ", semantic=StageSemantic.failure, sort=30),
            Stage(key="nc", name="Нет связи", semantic=StageSemantic.failure, sort=40),
        ]))  # fmt: skip

    lines = texts(before, edited(before, change))
    assert "В воронке «Общая» стадия «Новая» переименована в «Новая заявка»." in lines
    assert "В воронке «Общая» добавлена стадия «КП» (в работе)." in lines
    assert "Из воронки «Общая» удалена стадия «Финальный счёт»." in lines
    assert "Создана воронка «Тендеры»: Заявка → Выиграли." in lines
    assert "Создана воронка «Аудит»: Начало → Готово; провальные: Отказ, Нет связи." in lines
    assert "Воронка «Сервис» переименована в «Сервисный отдел»." in lines
    # Stages of the new pipeline are not listed one by one.
    assert not any("«Выиграли» (успех)" in line for line in lines)


def test_field_changes(before):
    def change(spec):
        deal = spec.crm.fields["deal"]
        source = deal[0]
        source.label = "Источник обращения"
        source.values[0].value = "Сайт компании"
        source.values.append(EnumValue(key="expo", value="Выставка"))
        source.values = [v for v in source.values if v.key != "call"]
        spec.crm.fields["deal"] = [f for f in deal if f.key != "printer"]
        spec.crm.fields["deal"].append(
            Field(
                key="region",
                label="Регион",
                type=FieldType.enumeration,
                required=True,
                values=[EnumValue(key="a", value="Алматы")],
            )
        )
        role = Field(key="post", label="Роль", type=FieldType.string, multiple=True)
        spec.crm.fields[EntityType.contact] = [role]

    lines = texts(before, edited(before, change))
    assert "Поле «Источник» в сделках переименовано в «Источник обращения»." in lines
    assert ("В списке «Источник обращения» в сделках значение «Сайт» переименовано в "
            "«Сайт компании».") in lines  # fmt: skip
    assert "В список «Источник обращения» в сделках добавлено значение «Выставка»." in lines
    assert "Из списка «Источник обращения» в сделках удалено значение «Звонок»." in lines
    assert "Удалено поле «Тип принтера» в сделках." in lines
    assert ("Добавлено поле «Регион» в сделках (список, обязательное): Алматы.") in lines
    assert "Добавлено поле «Роль» в контактах (строка, несколько значений)." in lines


def test_field_replace_and_required(before):
    def change(spec):
        printer = spec.crm.fields["deal"][1]
        printer.type = FieldType.boolean
        printer.settings = {}

    assert texts(before, edited(before, change)) == [
        "Поле «Тип принтера» в сделках пересоздано с типом «да/нет»."
    ]

    def required(spec):
        spec.crm.fields["deal"][1].required = True

    assert texts(before, edited(before, required)) == [
        "Поле «Тип принтера» в сделках сделано обязательным."
    ]


def test_department_changes(before):
    def change(spec):
        deps = spec.structure.departments
        deps.append(Department(key="servis", name="Сервис", parent="kompaniya"))
        deps[1].name = "Отдел продаж"

    lines = texts(before, edited(before, change))
    assert "Создан отдел «Сервис» в составе «Компания»." in lines
    assert "Отдел «Продажи» переименован в «Отдел продаж»." in lines


def test_changes_are_grouped_by_section(before):
    def change(spec):
        spec.structure.departments.append(Department(key="x", name="X", parent="kompaniya"))
        spec.crm.fields[EntityType.contact] = [Field(key="a", label="A", type=FieldType.string)]
        spec.crm.deal_categories[0].stages[0].name = "Новая заявка"
        spec.crm.fields["deal"][1].label = "Принтер"

    sections = [line.section for line in describe_changes(before, edited(before, change))]
    assert sections == ["Воронки и стадии", "Поля: Сделки", "Поля: Контакты",
                        "Структура компании"]  # fmt: skip


def test_render(before):
    after = edited(before, lambda s: setattr(s.crm.deal_categories[0].stages[0], "name", "Лид"))
    doc = render(after, changes=describe_changes(before, after), today=date(2026, 10, 15))
    assert doc.startswith("# Отчёт о настройке портала test.bitrix24.ru")
    assert "Дата: 15.10.2026" in doc
    assert "## Что сделано\n\n### Воронки и стадии\n\n- В воронке «Общая»" in doc
    assert "**Общая** (основная)" in doc and "6. Сделка успешна ✅" in doc
    assert "| Источник | список | Сайт, Звонок |" in doc
    assert "- Компания\n  - Продажи" in doc
    assert "Что нужно настроить вручную" not in doc

    assert "Изменений структуры не было." in render(before, changes=[])
    assert "## Что сделано" not in render(before, changes=None)


# --- AI part ---------------------------------------------------------------------------


class FakeClaude:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload, self.stop_reason, self.requests = payload, stop_reason, []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=4000, output_tokens=1500,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )  # fmt: skip


AI_ANSWER = {
    "summary": "Портал готов к приёму сервисных заявок.",
    "manual_steps": [
        {"title": "Робот SMS при выполнении", "why": "Клиент узнаёт, что ремонт завершён.",
         "steps": ["Откройте CRM → Сделки → воронка «Сервисные заявки».",
                   "Нажмите «Роботы» и добавьте «Отправить SMS» на стадию «Выполнено»."]},
    ],
}  # fmt: skip


def test_ai_manual_steps_and_render(before):
    claude = FakeClaude(AI_ANSWER)
    after = edited(before, lambda s: setattr(s.crm.deal_categories[0], "name", "Продажи"))
    changes = describe_changes(before, after)
    part = ai_manual_steps(claude, after, changes, "ТЗ: SMS клиенту",
                           model="claude-opus-5-5", effort="high")  # fmt: skip
    assert part.summary.startswith("Портал готов")
    assert part.manual_steps[0].steps[1].startswith("Нажмите «Роботы»")
    request = claude.requests[0]
    sent = [block["text"] for block in request["messages"][0]["content"]]
    assert "ТЗ: SMS клиенту" in sent[0]
    assert "Воронка «Общая» переименована в «Продажи»." in sent[1]
    assert request["output_config"]["format"]["type"] == "json_schema"

    doc = render(after, changes=changes, ai=part)
    assert "Портал готов к приёму сервисных заявок." in doc
    assert "### 1. Робот SMS при выполнении" in doc
    assert "2. Нажмите «Роботы»" in doc
    assert "могут немного отличаться" in doc


def test_ai_is_told_which_robots_are_not_configured(before):
    # The model once wrote that a robot "was created automatically": it must get the
    # pending rules as explicitly not configured.
    category = before.crm.deal_categories[0]
    stage = f"crm.deal_categories.{category.key}.stages.{category.stages[0].key}"
    data = before.model_dump(mode="json")
    data["automation"] = [
        {"key": "call", "stage": stage, "action": "task", "title": "Позвонить клиенту"},
        {"key": "sms", "stage": stage, "action": "sms", "title": "SMS готово", "done": True},
    ]
    spec = PortalSpec.model_validate(data)
    claude = FakeClaude(AI_ANSWER)
    ai_manual_steps(claude, spec, [], "ТЗ", model="claude-haiku-5-5", effort="low")
    request = claude.requests[0]
    sent = "\n".join(block["text"] for block in request["messages"][0]["content"])
    robots = sent.split("<robots_not_configured>")[1].split("</robots_not_configured>")[0]
    assert "Позвонить клиенту" in robots and "Поставить задачу" in robots
    assert "SMS готово" not in robots  # already set up by hand
    assert "NOT set up on the portal" in request["system"]

    # The rules are not part of the structure the model reads.
    structure = sent.split("<crm_structure>")[1]
    assert "Позвонить клиенту" not in structure and "automation" not in structure

    # The document itself says what is left, whatever the model wrote.
    doc = render(spec, changes=None)
    assert "> ⚠️ Нужно настроить вручную: 1 робот или триггер." in doc
    assert "Нужно настроить вручную" not in render(before, changes=None)

    claude = FakeClaude(AI_ANSWER)
    ai_manual_steps(claude, before, [], "ТЗ", model="claude-haiku-5-5", effort="low")
    sent = "\n".join(b["text"] for b in claude.requests[0]["messages"][0]["content"])
    assert "<robots_not_configured>\nnone\n" in sent


def test_ai_manual_steps_errors(before):
    with pytest.raises(RuntimeError, match="not valid JSON"):
        ai_manual_steps(FakeClaude("nope"), before, [], "x", model="claude-haiku-5-5",
                        effort="low")  # fmt: skip


# --- command line ----------------------------------------------------------------------


@pytest.fixture
def project(tmp_path, monkeypatch):
    portal = seeded_portal()

    def client(url, read_only=True):
        return BitrixClient(url, transport=portal.transport(), sleep=lambda _: None,
                            read_only=read_only)  # fmt: skip

    monkeypatch.setattr(cli, "BitrixClient", client)
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", "https://test.bitrix24.ru/rest/1/x/")
    monkeypatch.setenv("BITRIX_ALLOW_WRITE", "yes")
    spec_path = tmp_path / "portal.yaml"
    assert cli.main(["import", "-o", str(spec_path)]) == 0
    return spec_path


def apply(spec_path, change):
    from crmbuilder.spec import dump_spec

    spec = load_spec(spec_path)
    change(spec)
    dump_spec(spec, spec_path)
    assert cli.main(["apply", str(spec_path), "--yes", "--allow-destructive"]) == 0


def test_cli_report_counts_from_the_first_snapshot(project, capsys):
    apply(project, lambda s: setattr(s.crm.deal_categories[0].stages[0], "name", "Заявка"))
    apply(project, lambda s: setattr(s.crm.deal_categories[1], "name", "Сервисный отдел"))
    capsys.readouterr()
    assert cli.main(["report", str(project)]) == 0
    out = capsys.readouterr().out
    assert "report saved to" in out and "2 change(s)" in out
    doc = (project.parent / "report.md").read_text(encoding="utf-8")
    assert "стадия «Новая» переименована в «Заявка»" in doc  # from the first apply
    assert "Воронка «Сервис» переименована в «Сервисный отдел»." in doc

    latest = sorted((project.parent / ".history").iterdir())[-1].name
    assert cli.main(["report", str(project), "--since", latest, "-o",
                     str(project.parent / "last.md")]) == 0  # fmt: skip
    last = (project.parent / "last.md").read_text(encoding="utf-8")
    assert "Сервисный отдел" in last and "«Заявка»" not in last


def test_cli_report_without_history(project, capsys):
    assert cli.main(["report", str(project)]) == 0
    assert "structure only" in capsys.readouterr().out
    assert "## Что сделано" not in (project.parent / "report.md").read_text(encoding="utf-8")


def test_cli_report_with_ai(project, tmp_path, monkeypatch, capsys):
    apply(project, lambda s: setattr(s.crm.deal_categories[0].stages[0], "name", "Заявка"))
    brief = tmp_path / "tz.md"
    brief.write_text("Нужен SMS клиенту", encoding="utf-8")
    monkeypatch.setattr(cli, "_anthropic_client", lambda: FakeClaude(AI_ANSWER))
    assert cli.main(["report", str(project), "--ai", "--brief", str(brief),
                     "--model", "claude-haiku-5-5", "--title", "Сдача проекта"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "1 manual step(s)" in out and "[AI: about $" in out
    doc = (project.parent / "report.md").read_text(encoding="utf-8")
    assert doc.startswith("# Сдача проекта")
    assert "### 1. Робот SMS при выполнении" in doc


def test_cli_report_ai_needs_brief(project, capsys):
    assert cli.main(["report", str(project), "--ai"]) == 2
    assert "--ai needs --brief" in capsys.readouterr().err


def test_cli_report_unknown_snapshot(project, capsys):
    assert cli.main(["report", str(project), "--since", "nope"]) == 2
    assert "snapshot nope not found" in capsys.readouterr().err


def test_new_departments_are_listed_parents_first(before):
    def change(spec):
        # Children come first in the file, as in a real portal.
        spec.structure.departments += [
            Department(key="terapevty", name="Терапевты", parent="vrachi"),
            Department(key="vrachi", name="Врачи", parent="kompaniya"),
        ]

    lines = texts(before, edited(before, change))
    assert lines == [
        "Создан отдел «Врачи» в составе «Компания».",
        "Создан отдел «Терапевты» в составе «Врачи».",
    ]


def test_smart_process_sentences(before):
    from crmbuilder.spec import SmartProcess

    def create(spec):
        spec.crm.smart_processes.append(SmartProcess(
            key="dogovory", title="Договоры", stages_enabled=True, categories_enabled=True,
            client_enabled=True, categories=[
                Category(key="a", name="Основные", default=True, stages=[
                    Stage(key="new", name="Черновик", sort=10),
                    Stage(key="success", name="Действует", semantic=StageSemantic.success,
                          sort=20),
                    Stage(key="fail", name="Истёк", semantic=StageSemantic.failure, sort=30)]),
                Category(key="b", name="Разовые", stages=[
                    Stage(key="new", name="Заявка", sort=10),
                    Stage(key="won", name="Готово", semantic=StageSemantic.success,
                          sort=20)])]))  # fmt: skip
        spec.crm.smart_processes[0].title = "Заказы клиентов"

    lines = describe_changes(before, edited(before, create))
    assert [(line.section, line.text) for line in lines] == [
        ("Смарт-процессы", "Смарт-процесс «Заказы» переименован в «Заказы клиентов»."),
        ("Смарт-процессы", "Создан смарт-процесс «Договоры» с привязкой к клиентам. "
         "Воронка «Основные»: Черновик → Действует; провальные: Истёк. "
         "Воронка «Разовые»: Заявка → Готово."),
    ]  # fmt: skip
    gone = edited(before, lambda s: s.crm.smart_processes.clear())
    assert texts(before, gone) == ["Удалён смарт-процесс «Заказы»."]
