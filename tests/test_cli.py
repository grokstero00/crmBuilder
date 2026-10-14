import json

import pytest

from crmbuilder import cli
from crmbuilder.spec import load_spec

from .conftest import FakePortal


def test_import_writes_spec_and_state(portal: FakePortal, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BitrixClient", lambda url, **kw: portal.client())
    out = tmp_path / "clinic" / "portal.yaml"
    code = cli.main(["import", "--webhook", "https://x/rest/1/t/", "-o", str(out)])
    assert code == 0

    spec = load_spec(out)
    assert spec.portal == "clinic.bitrix24.ru"
    state = json.loads((tmp_path / "clinic" / "portal.state.json").read_text(encoding="utf-8"))
    assert "crm.deal_categories.obshchaya" in state["resources"]

    captured = capsys.readouterr()
    assert "deal categories: 2" in captured.out
    assert "warning:" in captured.err


def test_import_requires_webhook(monkeypatch, capsys):
    monkeypatch.delenv("BITRIX_WEBHOOK_URL", raising=False)
    assert cli.main(["import"]) == 2
    assert "BITRIX_WEBHOOK_URL" in capsys.readouterr().err


def test_validate(tmp_path, capsys):
    good = tmp_path / "good.yaml"
    good.write_text("version: 1\nportal: x.bitrix24.ru\n", encoding="utf-8")
    assert cli.main(["validate", str(good)]) == 0

    bad = tmp_path / "bad.yaml"
    bad.write_text("portal: x\ncrm:\n  deal_categories:\n    - {key: Bad, name: A}\n")
    assert cli.main(["validate", str(bad)]) == 1
    assert "crm.deal_categories.0.key" in capsys.readouterr().err


def test_validate_missing_file(tmp_path):
    assert cli.main(["validate", str(tmp_path / "nope.yaml")]) == 2


def test_version(capsys):
    with pytest.raises(SystemExit):
        cli.main(["--version"])
    assert "crmbuilder" in capsys.readouterr().out


def test_import_reports_unwritable_output(portal: FakePortal, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BitrixClient", lambda url, **kw: portal.client())
    blocker = tmp_path / "file"
    blocker.write_text("")
    code = cli.main(["import", "--webhook", "https://x/rest/1/t/", "-o", str(blocker / "p.yaml")])
    assert code == 1
    assert "cannot write output" in capsys.readouterr().err


def test_import_reports_network_errors(monkeypatch, capsys):
    import httpx

    from crmbuilder.bitrix import BitrixClient

    def refuse(request):
        raise httpx.ConnectError("connection refused")

    monkeypatch.setattr(
        cli,
        "BitrixClient",
        lambda url, **kw: BitrixClient(url, transport=httpx.MockTransport(refuse)),
    )
    assert cli.main(["import", "--webhook", "https://x/rest/1/t/"]) == 1
    assert "cannot reach the portal" in capsys.readouterr().err


def test_import_rejects_malformed_webhook(capsys):
    assert cli.main(["import", "--webhook", "https://clinic.bitrix24.ru/crm/deal/"]) == 2
    assert "invalid webhook URL" in capsys.readouterr().err


def _import(portal, tmp_path, monkeypatch, *extra):
    monkeypatch.setattr(cli, "BitrixClient", lambda url, **kw: portal.client())
    out = tmp_path / "portal.yaml"
    assert cli.main(["import", "--webhook", "https://x/rest/1/t/", "-o", str(out), *extra]) == 0
    return out


def test_plan_against_live_portal(portal: FakePortal, tmp_path, monkeypatch, capsys):
    from crmbuilder.spec import dump_spec

    out = _import(portal, tmp_path, monkeypatch)
    capsys.readouterr()
    assert cli.main(["plan", str(out), "--webhook", "https://x/rest/1/t/"]) == 0
    assert "no changes" in capsys.readouterr().out

    spec = load_spec(out)
    spec.crm.deal_categories[1].stages[0].name = "Новая заявка (сайт)"
    spec.crm.fields["deal"] = [f for f in spec.crm.fields["deal"] if f.key != "doctor"]
    dump_spec(spec, out)

    assert cli.main(["plan", str(out), "--webhook", "https://x/rest/1/t/"]) == 0
    text = capsys.readouterr().out
    assert "~ update  crm.deal_categories.pervichnyy_priem.stages.new" in text
    assert "- delete  crm.fields.deal.doctor" in text
    # plan never writes to the portal
    assert all(".list" in m or m == "department.get" for m, _ in portal.calls)


def test_plan_json(portal: FakePortal, tmp_path, monkeypatch, capsys):
    out = _import(portal, tmp_path, monkeypatch)
    capsys.readouterr()
    assert cli.main(["plan", str(out), "--json", "--webhook", "https://x/rest/1/t/"]) == 0
    assert json.loads(capsys.readouterr().out)["changes"] == []


def test_plan_requires_state(tmp_path, capsys):
    spec = tmp_path / "portal.yaml"
    spec.write_text("version: 1\nportal: clinic.bitrix24.ru\n", encoding="utf-8")
    assert cli.main(["plan", str(spec), "--webhook", "https://x/rest/1/t/"]) == 2
    assert "run `crmbuilder import` first" in capsys.readouterr().err


def test_plan_refuses_other_portal(portal: FakePortal, tmp_path, monkeypatch, capsys):
    out = _import(portal, tmp_path, monkeypatch)
    text = out.read_text(encoding="utf-8").replace("clinic.bitrix24.ru", "other.bitrix24.ru")
    out.write_text(text, encoding="utf-8")
    capsys.readouterr()
    assert cli.main(["plan", str(out), "--webhook", "https://x/rest/1/t/"]) == 2
    assert "belongs to other.bitrix24.ru" in capsys.readouterr().err


def test_plan_against_file(tmp_path, capsys):
    a = tmp_path / "a.yaml"
    b = tmp_path / "b.yaml"
    a.write_text("portal: p\nstructure:\n  departments:\n  - {key: hq, name: HQ}\n")
    b.write_text("portal: p\nstructure:\n  departments:\n  - {key: hq, name: Head office}\n")
    assert cli.main(["plan", str(b), "--against", str(a)]) == 0
    assert 'name: "HQ" -> "Head office"' in capsys.readouterr().out


def test_reimport_reuses_state_unless_fresh(portal: FakePortal, tmp_path, monkeypatch, capsys):
    out = _import(portal, tmp_path, monkeypatch)
    state_file = tmp_path / "portal.state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["resources"]["crm.fields.deal.vrach"] = state["resources"].pop("crm.fields.deal.doctor")
    state_file.write_text(json.dumps(state), encoding="utf-8")

    _import(portal, tmp_path, monkeypatch)
    assert "keys kept from previous import" in capsys.readouterr().out
    assert "vrach" in {f.key for f in load_spec(out).crm.fields["deal"]}

    _import(portal, tmp_path, monkeypatch, "--fresh")
    assert "doctor" in {f.key for f in load_spec(out).crm.fields["deal"]}


def test_import_refuses_state_of_other_portal(portal: FakePortal, tmp_path, monkeypatch, capsys):
    _import(portal, tmp_path, monkeypatch)
    state_file = tmp_path / "portal.state.json"
    state = json.loads(state_file.read_text(encoding="utf-8"))
    state["portal"] = "other.bitrix24.ru"
    state_file.write_text(json.dumps(state), encoding="utf-8")
    out = tmp_path / "portal.yaml"
    assert cli.main(["import", "--webhook", "https://x/rest/1/t/", "-o", str(out)]) == 2
    assert "belongs to other.bitrix24.ru" in capsys.readouterr().err


def test_certificate_error_has_a_hint(monkeypatch, capsys):
    import httpx

    from crmbuilder.bitrix import BitrixClient

    def fail(request):
        raise httpx.ConnectError("[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed")

    monkeypatch.setattr(
        cli,
        "BitrixClient",
        lambda url, **kw: BitrixClient(url, transport=httpx.MockTransport(fail)),
    )
    assert cli.main(["import", "--webhook", "https://x/rest/1/t/"]) == 1
    assert "BITRIX_CA_BUNDLE=/workspace/ca.pem" in capsys.readouterr().err
