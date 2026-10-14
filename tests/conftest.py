"""A fake Bitrix24 portal answering REST calls through httpx.MockTransport."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from crmbuilder.bitrix import BitrixClient

WEBHOOK = "https://clinic.bitrix24.ru/rest/1/secret/"

Handler = Callable[[dict[str, Any]], dict[str, Any]]


def paginate(items: list[dict], params: dict, page: int = 2) -> dict:
    start = int(params.get("start", 0))
    body: dict[str, Any] = {"result": items[start : start + page], "total": len(items)}
    if start + page < len(items):
        body["next"] = start + page
    return body


class FakePortal:
    def __init__(self) -> None:
        self.handlers: dict[str, Handler] = {}
        self.calls: list[tuple[str, dict]] = []

    def on(self, method: str, handler: Handler) -> None:
        self.handlers[method] = handler

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
            params = json.loads(request.content or b"{}")
            self.calls.append((method, params))
            handler = self.handlers.get(method)
            if handler is None:
                return httpx.Response(
                    400, json={"error": "ERROR_METHOD_NOT_FOUND", "error_description": method}
                )
            return httpx.Response(200, json=handler(params))

        return httpx.MockTransport(handle)

    def client(self) -> BitrixClient:
        return BitrixClient(WEBHOOK, transport=self.transport(), sleep=lambda _: None)


DEAL_STAGES = {
    "DEAL_STAGE": [
        {"ID": "1", "STATUS_ID": "NEW", "NAME": "Новая", "SORT": "10", "SEMANTICS": None},
        {"ID": "2", "STATUS_ID": "WON", "NAME": "Сделка успешна", "SORT": "20", "SEMANTICS": "S"},
        {"ID": "3", "STATUS_ID": "LOSE", "NAME": "Провал", "SORT": "30", "SEMANTICS": "F"},
    ],
    "DEAL_STAGE_1": [
        {"ID": "10", "STATUS_ID": "C1:NEW", "NAME": "Новая заявка", "SORT": "10",
         "EXTRA": {"SEMANTICS": "process", "COLOR": "#39A8EF"}},
        {"ID": "11", "STATUS_ID": "C1:UC_X7K2P1", "NAME": "Записан", "SORT": "20",
         "EXTRA": {"SEMANTICS": "process"}},
        {"ID": "12", "STATUS_ID": "C1:WON", "NAME": "Пришёл", "SORT": "30",
         "EXTRA": {"SEMANTICS": "success"}},
        {"ID": "13", "STATUS_ID": "C1:LOSE", "NAME": "Не пришёл", "SORT": "40",
         "EXTRA": {"SEMANTICS": "failure"}},
    ],
    "DYNAMIC_1036_STAGE_7": [
        {"ID": "20", "STATUS_ID": "DT1036_7:NEW", "NAME": "Черновик", "SORT": "10"},
        {"ID": "21", "STATUS_ID": "DT1036_7:SUCCESS", "NAME": "Утверждён", "SORT": "20",
         "SEMANTICS": "S"},
    ],
}  # fmt: skip

DEAL_FIELDS = [
    {"ID": "57", "FIELD_NAME": "UF_CRM_DOCTOR", "USER_TYPE_ID": "employee", "MULTIPLE": "N",
     "MANDATORY": "Y", "SORT": "100", "EDIT_FORM_LABEL": "Врач", "SETTINGS": {}},
    {"ID": "58", "FIELD_NAME": "UF_CRM_1712345678", "USER_TYPE_ID": "enumeration",
     "MULTIPLE": "N", "MANDATORY": "N", "SORT": "200", "EDIT_FORM_LABEL": "Источник записи",
     "SETTINGS": {"DISPLAY": "LIST", "LIST_HEIGHT": 1, "CAPTION_NO_VALUE": ""},
     "LIST": [
         {"ID": "1", "VALUE": "Сайт", "DEF": "Y", "SORT": "10",
          "XML_ID": "0c8c2f1b3e5a4d6f8a9b0c1d2e3f4a5b"},
         {"ID": "2", "VALUE": "Звонок", "DEF": "N", "SORT": "20", "XML_ID": "PHONE"},
     ]},
    {"ID": "59", "FIELD_NAME": "UF_CRM_1712345999", "USER_TYPE_ID": "resourcebooking",
     "MULTIPLE": "N", "MANDATORY": "N", "SORT": "300", "EDIT_FORM_LABEL": {"ru": "Бронь кабинета"}},
]  # fmt: skip


@pytest.fixture
def portal() -> FakePortal:
    fake = FakePortal()

    def categories(params: dict) -> dict:
        data = {
            2: [
                {"id": 1, "name": "Первичный приём", "sort": 200, "isDefault": "N"},
                {"id": 0, "name": "Общая", "sort": 100, "isDefault": "Y"},
            ],
            1036: [{"id": 7, "name": "Основное", "sort": 100, "isDefault": "Y"}],
        }
        return {"result": {"categories": data.get(params["entityTypeId"], [])}}

    def statuses(params: dict) -> dict:
        return paginate(DEAL_STAGES.get(params["filter"]["ENTITY_ID"], []), params)

    fake.on("crm.category.list", categories)
    fake.on("crm.status.list", statuses)
    fake.on("crm.deal.userfield.list", lambda p: paginate(DEAL_FIELDS, p))
    fake.on("crm.contact.userfield.list", lambda p: {"result": []})
    fake.on("crm.company.userfield.list", lambda p: {"result": []})
    # Lead userfields are left unregistered to exercise the "skipped" warning path.
    fake.on(
        "crm.type.list",
        lambda p: {
            "result": {
                "types": [
                    {"id": 3, "title": "План лечения", "code": "", "entityTypeId": 1036,
                     "isCategoriesEnabled": "N", "isStagesEnabled": "Y", "isClientEnabled": "Y"}
                ]
            },
            "total": 1,
        },
    )  # fmt: skip
    fake.on(
        "department.get",
        lambda p: paginate(
            [
                {"ID": "1", "NAME": "Клиника", "SORT": "500"},
                {"ID": "4", "NAME": "Регистратура", "SORT": "500", "PARENT": "1"},
                {"ID": "5", "NAME": "Врачи", "SORT": "500", "PARENT": "1"},
            ],
            p,
        ),
    )
    return fake
