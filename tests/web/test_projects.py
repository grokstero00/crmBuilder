from fastapi.testclient import TestClient

from crmbuilder.spec import spec_from_yaml, spec_to_yaml

from .conftest import WEBHOOK


def test_projects_need_login(client):
    assert client.get("/api/projects").status_code == 401
    assert client.post("/api/projects", json={"name": "x", "webhook": WEBHOOK}).status_code == 401


def test_create_and_list(user, project, app):
    assert project["portal"] == "test.bitrix24.ru"
    assert project["counts"] is None and project["imported_at"] is None
    assert [p["id"] for p in user.get("/api/projects").json()] == [project["id"]]
    # The webhook is stored encrypted and never returned.
    with app.state.sessionmaker() as db:
        from crmbuilder_web.models import Project

        stored = db.get(Project, project["id"])
        assert "abc123" not in stored.webhook_encrypted
    assert "abc123" not in user.get(f"/api/projects/{project['id']}").text


def test_webhook_is_validated(user):
    r = user.post("/api/projects", json={"name": "x", "webhook": "https://example.com/hello"})
    assert r.status_code == 422
    assert "вебхука" in r.json()["detail"]


def test_other_users_cannot_see_a_project(app, project):
    other = TestClient(app)
    other.post("/api/auth/register", json={"email": "z@z.kz", "password": "12345678"})
    assert other.get(f"/api/projects/{project['id']}").status_code == 404
    assert other.post(f"/api/projects/{project['id']}/import").status_code == 404
    assert other.get("/api/projects").json() == []


def test_import_spec_and_plan(user, project, fake, opened):
    pid = project["id"]
    assert user.get(f"/api/projects/{pid}/spec").status_code == 409

    r = user.post(f"/api/projects/{pid}/import")
    assert r.status_code == 200, r.text
    counts = r.json()["project"]["counts"]
    assert counts["deal_categories"] == 2 and counts["smart_processes"] == 1
    assert all(c.read_only for c in opened)
    assert opened and all(c._http.is_closed for c in opened)

    spec = user.get(f"/api/projects/{pid}/spec").json()
    assert spec["spec"]["portal"] == "test.bitrix24.ru"
    assert "obshchaya" in spec["yaml"]

    plan = user.post(f"/api/projects/{pid}/plan").json()
    assert plan["plan"]["changes"] == [] and plan["manual"] == []

    # Edit the desired spec: rename a stage and add a robot.
    desired = spec_from_yaml(spec["yaml"])
    desired.crm.deal_categories[0].stages[0].name = "Новая заявка"
    text = spec_to_yaml(desired) + (
        "automation:\n- key: call\n  stage: crm.deal_categories.obshchaya.stages.new\n"
        "  action: task\n  title: Перезвонить\n"
    )
    r = user.put(f"/api/projects/{pid}/spec", json={"yaml": text})
    assert r.status_code == 200, r.text

    plan = user.post(f"/api/projects/{pid}/plan").json()
    changes = plan["plan"]["changes"]
    assert [(c["action"], c["address"]) for c in changes] == [
        ("update", "crm.deal_categories.obshchaya.stages.new")
    ]
    assert plan["manual"] == [
        {"key": "call", "title": "Перезвонить", "stage": "crm.deal_categories.obshchaya.stages.new"}
    ]
    assert fake.writes == []  # plan and import never write

    # Re-import keeps the robot rule (it cannot be read from the portal).
    r = user.post(f"/api/projects/{pid}/import")
    assert r.json()["project"]["counts"]["automation"] == 1


def test_bad_spec_is_rejected(user, project):
    pid = project["id"]
    user.post(f"/api/projects/{pid}/import")
    r = user.put(f"/api/projects/{pid}/spec", json={"yaml": "portal: [unclosed"})
    assert r.status_code == 422 and "YAML" in r.json()["detail"]
    r = user.put(f"/api/projects/{pid}/spec", json={"yaml": "portal: other.bitrix24.ru\n"})
    assert r.status_code == 422 and "other.bitrix24.ru" in r.json()["detail"]
    r = user.put(f"/api/projects/{pid}/spec", json={"yaml": "portal: test.bitrix24.ru\nfoo: 1\n"})
    assert r.status_code == 422 and "проверку" in r.json()["detail"]


def test_portal_errors_are_reported(user, project, fake):
    def boom(method, params):
        from ..fake_bitrix import ApiError

        raise ApiError("INSUFFICIENT_SCOPE", "no crm scope")

    fake.dispatch = boom
    r = user.post(f"/api/projects/{project['id']}/import")
    assert r.status_code == 502
    assert "INSUFFICIENT_SCOPE" in r.json()["detail"]


def test_update_and_delete(user, project):
    pid = project["id"]
    r = user.patch(f"/api/projects/{pid}", json={"name": "Клиника"})
    assert r.json()["name"] == "Клиника"
    user.post(f"/api/projects/{pid}/import")
    other_portal = "https://other.bitrix24.ru/rest/1/zzz/"
    assert user.patch(f"/api/projects/{pid}", json={"webhook": other_portal}).status_code == 409
    new_hook = "https://test.bitrix24.ru/rest/7/newtoken/"
    assert user.patch(f"/api/projects/{pid}", json={"webhook": new_hook}).status_code == 200
    assert user.delete(f"/api/projects/{pid}").status_code == 204
    assert user.get(f"/api/projects/{pid}").status_code == 404
