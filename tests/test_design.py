from types import SimpleNamespace

import pytest

from crmbuilder import cli
from crmbuilder.apply import Applier
from crmbuilder.design import (
    FALLBACK_BETA,
    NOT_AVAILABLE_ANSWER,
    DesignError,
    EditError,
    SpecEditor,
    design,
)
from crmbuilder.importer import import_portal
from crmbuilder.plan import diff_specs
from crmbuilder.spec import load_spec

from .fake_bitrix import seeded_portal


def base_spec():
    return import_portal(seeded_portal().client()).spec


# --- a scripted stand-in for the Anthropic client ------------------------------------


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool(name, tool_input, block_id=None):
    return SimpleNamespace(
        type="tool_use", id=block_id or f"tu_{name}", name=name, input=tool_input
    )


def response(stop_reason, *content, usage=None):
    usage = usage or SimpleNamespace(
        input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
        cache_creation_input_tokens=0,
    )  # fmt: skip
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage,
                           stop_details=None)  # fmt: skip


class FakeClaude:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.requests: list[dict] = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        # Messages are mutated after the call; keep what was sent at this point.
        self.requests.append({**kwargs, "messages": list(kwargs["messages"])})
        return self.responses.pop(0)


TENDER = {
    "name": "Тендеры",
    "sort": 300,
    "stages": [
        {"key": "new", "name": "Новый тендер", "sort": 10},
        {"key": "kp", "name": "КП отправлено", "sort": 20},
        {"key": "won", "name": "Выиграли", "semantic": "success", "sort": 30},
        {"key": "lose", "name": "Проиграли", "semantic": "failure", "sort": 40},
    ],
}


# --- editor --------------------------------------------------------------------------


def test_editor_create_update_delete():
    editor = SpecEditor(base_spec())
    editor.create("crm.deal_categories.tender", TENDER)
    editor.update("crm.deal_categories.tender.stages.kp", {"name": "КП у клиента"})
    editor.create("crm.fields.contact.birthday", {"label": "День рождения", "type": "date"})
    editor.create("crm.fields.deal.source.values.expo", {"value": "Выставка", "sort": 30})
    editor.delete("crm.fields.deal.printer")
    editor.create("structure.departments.tenders", {"name": "Тендерный отдел",
                                                    "parent": "kompaniya"})  # fmt: skip
    spec = editor.spec
    tender = next(c for c in spec.crm.deal_categories if c.key == "tender")
    assert [s.name for s in tender.stages][1] == "КП у клиента"
    assert [f.key for f in spec.crm.fields["contact"]] == ["birthday"]
    assert "printer" not in {f.key for f in spec.crm.fields["deal"]}
    assert editor.log[0] == "create crm.deal_categories.tender"


@pytest.mark.parametrize(
    "action, args, message",
    [
        ("create", ("crm.deal_categories.obshchaya", {"name": "X"}), "already exists"),
        ("update", ("crm.deal_categories.obshchaya", {"key": "main"}), "cannot be renamed"),
        ("update", ("crm.deal_categories.nope", {"name": "X"}), "does not exist"),
        ("create", ("crm.smart_processes.new_sp", {"title": "X"}), "not supported yet"),
        ("create", ("crm.pipelines.x", {"name": "X"}), "not a valid address"),
        ("create", ("crm.fields.invoice.x", {"label": "X", "type": "string"}), "unknown entity"),
        ("create", ("structure.departments.x", {"name": "X", "parent": "ghost"}), "unknown parent"),
        ("create", ("crm.fields.deal.x", {"label": "X", "type": "string",
                                          "values": [{"key": "a", "value": "A"}]}),
         "only allowed for enumeration"),
        ("create", ("crm.deal_categories.Bad", {"name": "X"}), "should match pattern"),
    ],
)  # fmt: skip
def test_editor_rejects_bad_edits_and_keeps_the_spec(action, args, message):
    editor = SpecEditor(base_spec())
    before = editor.spec
    with pytest.raises(EditError, match=message):
        getattr(editor, action)(*args)
    assert editor.spec == before


def test_editor_view():
    editor = SpecEditor(base_spec())
    assert "portal: test.bitrix24.ru" in editor.view()
    assert editor.view("crm.fields.deal.source").startswith("key: source")


# --- conversation --------------------------------------------------------------------


def test_design_conversation():
    claude = FakeClaude(
        response("tool_use", text("Уточню пару вещей."),
                 tool("ask_user", {"questions": ["Нужна ли воронка тендеров?"]})),
        response("tool_use",
                 tool("create", {"address": "crm.deal_categories.tender",
                                 "object": {**TENDER, "stages": TENDER["stages"] + [
                                     {"key": "bad", "name": "X", "sort": 50,
                                      "semantic": "nonsense"}]}}, "tu_1")),
        response("tool_use", tool("create", {"address": "crm.deal_categories.tender",
                                             "object": TENDER}, "tu_2"),
                 tool("delete", {"address": "crm.fields.deal.printer"}, "tu_3")),
        response("end_turn", text("Готово: добавил воронку «Тендеры», удалил поле принтера.")),
    )  # fmt: skip
    asked = []
    lines = []

    def ask(questions):
        asked.append(questions)
        return ["Да, нужна"]

    spec = base_spec()
    result = design(claude, spec, "Добавь тендеры, убери лишнее", ask=ask, report=lines.append)

    assert asked == [["Нужна ли воронка тендеров?"]]
    assert "tender" in {c.key for c in result.spec.crm.deal_categories}
    assert "printer" not in {f.key for f in result.spec.crm.fields["deal"]}
    assert result.summary.startswith("Готово")
    assert result.edits == ["create crm.deal_categories.tender", "delete crm.fields.deal.printer"]
    assert result.usage.requests == 4
    assert result.usage.cost("claude-opus-5-5") == pytest.approx(4 * (1000 * 4 + 200 * 20) / 1e6)
    assert any(line.startswith("  ! ") for line in lines)  # the rejected edit was reported

    # The invalid edit came back to the model as an error, the answer as a plain result.
    second, third = claude.requests[1], claude.requests[2]
    (answer,) = second["messages"][-1]["content"]
    assert "A: Да, нужна" in answer["content"] and "is_error" not in answer
    (error,) = third["messages"][-1]["content"]
    assert error["is_error"] is True and "rejected" in error["content"]

    first = claude.requests[0]
    assert first["model"] == "claude-opus-5-5"
    assert first["betas"] == [FALLBACK_BETA] and first["fallbacks"] == "default"
    assert first["cache_control"] == {"type": "ephemeral"}
    assert first["thinking"] == {"type": "adaptive"}
    assert first["output_config"] == {"effort": "high"}
    assert "<current_spec>" in first["messages"][0]["content"][0]["text"]
    assert spec == base_spec()  # the input spec object is not modified


def test_without_ask_the_model_is_told_to_assume():
    claude = FakeClaude(
        response("tool_use", tool("ask_user", {"questions": ["Сколько врачей?"]})),
        response("end_turn", text("Ок")),
    )
    design(claude, base_spec(), "клиника")
    (result,) = claude.requests[1]["messages"][-1]["content"]
    assert result["content"] == NOT_AVAILABLE_ANSWER


def test_haiku_gets_no_fallbacks():
    claude = FakeClaude(response("end_turn", text("Ок")))
    design(claude, base_spec(), "x", model="claude-haiku-5-5")
    assert "fallbacks" not in claude.requests[0]


@pytest.mark.parametrize("stop, message", [("refusal", "declined"), ("max_tokens", "cut off")])
def test_bad_stops_raise(stop, message):
    with pytest.raises(DesignError, match=message):
        design(FakeClaude(response(stop)), base_spec(), "x")


def test_turn_limit():
    loop = [response("tool_use", tool("view_spec", {})) for _ in range(3)]
    with pytest.raises(DesignError, match="no result after 3 rounds"):
        design(FakeClaude(*loop), base_spec(), "x", max_turns=3)


def test_designed_spec_applies_cleanly():
    fake = seeded_portal()
    before = import_portal(fake.client())
    claude = FakeClaude(
        response("tool_use", tool("create", {"address": "crm.deal_categories.tender",
                                             "object": TENDER}),
                 tool("create", {"address": "crm.fields.deal.order",
                                 "object": {"label": "Заказ", "type": "crm",
                                            "settings": {"SMART_PROCESS:zakazy": "Y"}}},
                      "tu_b")),
        response("end_turn", text("Готово")),
    )  # fmt: skip
    result = design(claude, before.spec, "тендеры")
    plan = diff_specs(before.spec, result.spec)
    applier = Applier(fake.writable_client(), before.spec, result.spec, before.state)
    assert applier.unsupported(plan) == []
    assert applier.apply(plan).ok
    after = import_portal(fake.client(), previous_state=applier.state)
    assert diff_specs(after.spec, result.spec).changes == []


# --- command line ----------------------------------------------------------------------


@pytest.fixture
def spec_file(tmp_path, monkeypatch):
    from crmbuilder.bitrix import BitrixClient

    fake = seeded_portal()
    monkeypatch.setattr(
        cli, "BitrixClient",
        lambda url, **kw: BitrixClient(url, transport=fake.transport(), sleep=lambda _: None),
    )  # fmt: skip
    path = tmp_path / "portal.yaml"
    assert cli.main(["import", "--webhook", "https://test.bitrix24.ru/rest/1/x/",
                     "-o", str(path)]) == 0  # fmt: skip
    return path


def test_cli_design_saves_after_confirmation(spec_file, monkeypatch, capsys):
    claude = FakeClaude(
        response("tool_use", tool("create", {"address": "crm.deal_categories.tender",
                                             "object": TENDER})),
        response("end_turn", text("Добавил воронку тендеров.")),
    )  # fmt: skip
    monkeypatch.setattr(cli, "_anthropic_client", lambda: claude)
    monkeypatch.setattr("builtins.input", lambda: "y")
    capsys.readouterr()
    assert cli.main(["design", str(spec_file), "--brief-text", "Нужны тендеры"]) == 0
    out = capsys.readouterr().out
    assert "Добавил воронку тендеров." in out
    assert "+ create  crm.deal_categories.tender" in out
    assert "about $0.02" in out
    assert "tender" in {c.key for c in load_spec(spec_file).crm.deal_categories}


def test_cli_design_not_saved_on_no(spec_file, monkeypatch, capsys):
    before = spec_file.read_text(encoding="utf-8")
    claude = FakeClaude(
        response("tool_use", tool("delete", {"address": "crm.fields.deal.printer"})),
        response("end_turn", text("Удалил поле.")),
    )
    monkeypatch.setattr(cli, "_anthropic_client", lambda: claude)
    monkeypatch.setattr("builtins.input", lambda: "n")
    assert cli.main(["design", str(spec_file), "--brief-text", "убери принтер"]) == 1
    assert spec_file.read_text(encoding="utf-8") == before


def test_cli_design_reads_brief_file(spec_file, tmp_path, monkeypatch):
    brief = tmp_path / "tz.md"
    brief.write_text("Нужны тендеры", encoding="utf-8")
    claude = FakeClaude(response("end_turn", text("Ничего менять не нужно.")))
    monkeypatch.setattr(cli, "_anthropic_client", lambda: claude)
    assert cli.main(["design", str(spec_file), "--brief", str(brief), "--yes"]) == 0
    assert "Нужны тендеры" in claude.requests[0]["messages"][0]["content"][1]["text"]


def test_cli_design_requires_a_brief(spec_file, capsys):
    assert cli.main(["design", str(spec_file)]) == 2
    assert "--brief" in capsys.readouterr().err


def test_cli_design_reports_missing_key(spec_file, monkeypatch, capsys):
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    error = anthropic.AuthenticationError(
        "invalid x-api-key", response=httpx2.Response(401, request=request), body=None
    )

    class Failing:
        beta = SimpleNamespace(
            messages=SimpleNamespace(create=lambda **kw: (_ for _ in ()).throw(error))
        )

    monkeypatch.setattr(cli, "_anthropic_client", lambda: Failing())
    assert cli.main(["design", str(spec_file), "--brief-text", "x"]) == 2
    assert "ANTHROPIC_API_KEY" in capsys.readouterr().err


def test_haiku_cost_is_estimated():
    from crmbuilder.design import Usage

    usage = Usage(input_tokens=12736, output_tokens=3488)
    assert usage.cost("claude-haiku-5-5") == pytest.approx(0.0030176)
    assert usage.cost("some-other-model") is None
