import json
from types import SimpleNamespace

import pytest

from crmbuilder import cli
from crmbuilder.audit import Severity, ai_review, audit, render_markdown, render_text
from crmbuilder.spec import PortalSpec, dump_spec

from .fake_bitrix import seeded_portal


def stages(*items):
    return [{"key": k, "name": n, "semantic": sem, "sort": s} for k, n, sem, s in items]


GOOD_STAGES = stages(
    ("new", "Новая", "process", 10),
    ("rabota", "В работе", "process", 20),
    ("kp", "КП", "process", 30),
    ("won", "Успех", "success", 40),
    ("lose", "Провал", "failure", 50),
)

MESSY = {
    "portal": "x.bitrix24.ru",
    "crm": {
        "deal_categories": [
            {"key": "main", "name": "Продажи", "stages": stages(
                ("new", "Новая", "process", 10),
                ("schet", "Cчёт", "process", 20),          # Latin C
                ("schet2", "Счёт", "process", 25),
                ("won", "Успех", "success", 30),
                ("lose", "Провал", "failure", 15),        # failure before success
            )},
            {"key": "a", "name": "Филиал А", "stages": GOOD_STAGES},
            {"key": "b", "name": "Филиал Б", "stages": GOOD_STAGES},
            {"key": "c", "name": "Филиал  В ", "stages": stages(
                ("new", "Новая", "process", 10),
                ("won1", "Успех", "success", 20),
                ("won2", "Тоже успех", "success", 30),
            )},
        ],
        "fields": {
            "deal": [
                {"key": "summa", "label": "Сумма", "type": "money"},
                {"key": "veroyatnost", "label": "Вероятность, %", "type": "double"},
                {"key": "test", "label": "тест", "type": "string"},
                {"key": "dup1", "label": "Регион", "type": "string"},
                {"key": "dup2", "label": "регион", "type": "string"},
                {"key": "status", "label": "Статус", "type": "enumeration",
                 "settings": {"CAPTION_NO_VALUE": "В работе"},
                 "values": [{"key": "v_rabote", "value": "В работе"},
                            {"key": "pauza", "value": "Пауза"}]},
                {"key": "one", "label": "Одно значение", "type": "enumeration",
                 "values": [{"key": "da", "value": "Да"}]},
                {"key": "empty", "label": "Пустой список", "type": "enumeration"},
                {"key": "link", "label": "Связь", "type": "crm",
                 "settings": {"DYNAMIC_1066": "Y"}},
                {"key": "raw", "label": "UF_CRM_123", "type": "string"},
            ],
            "contact": [{"key": "post", "label": "Должность", "type": "string"}],
            "company": [{"key": "bin", "label": "БИН", "type": "string"},
                        {"key": "email", "label": "E-mail", "type": "string"}],
        },
    },
    "structure": {"departments": [
        {"key": "root", "name": "Компания"},
        {"key": "s1", "name": "Продажи", "parent": "root"},
        {"key": "s2", "name": "продажи", "parent": "root"},
    ]},
}  # fmt: skip


@pytest.fixture
def findings():
    return audit(PortalSpec.model_validate(MESSY))


def codes(findings):
    return {(f.code, f.address) for f in findings}


def test_rule_checks_find_the_problems(findings):
    found = codes(findings)
    expected = {
        ("mixed_script", "crm.deal_categories.main.stages.schet"),
        ("stage_order", "crm.deal_categories.main"),
        ("success_stages", "crm.deal_categories.c"),
        ("no_failure_stage", "crm.deal_categories.c"),
        ("whitespace", "crm.deal_categories.c"),
        ("similar_pipelines", "crm.deal_categories.a"),
        ("standard_duplicate", "crm.fields.deal.summa"),
        ("standard_duplicate", "crm.fields.deal.veroyatnost"),
        ("standard_duplicate", "crm.fields.contact.post"),
        ("standard_duplicate", "crm.fields.company.email"),
        ("requisites_field", "crm.fields.company.bin"),
        ("junk_name", "crm.fields.deal.test"),
        ("duplicate_name", "crm.fields.deal"),
        ("caption_conflict", "crm.fields.deal.status"),
        ("single_value_list", "crm.fields.deal.one"),
        ("empty_list", "crm.fields.deal.empty"),
        ("broken_link", "crm.fields.deal.link"),
        ("no_label", "crm.fields.deal.raw"),
        ("duplicate_name", "structure.departments"),
    }
    assert expected <= found, expected - found


def test_findings_are_sorted_by_severity(findings):
    ranks = [list(Severity).index(f.severity) for f in findings]
    assert ranks == sorted(ranks)


def test_clean_spec_has_no_findings():
    spec = PortalSpec.model_validate({
        "portal": "x", "crm": {"deal_categories": [{"key": "s", "name": "Продажи",
                                                    "stages": GOOD_STAGES}]},
    })  # fmt: skip
    assert audit(spec) == []
    assert "Проблем не найдено" in render_text("x", [])


def test_a_freshly_imported_portal_has_no_errors():
    from crmbuilder.importer import import_portal

    findings = audit(import_portal(seeded_portal().client()).spec)
    assert not [f for f in findings if f.severity is Severity.error]
    # Two pipelines left with Bitrix default stages are flagged as near-identical.
    assert ("similar_pipelines", "crm.deal_categories.obshchaya") in codes(findings)


def test_reports(findings):
    text = render_text("x.bitrix24.ru", findings)
    assert "Ошибки" in text and "Предупреждения" in text and "Итого:" in text
    assert "«Cчёт»" in text
    md = render_markdown("x.bitrix24.ru", findings, "Оптовая торговля")
    assert md.startswith("# Аудит портала x.bitrix24.ru")
    assert "**Профиль бизнеса (оценка ИИ):** Оптовая торговля" in md
    assert "`crm.fields.deal.summa`" in md


# --- AI review -----------------------------------------------------------------------


class FakeClaude:
    def __init__(self, payload, stop_reason="end_turn"):
        self.payload = payload
        self.stop_reason = stop_reason
        self.requests = []
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.requests.append(kwargs)
        text = self.payload if isinstance(self.payload, str) else json.dumps(self.payload)
        return SimpleNamespace(
            stop_reason=self.stop_reason,
            content=[SimpleNamespace(type="text", text=text)],
            usage=SimpleNamespace(input_tokens=5000, output_tokens=800,
                                  cache_read_input_tokens=0, cache_creation_input_tokens=0),
        )  # fmt: skip


AI_ANSWER = {
    "business_guess": "Поставщик промышленного оборудования",
    "findings": [
        {"severity": "warning", "address": "crm.fields.deal.printer",
         "title": "Поле «Тип принтера» не связано с бизнесом",
         "recommendation": "Удалите, если не используется."},
        {"severity": "bogus", "address": "x", "title": "ignored", "recommendation": ""},
    ],
}  # fmt: skip


def test_ai_review(findings):
    claude = FakeClaude(AI_ANSWER)
    spec = PortalSpec.model_validate(MESSY)
    review = ai_review(claude, spec, findings, context="Продаём насосы", model="claude-opus-5-5",
                       effort="high")  # fmt: skip
    assert review.business_guess == "Поставщик промышленного оборудования"
    (finding,) = review.findings
    assert finding.source == "ai" and finding.severity is Severity.warning
    assert finding.hint == "Удалите, если не используется."

    request = claude.requests[0]
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert request["output_config"]["effort"] == "high"
    assert request["fallbacks"] == "default"
    texts = [block["text"] for block in request["messages"][0]["content"]]
    assert "<portal_structure>" in texts[0]
    assert "Cчёт" in texts[1]  # rule findings are passed on, not re-found
    assert "Продаём насосы" in texts[2]
    assert review.usage.cost("claude-opus-5-5") == pytest.approx((5000 * 4 + 800 * 20) / 1e6)


@pytest.mark.parametrize(
    "payload, stop, message",
    [("not json", "end_turn", "not valid JSON"), ("{}", "refusal", "declined"),
     ("{}", "max_tokens", "cut off")],
)  # fmt: skip
def test_ai_review_errors(payload, stop, message):
    spec = PortalSpec.model_validate({"portal": "x"})
    with pytest.raises(RuntimeError, match=message):
        ai_review(FakeClaude(payload, stop), spec, [], model="claude-haiku-5-5", effort="low")


# --- command line ----------------------------------------------------------------------


@pytest.fixture
def messy_file(tmp_path):
    path = tmp_path / "portal.yaml"
    dump_spec(PortalSpec.model_validate(MESSY), path)
    return path


def test_cli_audit(messy_file, tmp_path, capsys):
    report = tmp_path / "report.md"
    assert cli.main(["audit", str(messy_file), "-o", str(report)]) == 0
    out = capsys.readouterr().out
    assert "Аудит портала x.bitrix24.ru" in out and "«Cчёт»" in out
    assert report.read_text(encoding="utf-8").startswith("# Аудит портала")


def test_cli_audit_json(messy_file, capsys):
    assert cli.main(["audit", str(messy_file), "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["portal"] == "x.bitrix24.ru"
    assert any(f["code"] == "mixed_script" for f in data["findings"])


def test_cli_audit_with_ai(messy_file, monkeypatch, capsys):
    claude = FakeClaude(AI_ANSWER)
    monkeypatch.setattr(cli, "_anthropic_client", lambda: claude)
    assert cli.main(["audit", str(messy_file), "--ai", "--context", "насосы",
                     "--model", "claude-haiku-5-5"]) == 0  # fmt: skip
    out = capsys.readouterr().out
    assert "Похоже на: Поставщик промышленного оборудования" in out
    assert "Тип принтера» не связано с бизнесом [ИИ]" in out
    assert "[AI review: about $0.0009]" in out
    assert "fallbacks" not in claude.requests[0]  # not sent for Haiku


def test_bitrix_default_typo_is_explained():
    spec = PortalSpec.model_validate({"portal": "x", "crm": {"deal_categories": [
        {"key": "s", "name": "Продажи", "stages": [
            {"key": "new", "name": "Новая", "sort": 10},
            {"key": "inv", "name": "Cчёт на предоплату", "sort": 20},
            {"key": "won", "name": "Успех", "semantic": "success", "sort": 30},
            {"key": "lose", "name": "Провал", "semantic": "failure", "sort": 40}]}]}})  # fmt: skip
    (finding,) = audit(spec)
    assert "стандартном названии стадии Битрикс24" in finding.hint


def test_shipped_templates_pass_the_audit():
    from crmbuilder import templates
    from crmbuilder.importer import import_portal

    base = import_portal(seeded_portal().client()).spec
    for name in templates.available():
        merged, report = templates.merge(base, name)
        added = set(report.added)
        own = [f for f in audit(merged) if any(f.address.startswith(a) for a in added)]
        assert own == [], f"template {name}: {own}"
