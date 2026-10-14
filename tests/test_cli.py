import json

import pytest

from crmbuilder import cli
from crmbuilder.spec import load_spec

from .conftest import FakePortal


def test_import_writes_spec_and_state(portal: FakePortal, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr(cli, "BitrixClient", lambda url: portal.client())
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
    monkeypatch.setattr(cli, "BitrixClient", lambda url: portal.client())
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
        cli, "BitrixClient", lambda url: BitrixClient(url, transport=httpx.MockTransport(refuse))
    )
    assert cli.main(["import", "--webhook", "https://x/rest/1/t/"]) == 1
    assert "cannot reach the portal" in capsys.readouterr().err


def test_import_rejects_malformed_webhook(capsys):
    assert cli.main(["import", "--webhook", "https://clinic.bitrix24.ru/crm/deal/"]) == 2
    assert "invalid webhook URL" in capsys.readouterr().err
