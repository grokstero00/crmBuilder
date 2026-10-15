from crmbuilder.spec import StageSemantic, spec_from_yaml, spec_to_yaml
from crmbuilder_web.models import Run
from crmbuilder_web.runs import mark_interrupted

PORTAL = "test.bitrix24.ru"


def _imported(user, project) -> int:
    pid = project["id"]
    assert user.post(f"/api/projects/{pid}/import").status_code == 200
    return pid


def _edit(user, pid, change) -> None:
    spec = spec_from_yaml(user.get(f"/api/projects/{pid}/spec").json()["yaml"])
    change(spec)
    r = user.put(f"/api/projects/{pid}/spec", json={"yaml": spec_to_yaml(spec)})
    assert r.status_code == 200, r.text


def _rename_stage(spec) -> None:
    spec.crm.deal_categories[0].stages[0].name = "Новая заявка"


def _drop_printer(spec) -> None:
    spec.crm.fields["deal"] = [f for f in spec.crm.fields["deal"] if f.key != "printer"]


def _allow_write(user, pid) -> None:
    r = user.patch(f"/api/projects/{pid}", json={"allow_write": True, "confirm": PORTAL})
    assert r.status_code == 200 and r.json()["allow_write"] is True


def _plan(user, pid) -> dict:
    r = user.post(f"/api/projects/{pid}/plan")
    assert r.status_code == 200, r.text
    return r.json()


def _apply(user, pid, plan, **extra):
    body = {"confirm": PORTAL, "fingerprint": plan["fingerprint"], **extra}
    return user.post(f"/api/projects/{pid}/apply", json=body)


def test_writes_are_off_until_confirmed(user, project, fake):
    pid = _imported(user, project)
    assert project["allow_write"] is False
    _edit(user, pid, _rename_stage)
    plan = _plan(user, pid)
    r = _apply(user, pid, plan)
    assert r.status_code == 403 and "запрещены" in r.json()["detail"]

    r = user.patch(f"/api/projects/{pid}", json={"allow_write": True})
    assert r.status_code == 422 and PORTAL in r.json()["detail"]
    r = user.patch(f"/api/projects/{pid}", json={"allow_write": True, "confirm": "nope"})
    assert r.status_code == 422
    _allow_write(user, pid)

    r = user.post(f"/api/projects/{pid}/apply", json={"confirm": "wrong", "fingerprint": "x"})
    assert r.status_code == 422 and PORTAL in r.json()["detail"]
    assert fake.writes == []

    # Turning writes off needs no confirmation.
    r = user.patch(f"/api/projects/{pid}", json={"allow_write": False})
    assert r.json()["allow_write"] is False


def test_apply_and_rollback(user, project, fake):
    pid = _imported(user, project)
    _edit(user, pid, _rename_stage)
    _allow_write(user, pid)
    plan = _plan(user, pid)
    assert plan["blockers"] == []
    assert [c["address"] for c in plan["plan"]["changes"]] == [
        "crm.deal_categories.obshchaya.stages.new"
    ]

    r = _apply(user, pid, plan)
    assert r.status_code == 202, r.text
    run = user.get(f"/api/projects/{pid}/runs/{r.json()['id']}").json()
    assert run["status"] == "verified", run["error"]
    assert run["done_addresses"] == ["crm.deal_categories.obshchaya.stages.new"]
    assert run["total"] == run["done"] == 1
    assert any(e["method"] == "crm.status.update" for e in run["journal"])
    assert run["can_rollback"] is True
    assert any(s["NAME"] == "Новая заявка" for s in fake.statuses)
    assert _plan(user, pid)["plan"]["changes"] == []  # the portal matches the spec now

    # Rollback: preview, then run.
    back = user.post(f"/api/projects/{pid}/runs/{run['id']}/rollback-plan").json()
    assert [(c["action"], c["address"]) for c in back["plan"]["changes"]] == [
        ("update", "crm.deal_categories.obshchaya.stages.new")
    ]
    body = {"confirm": PORTAL, "fingerprint": back["fingerprint"]}
    r = user.post(f"/api/projects/{pid}/runs/{run['id']}/rollback", json=body)
    assert r.status_code == 202, r.text
    undo = user.get(f"/api/projects/{pid}/runs/{r.json()['id']}").json()
    assert undo["status"] == "verified" and undo["rollback_of"] == run["id"]
    assert not any(s["NAME"] == "Новая заявка" for s in fake.statuses)
    # The spec follows the restored portal.
    spec = user.get(f"/api/projects/{pid}/spec").json()["spec"]
    assert spec["crm"]["deal_categories"][0]["stages"][0]["name"] == "Новая"

    history = user.get(f"/api/projects/{pid}/runs").json()
    assert [(h["kind"], h["status"]) for h in history] == [
        ("rollback", "verified"),
        ("apply", "verified"),
    ]


def test_stale_plan_is_refused(user, project, fake):
    pid = _imported(user, project)
    _edit(user, pid, _rename_stage)
    _allow_write(user, pid)
    plan = _plan(user, pid)
    # The spec changes after the plan was shown.
    _edit(user, pid, lambda spec: setattr(spec.crm.deal_categories[1], "name", "Сервисная"))
    run = _apply(user, pid, plan).json()
    assert run["status"] == "failed"
    assert "Постройте план заново" in run["error"]
    assert fake.writes == []


def test_destructive_changes_need_their_own_consent(user, project, fake):
    pid = _imported(user, project)
    _edit(user, pid, _drop_printer)
    _allow_write(user, pid)
    plan = _plan(user, pid)
    assert plan["plan"]["changes"][0]["destructive"] is True

    run = _apply(user, pid, plan).json()
    assert run["status"] == "failed" and "опасные" in run["error"]
    assert fake.writes == []

    run = _apply(user, pid, plan, allow_destructive=True).json()
    assert run["status"] == "verified", run["error"]
    assert all(f["FIELD_NAME"] != "UF_CRM_PRINTER" for f in fake.userfields["deal"])


def test_nothing_to_do(user, project, fake):
    pid = _imported(user, project)
    _allow_write(user, pid)
    run = _apply(user, pid, _plan(user, pid)).json()
    assert run["status"] == "unchanged" and run["can_rollback"] is False
    assert fake.writes == []


def test_blockers_are_reported_and_enforced(user, project, fake):
    pid = _imported(user, project)

    def to_failure(spec):  # a stage cannot change its group in Bitrix24
        spec.crm.deal_categories[0].stages[1].semantic = StageSemantic.failure

    _edit(user, pid, to_failure)
    _allow_write(user, pid)
    plan = _plan(user, pid)
    assert plan["blockers"] and "podgotovka_dokumentov" in plan["blockers"][0]["address"]
    run = _apply(user, pid, plan).json()
    assert run["status"] == "failed" and "нельзя применить" in run["error"]
    assert fake.writes == []


def test_one_run_at_a_time_and_interrupted_runs(app, user, project):
    pid = _imported(user, project)
    _allow_write(user, pid)
    with app.state.sessionmaker() as db:
        db.add(Run(project_id=pid, kind="apply", status="running", allow_destructive=False,
                   fingerprint="x", desired_spec={}, done=[], journal=[], warnings=[]))  # fmt: skip
        db.commit()
    r = _apply(user, pid, _plan(user, pid))
    assert r.status_code == 409

    mark_interrupted(app.state.sessionmaker)
    runs = user.get(f"/api/projects/{pid}/runs").json()
    assert runs[0]["status"] == "interrupted" and "перезапущен" in runs[0]["error"]


def test_portal_failure_mid_run_keeps_state(user, project, fake):
    pid = _imported(user, project)

    def two_changes(spec):
        _rename_stage(spec)
        spec.crm.deal_categories[1].name = "Сервисная воронка"

    _edit(user, pid, two_changes)
    _allow_write(user, pid)
    plan = _plan(user, pid)

    original = fake.dispatch

    def flaky(method, params):
        from ..fake_bitrix import ApiError

        if method == "crm.status.update":
            raise ApiError("ERROR_CORE", "portal is busy")
        return original(method, params)

    fake.dispatch = flaky
    run = _apply(user, pid, plan).json()
    assert run["status"] == "failed"
    assert "portal is busy" in run["error"]
    assert run["failed"] == "crm.deal_categories.obshchaya.stages.new"
    # The category rename ran first and is recorded, so it can be rolled back.
    assert run["done_addresses"] == ["crm.deal_categories.servis"]
    assert run["can_rollback"] is True


def test_runs_belong_to_the_project_owner(app, user, project):
    from fastapi.testclient import TestClient

    pid = _imported(user, project)
    _allow_write(user, pid)
    run = _apply(user, pid, _plan(user, pid)).json()
    other = TestClient(app)
    other.post("/api/auth/register", json={"email": "o@o.kz", "password": "12345678"})
    assert other.get(f"/api/projects/{pid}/runs/{run['id']}").status_code == 404
    body = {"confirm": PORTAL, "fingerprint": "x"}
    assert other.post(f"/api/projects/{pid}/apply", json=body).status_code == 404
