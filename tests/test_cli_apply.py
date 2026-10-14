import json

import pytest

from crmbuilder import cli
from crmbuilder.bitrix import BitrixClient
from crmbuilder.spec import StageSemantic, dump_spec, load_spec

from .fake_bitrix import FakeBitrix, seeded_portal

WEBHOOK = "https://test.bitrix24.ru/rest/1/secret/"


@pytest.fixture
def fake(monkeypatch) -> FakeBitrix:
    portal = seeded_portal()

    def client(url, read_only=True):
        return BitrixClient(url, transport=portal.transport(), sleep=lambda _: None,
                            read_only=read_only)  # fmt: skip

    monkeypatch.setattr(cli, "BitrixClient", client)
    monkeypatch.setenv("BITRIX_WEBHOOK_URL", WEBHOOK)
    monkeypatch.setenv("BITRIX_ALLOW_WRITE", "yes")
    return portal


@pytest.fixture
def spec_path(fake, tmp_path):
    path = tmp_path / "portal.yaml"
    assert cli.main(["import", "-o", str(path)]) == 0
    return path


def edit(path, change):
    spec = load_spec(path)
    change(spec)
    dump_spec(spec, path)


def rename_stage(spec):
    spec.crm.deal_categories[0].stages[0].name = "Новая заявка"


def drop_printer(spec):
    spec.crm.fields["deal"] = [f for f in spec.crm.fields["deal"] if f.key != "printer"]


def test_apply_with_confirmation(fake, spec_path, monkeypatch, capsys):
    edit(spec_path, rename_stage)
    monkeypatch.setattr("builtins.input", lambda: "test.bitrix24.ru")
    capsys.readouterr()
    assert cli.main(["apply", str(spec_path)]) == 0
    out = capsys.readouterr().out
    assert "applied 1 of 1 change(s)" in out
    assert "verified: the portal matches the spec" in out
    assert any(s["NAME"] == "Новая заявка" for s in fake.statuses)

    (snapshot,) = (spec_path.parent / ".history").iterdir()
    assert {p.name for p in snapshot.iterdir()} == {
        "before.yaml",
        "before.state.json",
        "desired.yaml",
        "plan.json",
        "journal.jsonl",
    }
    entries = [json.loads(line) for line in (snapshot / "journal.jsonl").read_text().splitlines()]
    assert [e["method"] for e in entries] == ["crm.status.update"]

    assert cli.main(["plan", str(spec_path)]) == 0
    assert "no changes" in capsys.readouterr().out


def test_apply_requires_write_permission(fake, spec_path, monkeypatch, capsys):
    edit(spec_path, rename_stage)
    monkeypatch.delenv("BITRIX_ALLOW_WRITE")
    calls_before = len(fake.calls)
    assert cli.main(["apply", str(spec_path), "--yes"]) == 2
    assert "BITRIX_ALLOW_WRITE=yes" in capsys.readouterr().err
    assert len(fake.calls) == calls_before  # did not even read the portal
    assert fake.writes == []


def test_wrong_confirmation_aborts(fake, spec_path, monkeypatch, capsys):
    edit(spec_path, rename_stage)
    monkeypatch.setattr("builtins.input", lambda: "yes")
    assert cli.main(["apply", str(spec_path)]) == 1
    assert "aborted, nothing was changed" in capsys.readouterr().err
    assert fake.writes == []


def test_destructive_changes_need_a_flag(fake, spec_path, capsys):
    edit(spec_path, drop_printer)
    assert cli.main(["apply", str(spec_path), "--yes"]) == 2
    assert "--allow-destructive" in capsys.readouterr().err
    assert fake.writes == []

    assert cli.main(["apply", str(spec_path), "--yes", "--allow-destructive"]) == 0
    assert all(f["FIELD_NAME"] != "UF_CRM_PRINTER" for f in fake.userfields["deal"])


def test_unsupported_changes_block_everything(fake, spec_path, capsys):
    def change(spec):
        rename_stage(spec)
        spec.crm.deal_categories[0].stages[1].semantic = StageSemantic.failure

    edit(spec_path, change)
    assert cli.main(["apply", str(spec_path), "--yes"]) == 2
    err = capsys.readouterr().err
    assert "cannot move a stage to another group" in err
    assert "Nothing was changed" in err
    assert fake.writes == []


def test_failed_apply_saves_state_and_reports(fake, spec_path, capsys):
    fake.busy_statuses.add("C1:EXECUTING")

    def change(spec):
        rename_stage(spec)
        servis = spec.crm.deal_categories[1]
        servis.stages = [s for s in servis.stages if s.key != "v_rabote"]

    edit(spec_path, change)
    assert cli.main(["apply", str(spec_path), "--yes", "--allow-destructive"]) == 1
    captured = capsys.readouterr()
    assert "applied 1 of 2 change(s)" in captured.out
    assert "There are active items in this status." in captured.err
    assert "the portal still differs from the spec" in captured.out
    assert "- delete  crm.deal_categories.servis.stages.v_rabote" in captured.out


def test_rollback_restores_previous_portal(fake, spec_path, capsys):
    original = load_spec(spec_path)

    def change(spec):
        rename_stage(spec)
        drop_printer(spec)

    edit(spec_path, change)
    assert cli.main(["apply", str(spec_path), "--yes", "--allow-destructive"]) == 0
    assert all(f["FIELD_NAME"] != "UF_CRM_PRINTER" for f in fake.userfields["deal"])

    capsys.readouterr()
    assert cli.main(["rollback", str(spec_path), "--yes"]) == 0
    out = capsys.readouterr().out
    assert "+ create  crm.fields.deal.printer" in out
    assert "verified: the portal matches the spec" in out
    names = {s["NAME"] for s in fake.statuses}
    assert "Новая" in names and "Новая заявка" not in names
    assert any(f["EDIT_FORM_LABEL"] == "Тип принтера" for f in fake.userfields["deal"])
    assert load_spec(spec_path) == original
    # The data of the deleted field is gone for good: rollback restores structure only.


def test_rollback_without_history(fake, spec_path, capsys):
    assert cli.main(["rollback", str(spec_path), "--yes"]) == 2
    assert "no snapshots found" in capsys.readouterr().err
