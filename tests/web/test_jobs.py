import json
import threading
import time
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient

from crmbuilder.design import NOT_AVAILABLE_ANSWER, PRICES
from crmbuilder.spec import spec_from_yaml, spec_to_yaml
from crmbuilder_web.jobs import mark_jobs_interrupted
from crmbuilder_web.models import Job, Run

from .conftest import WEBHOOK

PORTAL = "test.bitrix24.ru"

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
CREATE_TENDER = {"address": "crm.deal_categories.tender", "object": TENDER}


# --- scripted model answers ------------------------------------------------------------


def text(value):
    return SimpleNamespace(type="text", text=value)


def tool(name, tool_input, block_id=None):
    return SimpleNamespace(type="tool_use", id=block_id or f"tu_{name}", name=name,
                           input=tool_input)  # fmt: skip


def response(stop_reason, *content):
    usage = SimpleNamespace(input_tokens=1000, output_tokens=200, cache_read_input_tokens=0,
                            cache_creation_input_tokens=0)  # fmt: skip
    return SimpleNamespace(stop_reason=stop_reason, content=list(content), usage=usage,
                           stop_details=None)  # fmt: skip


def json_answer(payload):
    return response("end_turn", text(json.dumps(payload, ensure_ascii=False)))


def api_error(cls, status_code):
    import anthropic
    import httpx2

    request = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    if cls is anthropic.APIConnectionError:
        return cls(request=request)
    return cls("boom", response=httpx2.Response(status_code, request=request), body=None)


# --- helpers ---------------------------------------------------------------------------


def _imported(user, project) -> int:
    pid = project["id"]
    assert user.post(f"/api/projects/{pid}/import").status_code == 200
    return pid


def _design(user, pid, **body):
    body = {"brief": "Нужна воронка для тендеров", "questions": False, **body}
    return user.post(f"/api/projects/{pid}/design", json=body)


def _job(user, pid, job_id) -> dict:
    r = user.get(f"/api/projects/{pid}/jobs/{job_id}")
    assert r.status_code == 200, r.text
    return r.json()


def _wait(user, pid, job_id, *statuses, timeout=10.0) -> dict:
    deadline = time.monotonic() + timeout
    while True:
        job = _job(user, pid, job_id)
        if job["status"] in statuses or time.monotonic() > deadline:
            assert job["status"] in statuses, job
            return job
        time.sleep(0.01)


def _edit(user, pid, change) -> None:
    spec = spec_from_yaml(user.get(f"/api/projects/{pid}/spec").json()["yaml"])
    change(spec)
    r = user.put(f"/api/projects/{pid}/spec", json={"yaml": spec_to_yaml(spec)})
    assert r.status_code == 200, r.text


def _apply(user, pid, change) -> dict:
    _edit(user, pid, change)
    plan = user.post(f"/api/projects/{pid}/plan").json()
    body = {"confirm": PORTAL, "fingerprint": plan["fingerprint"]}
    r = user.post(f"/api/projects/{pid}/apply", json=body)
    assert r.status_code == 202, r.text
    run = user.get(f"/api/projects/{pid}/runs/{r.json()['id']}").json()
    assert run["status"] == "verified", run["error"]
    return run


def _rename_stage(spec) -> None:
    spec.crm.deal_categories[0].stages[0].name = "Новая заявка"


def _rename_service(spec) -> None:
    spec.crm.deal_categories[1].name = "Сервисная воронка"


@pytest.fixture
def threaded(app):
    """Jobs run in a real thread, as on the server, and look for answers every 10 ms."""
    threads: list[threading.Thread] = []

    def executor(job):
        thread = threading.Thread(target=job, daemon=True)
        thread.start()
        threads.append(thread)

    app.state.run_executor = executor
    app.state.job_runner.poll_interval = 0.01
    app.state.job_runner.answer_timeout = 10
    yield threads
    for thread in threads:
        thread.join(timeout=10)


# --- AI settings -----------------------------------------------------------------------


def test_ai_config(app, user, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    monkeypatch.delenv("CRMBUILDER_WEB_MODEL", raising=False)
    config = user.get("/api/ai").json()
    assert config["available"] is False
    assert config["default_model"] == "claude-haiku-5-5"
    assert [m["id"] for m in config["models"]] == [
        "claude-haiku-5-5",
        "claude-sonnet-5-5",
        "claude-opus-5-5",
    ]
    assert all(m["id"] in PRICES and m["label"] for m in config["models"])

    monkeypatch.setenv("ANTHROPIC_API_KEY", "  ")
    assert user.get("/api/ai").json()["available"] is False
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    monkeypatch.setenv("CRMBUILDER_WEB_MODEL", "claude-opus-5-5")
    config = user.get("/api/ai").json()
    assert config["available"] is True and config["default_model"] == "claude-opus-5-5"
    monkeypatch.setenv("CRMBUILDER_WEB_MODEL", "gpt-5")
    assert user.get("/api/ai").json()["default_model"] == "claude-haiku-5-5"

    assert TestClient(app).get("/api/ai").status_code == 401


# --- designer --------------------------------------------------------------------------


def test_design_then_save(ai, user, project, claude, fake):
    pid = _imported(user, project)
    claude.queue(
        # Questions are off: the model is told to assume, and its edit goes through.
        response(
            "tool_use",
            tool("ask_user", {"questions": ["Какие этапы?"]}, "tu_q"),
            tool("create", CREATE_TENDER, "tu_c"),
        ),
        response("end_turn", text("Добавил воронку «Тендеры».")),
    )
    r = _design(user, pid)
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "done", job["error"]
    assert job["title"] == "Нужна воронка для тендеров"
    assert job["params"] == {"brief": "Нужна воронка для тендеров",
                             "model": "claude-haiku-5-5", "questions": False}  # fmt: skip
    result = job["result"]
    assert result["summary"] == "Добавил воронку «Тендеры»."
    assert result["edits"] == ["create crm.deal_categories.tender"]
    assert [c["address"] for c in result["plan"]["changes"]][0] == "crm.deal_categories.tender"
    assert result["described"][0]["section"] == "Воронки и стадии"
    assert result["described"][0]["text"].startswith("Создана воронка «Тендеры»: Новый тендер")
    assert result["spec"]["portal"] == PORTAL
    assert [e["type"] for e in job["log"]] == ["info", "edit"]
    assert job["log"][1]["text"] == "create crm.deal_categories.tender"
    assert job["usage"]["requests"] == 2 and job["usage"]["model"] == "claude-haiku-5-5"
    assert job["cost_usd"] == pytest.approx(job["usage"]["cost_usd"]) and job["cost_usd"] > 0
    assert job["saved"] is False and job["questions"] is None

    request = claude.requests[1]
    assert "fallbacks" not in request  # Haiku
    results = request["messages"][-1]["content"]
    assert results[0]["content"] == NOT_AVAILABLE_ANSWER
    assert claude.closed == 1

    # Nothing changes until the result is saved.
    spec = user.get(f"/api/projects/{pid}/spec").json()["spec"]
    assert "tender" not in {c["key"] for c in spec["crm"]["deal_categories"]}
    r = user.post(f"/api/projects/{pid}/jobs/{job['id']}/save")
    assert r.status_code == 200, r.text
    assert r.json()["project"]["counts"]["deal_categories"] == 3
    spec = user.get(f"/api/projects/{pid}/spec").json()["spec"]
    assert "tender" in {c["key"] for c in spec["crm"]["deal_categories"]}
    plan = user.post(f"/api/projects/{pid}/plan").json()
    assert ("create", "crm.deal_categories.tender") in {
        (c["action"], c["address"]) for c in plan["plan"]["changes"]
    }
    assert fake.writes == []

    assert _job(user, pid, job["id"])["saved"] is True
    r = user.post(f"/api/projects/{pid}/jobs/{job['id']}/save")
    assert r.status_code == 409 and "уже сохранён" in r.json()["detail"]
    r = user.get(f"/api/projects/{pid}/jobs/{job['id']}/download")
    assert r.status_code == 409

    listed = user.get(f"/api/projects/{pid}/jobs?kind=design").json()
    assert [(j["id"], j["status"]) for j in listed] == [(job["id"], "done")]
    assert listed[0]["cost_usd"] == job["cost_usd"]
    assert user.get(f"/api/projects/{pid}/jobs?kind=audit").json() == []
    assert user.get(f"/api/projects/{pid}/jobs?kind=nope").status_code == 422


def test_designer_questions_are_answered_while_it_waits(ai, user, project, claude, threaded):
    pid = _imported(user, project)
    questions = ["Сколько этапов у тендера?", "Нужны ли роботы?"]
    claude.queue(
        response("tool_use", tool("ask_user", {"questions": questions})),
        response("tool_use", tool("create", CREATE_TENDER)),
        response("end_turn", text("Готово.")),
    )
    r = _design(user, pid, questions=True)
    assert r.status_code == 202, r.text
    job_id = r.json()["id"]

    waiting = _wait(user, pid, job_id, "waiting")
    assert waiting["questions"] == questions
    assert waiting["log"][-1] == {"type": "questions", "text": "\n".join(questions),
                                  "items": questions}  # fmt: skip
    url = f"/api/projects/{pid}/jobs/{job_id}/answers"
    r = user.post(url, json={"answers": ["Четыре"]})
    assert r.status_code == 422 and "Нужно ответов: 2" in r.json()["detail"]
    r = user.post(url, json={"answers": [" Четыре ", ""]})
    assert r.status_code == 200, r.text
    assert r.json()["questions"] is None  # answered, the job picks the answers up
    assert user.post(url, json={"answers": ["a", "b"]}).status_code == 409

    done = _wait(user, pid, job_id, "done", "failed")
    assert done["status"] == "done", done["error"]
    assert [e["type"] for e in done["log"]] == ["info", "questions", "answers", "edit"]
    assert done["log"][2]["items"] == ["Четыре", ""]
    sent = claude.requests[1]["messages"][-1]["content"][0]["content"]
    assert "A: Четыре" in sent and "(no answer, decide yourself)" in sent
    assert done["result"]["edits"] == ["create crm.deal_categories.tender"]
    assert user.post(url, json={"answers": ["a", "b"]}).status_code == 409


def test_cancel_while_waiting(ai, user, project, claude, threaded):
    pid = _imported(user, project)
    claude.queue(response("tool_use", tool("ask_user", {"questions": ["Какие этапы?"]})))
    job_id = _design(user, pid, questions=True).json()["id"]
    _wait(user, pid, job_id, "waiting")

    r = user.post(f"/api/projects/{pid}/jobs/{job_id}/cancel")
    assert r.status_code == 200, r.text
    job = _wait(user, pid, job_id, "cancelled")
    assert "Остановлено" in job["error"] and job["finished_at"] and job["questions"] is None
    assert job["result"] is None and len(claude.requests) == 1
    assert user.post(f"/api/projects/{pid}/jobs/{job_id}/cancel").status_code == 409
    r = user.post(f"/api/projects/{pid}/jobs/{job_id}/answers", json={"answers": ["x"]})
    assert r.status_code == 409 and "не ждёт" in r.json()["detail"]


def test_cancel_stops_before_the_next_model_call(ai, app, user, project, claude):
    pid = _imported(user, project)
    claude.queue(response("tool_use", tool("create", CREATE_TENDER)))
    runner = app.state.job_runner
    original = runner._on_edit

    def edit_then_cancel(db, job, line):
        # The person presses «Отменить» while the model is working.
        with app.state.sessionmaker() as other:
            other.get(Job, job.id).cancel_requested = True
            other.commit()
        original(db, job, line)

    runner._on_edit = edit_then_cancel
    job = _design(user, pid).json()
    assert job["status"] == "cancelled"
    assert [e["type"] for e in job["log"]] == ["info", "edit"]
    assert len(claude.requests) == 1


def test_unanswered_questions_time_out(ai, app, user, project, claude):
    pid = _imported(user, project)
    app.state.job_runner.answer_timeout = 0
    claude.queue(
        response("tool_use", tool("ask_user", {"questions": ["Какие этапы?"]})),
        response("end_turn", text("Решил сам.")),
    )
    job = _design(user, pid, questions=True).json()
    assert job["status"] == "done", job["error"]
    assert job["log"][-1] == {"type": "info", "text": "Ответов не дождались: модель решит сама"}
    sent = claude.requests[1]["messages"][-1]["content"][0]["content"]
    assert "(no answer, decide yourself)" in sent


def test_save_is_refused_after_the_spec_changed(ai, user, project, claude):
    pid = _imported(user, project)
    claude.queue(response("tool_use", tool("create", CREATE_TENDER)),
                 response("end_turn", text("Ок")))  # fmt: skip
    job = _design(user, pid).json()
    assert job["status"] == "done"
    _edit(user, pid, _rename_service)
    r = user.post(f"/api/projects/{pid}/jobs/{job['id']}/save")
    assert r.status_code == 409 and "запустите его заново" in r.json()["detail"]
    spec = user.get(f"/api/projects/{pid}/spec").json()["spec"]
    assert "tender" not in {c["key"] for c in spec["crm"]["deal_categories"]}


def test_design_needs_a_spec_a_key_and_a_known_model(user, project, claude, monkeypatch):
    pid = project["id"]
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    r = _design(user, pid)
    assert r.status_code == 409 and "импортируйте" in r.json()["detail"]
    _imported(user, project)
    r = _design(user, pid)
    assert r.status_code == 503 and "ANTHROPIC_API_KEY" in r.json()["detail"]
    r = user.post(f"/api/projects/{pid}/audit", json={"ai": True})
    assert r.status_code == 503
    body = {"ai": True, "brief": "ТЗ"}
    assert user.post(f"/api/projects/{pid}/report", json=body).status_code == 503
    assert claude.requests == []
    # Without AI the audit and the report still work.
    assert user.post(f"/api/projects/{pid}/audit", json={}).json()["status"] == "done"
    assert user.post(f"/api/projects/{pid}/report", json={}).json()["status"] == "done"

    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-x")
    r = _design(user, pid, model="gpt-5")
    assert r.status_code == 422 and "Неизвестная модель" in r.json()["detail"]
    r = _design(user, pid, brief="   ")
    assert r.status_code == 422
    r = _design(user, pid, brief="")
    assert r.status_code == 422 and r.json()["detail"].startswith("ТЗ:")
    r = user.post(f"/api/projects/{pid}/jobs/1/answers", json={"answers": ["x" * 5001]})
    assert r.status_code == 422 and r.json()["detail"].startswith("Ответы:")
    assert claude.requests == []


@pytest.mark.parametrize(
    "error, message",
    [
        (("AuthenticationError", 401), "Ключ Anthropic API не задан или неверен"),
        (("RateLimitError", 429), "Превышен лимит запросов Anthropic"),
        (("InternalServerError", 500), "Ошибка Anthropic API 500"),
        (("APIConnectionError", 0), "Нет связи с Anthropic API"),
    ],
)
def test_anthropic_errors_are_explained(ai, user, project, claude, error, message):
    import anthropic

    pid = _imported(user, project)
    claude.queue(api_error(getattr(anthropic, error[0]), error[1]))
    job = _design(user, pid).json()
    assert job["status"] == "failed" and job["error"].startswith(message), job["error"]
    assert claude.closed == 1


def test_model_failures_are_explained(ai, user, project, claude):
    pid = _imported(user, project)
    claude.queue(response("max_tokens"))
    job = _design(user, pid).json()
    assert job["status"] == "failed"
    assert job["error"].startswith("Модель не справилась:") and "max_tokens" in job["error"]


def test_one_design_at_a_time_and_interrupted_jobs(ai, app, user, project, claude):
    pid = _imported(user, project)
    with app.state.sessionmaker() as db:
        db.add_all([
            Job(project_id=pid, kind="design", status="waiting", params={"brief": "x"},
                questions=["Какие этапы?"], log=[]),
            Job(project_id=pid, kind="audit", status="running", params={}, log=[]),
        ])  # fmt: skip
        db.commit()
    r = _design(user, pid)
    assert r.status_code == 409 and "уже работает" in r.json()["detail"]
    # Only the designer is limited to one at a time.
    assert user.post(f"/api/projects/{pid}/audit", json={}).status_code == 202

    mark_jobs_interrupted(app.state.sessionmaker)
    jobs = user.get(f"/api/projects/{pid}/jobs").json()
    assert [j["status"] for j in jobs] == ["done", "interrupted", "interrupted"]
    assert all("перезапущен" in j["error"] and j["finished_at"] for j in jobs[1:])
    assert _job(user, pid, jobs[2]["id"])["questions"] is None

    claude.queue(response("end_turn", text("Ничего менять не нужно.")))
    assert _design(user, pid).json()["status"] == "done"


# --- audit -----------------------------------------------------------------------------


def test_audit_by_the_rules(user, project, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    pid = _imported(user, project)
    r = user.post(f"/api/projects/{pid}/audit", json={})
    assert r.status_code == 202, r.text
    job = r.json()
    assert job["status"] == "done", job["error"]
    assert job["title"] == "Аудит" and job["usage"] is None and job["cost_usd"] is None
    result = job["result"]
    assert result["counts"] == {"error": 0, "warning": 1, "info": 1}
    assert [f["code"] for f in result["findings"]] == ["standard_duplicate", "similar_pipelines"]
    assert result["findings"][0]["source"] == "rules"
    assert result["business_guess"] == ""
    assert result["markdown"].startswith(f"# Аудит портала {PORTAL}")

    r = user.get(f"/api/projects/{pid}/jobs/{job['id']}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    day = job["finished_at"][:10]
    assert r.headers["content-disposition"] == f'attachment; filename="audit-{PORTAL}-{day}.md"'
    assert r.text == result["markdown"]


def test_audit_with_ai(ai, user, project, claude):
    pid = _imported(user, project)
    claude.queue(json_answer({
        "business_guess": "Сервисная компания",
        "findings": [{"severity": "warning", "address": "crm.fields.deal.printer",
                      "title": "Поле «Принтер» не связано с бизнесом",
                      "recommendation": "Удалите, если не используется."}],
    }))  # fmt: skip
    body = {"ai": True, "context": "Ремонтируем технику", "model": "claude-sonnet-5-5"}
    job = user.post(f"/api/projects/{pid}/audit", json=body).json()
    assert job["status"] == "done", job["error"]
    assert job["title"] == "Аудит с ИИ"
    result = job["result"]
    assert result["business_guess"] == "Сервисная компания"
    assert result["counts"] == {"error": 0, "warning": 2, "info": 1}
    ai_findings = [f for f in result["findings"] if f["source"] == "ai"]
    assert ai_findings == [{"severity": "warning", "code": "ai",
                            "address": "crm.fields.deal.printer",
                            "message": "Поле «Принтер» не связано с бизнесом",
                            "hint": "Удалите, если не используется.", "source": "ai"}]  # fmt: skip
    assert "*(ИИ)*" in result["markdown"] and "Сервисная компания" in result["markdown"]
    assert job["usage"]["model"] == "claude-sonnet-5-5"
    assert job["cost_usd"] == pytest.approx((1000 * 2 + 200 * 10) / 1e6)

    request = claude.requests[0]
    assert request["model"] == "claude-sonnet-5-5" and request["fallbacks"] == "default"
    assert request["output_config"]["format"]["type"] == "json_schema"
    assert "Ремонтируем технику" in request["messages"][0]["content"][2]["text"]


def test_audit_ai_answer_that_is_not_json(ai, user, project, claude):
    pid = _imported(user, project)
    claude.queue(response("end_turn", text("not json")))
    job = user.post(f"/api/projects/{pid}/audit", json={"ai": True}).json()
    assert job["status"] == "failed" and job["error"].startswith("Модель не справилась")


# --- report ----------------------------------------------------------------------------

MANUAL = {
    "summary": "Портал готов к работе с тендерами.",
    "manual_steps": [{"title": "Права доступа", "why": "Менеджеры видят только свои сделки.",
                      "steps": ["Откройте CRM → Настройки → Права доступа."]}],
}  # fmt: skip


def test_report_counts_changes_since_the_first_run(ai, app, user, project, claude):
    pid = _imported(user, project)
    user.patch(f"/api/projects/{pid}", json={"allow_write": True, "confirm": PORTAL})
    first = _apply(user, pid, _rename_stage)
    second = _apply(user, pid, _rename_service)

    job = user.post(f"/api/projects/{pid}/report", json={}).json()
    assert job["status"] == "done", job["error"]
    assert job["title"] == "Отчёт" and job["usage"] is None
    result = job["result"]
    assert result["changes"] == 2 and result["manual_steps"] == 0
    assert result["since_run_id"] == first["id"]
    assert "## Что сделано" in result["markdown"]
    assert "стадия «Новая» переименована в «Новая заявка»" in result["markdown"]
    assert "Воронка «Сервис» переименована в «Сервисная воронка»." in result["markdown"]

    body = {"since_run_id": second["id"]}
    result = user.post(f"/api/projects/{pid}/report", json=body).json()["result"]
    assert result["changes"] == 1 and result["since_run_id"] == second["id"]
    assert "«Новая заявка»" not in result["markdown"]

    assert user.post(f"/api/projects/{pid}/report", json={"since_run_id": 999}).status_code == 404
    with app.state.sessionmaker() as db:  # a run that never read the portal
        run = Run(project_id=pid, kind="apply", status="failed", allow_destructive=False,
                  fingerprint="x", desired_spec={}, done=[], journal=[], warnings=[])  # fmt: skip
        db.add(run)
        db.commit()
        unread = run.id
    r = user.post(f"/api/projects/{pid}/report", json={"since_run_id": unread})
    assert r.status_code == 404

    # Manual steps by Claude need the brief.
    r = user.post(f"/api/projects/{pid}/report", json={"ai": True, "brief": "  "})
    assert r.status_code == 422 and "нужен текст ТЗ" in r.json()["detail"]
    claude.queue(json_answer(MANUAL))
    body = {"ai": True, "brief": "Нужны тендеры и права доступа", "title": "Сдача проекта"}
    job = user.post(f"/api/projects/{pid}/report", json=body).json()
    assert job["status"] == "done", job["error"]
    assert job["title"] == "Сдача проекта" and job["cost_usd"] > 0
    result = job["result"]
    assert result["manual_steps"] == 1 and result["changes"] == 2
    assert result["markdown"].startswith("# Сдача проекта")
    assert "## Что нужно настроить вручную" in result["markdown"]
    assert "Нужны тендеры" in claude.requests[0]["messages"][0]["content"][0]["text"]

    r = user.get(f"/api/projects/{pid}/jobs/{job['id']}/download")
    day = job["finished_at"][:10]
    assert r.headers["content-disposition"] == f'attachment; filename="report-{PORTAL}-{day}.md"'
    assert r.text.startswith("# Сдача проекта")


def test_report_describes_the_structure_without_runs(app, user, project):
    pid = _imported(user, project)
    job = user.post(f"/api/projects/{pid}/report", json={"title": "  "}).json()
    assert job["status"] == "done", job["error"]
    result = job["result"]
    assert result == {**result, "changes": 0, "manual_steps": 0, "since_run_id": None}
    assert "## Что сделано" not in result["markdown"]
    assert "## Структура CRM" in result["markdown"]

    r = user.get(f"/api/projects/{pid}/jobs/{job['id']}/download")
    assert r.status_code == 200
    assert r.headers["content-type"] == "text/markdown; charset=utf-8"
    assert r.headers["content-disposition"].startswith('attachment; filename="report-')

    # Jobs go with their project.
    assert user.delete(f"/api/projects/{pid}").status_code == 204
    with app.state.sessionmaker() as db:
        assert db.query(Job).count() == 0


# --- access ----------------------------------------------------------------------------


def test_jobs_belong_to_the_project_owner(ai, app, user, project, claude):
    pid = _imported(user, project)
    job = user.post(f"/api/projects/{pid}/audit", json={}).json()
    base = f"/api/projects/{pid}"

    other = TestClient(app)
    other.post("/api/auth/register", json={"email": "o@o.kz", "password": "12345678"})
    assert other.get(f"{base}/jobs").status_code == 404
    assert other.get(f"{base}/jobs/{job['id']}").status_code == 404
    assert other.get(f"{base}/jobs/{job['id']}/download").status_code == 404
    assert other.post(f"{base}/jobs/{job['id']}/cancel").status_code == 404
    assert other.post(f"{base}/jobs/{job['id']}/save").status_code == 404
    assert other.post(f"{base}/design", json={"brief": "x"}).status_code == 404
    assert other.post(f"{base}/audit", json={}).status_code == 404

    # A job is reached only through its own project.
    r = user.post("/api/projects", json={"name": "Второй", "webhook": WEBHOOK})
    second = r.json()["id"]
    assert user.get(f"/api/projects/{second}/jobs/{job['id']}").status_code == 404
    assert claude.requests == []


def test_design_of_robot_rules_only_can_be_saved(ai, user, project, claude):
    # The portal plan ignores `automation`, but the result is still an edit worth saving.
    pid = _imported(user, project)
    rule = {"stage": "crm.deal_categories.obshchaya.stages.new", "action": "task",
            "title": "Позвонить клиенту"}  # fmt: skip
    claude.queue(
        response("tool_use", tool("create", {"address": "automation.call", "object": rule})),
        response("end_turn", text("Добавил робота.")),
    )
    job = _design(user, pid).json()
    assert job["status"] == "done", job["error"]
    assert job["result"]["edits"] == ["create automation.call"]
    assert job["result"]["plan"]["changes"] == []
    assert user.post(f"/api/projects/{pid}/jobs/{job['id']}/save").status_code == 200
    spec = user.get(f"/api/projects/{pid}/spec").json()["spec"]
    assert [r["key"] for r in spec["automation"]] == ["call"]


def test_a_result_that_cannot_be_stored_still_ends_the_job(ai, app, user, project, claude):
    # E.g. PostgreSQL rejects NUL in JSONB: the final commit fails, the job must not hang.
    pid = _imported(user, project)
    runner = app.state.job_runner
    runner._audit = lambda db, job: {"bad": {1, 2}}  # not JSON-serialisable
    job = user.post(f"/api/projects/{pid}/audit", json={}).json()
    assert job["status"] == "failed" and job["finished_at"]
    assert "Внутренняя ошибка" in job["error"]
    # Nothing is left running.
    assert user.post(f"/api/projects/{pid}/audit", json={}).status_code == 202


def test_runs_say_whether_they_can_be_a_report_base(app, user, project):
    pid = _imported(user, project)
    user.patch(f"/api/projects/{pid}", json={"allow_write": True, "confirm": PORTAL})
    run = _apply(user, pid, _rename_stage)
    with app.state.sessionmaker() as db:
        db.add(Run(project_id=pid, kind="apply", status="failed", allow_destructive=False,
                   fingerprint="x", desired_spec={}, done=[], journal=[], warnings=[]))  # fmt: skip
        db.commit()
    listed = {r["id"]: r["has_before"] for r in user.get(f"/api/projects/{pid}/runs").json()}
    assert listed[run["id"]] is True
    assert sorted(listed.values()) == [False, True]
