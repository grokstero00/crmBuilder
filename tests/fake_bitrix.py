"""An in-memory Bitrix24 portal that supports the read and write methods we use.

Behaviour follows the REST documentation where it matters for `apply`:
new pipelines get default stages, stage codes of non-default pipelines get the
``C<id>:`` / ``DT<type>_<id>:`` prefix, stages and pipelines with items cannot be
deleted, the default pipeline cannot be deleted, user field codes get the
``UF_CRM_`` prefix and enumeration values are edited by ``ID`` / ``DEL``.
"""

from __future__ import annotations

import copy
import json
import re
from typing import Any

import httpx

from crmbuilder.bitrix import BitrixClient

WEBHOOK = "https://test.bitrix24.ru/rest/1/secret/"

_DEAL_DEFAULT_STAGES = [
    ("NEW", "Новая", ""),
    ("PREPARATION", "Подготовка документов", ""),
    ("PREPAYMENT_INVOICE", "Счёт на предоплату", ""),
    ("EXECUTING", "В работе", ""),
    ("FINAL_INVOICE", "Финальный счёт", ""),
    ("WON", "Сделка успешна", "S"),
    ("LOSE", "Сделка провалена", "F"),
    ("APOLOGY", "Анализ причины провала", "F"),
]
_SMART_DEFAULT_STAGES = [
    ("NEW", "Новый", ""),
    ("PREPARATION", "Подготовка", ""),
    ("CLIENT", "Согласование", ""),
    ("SUCCESS", "Успех", "S"),
    ("FAIL", "Провал", "F"),
]
_USERFIELD_ENTITIES = ("lead", "deal", "contact", "company")


class ApiError(Exception):
    def __init__(self, code: str, description: str = "") -> None:
        super().__init__(code)
        self.code = code
        self.description = description


class FakeBitrix:
    def __init__(self) -> None:
        self.categories: dict[int, list[dict]] = {2: []}
        self.statuses: list[dict] = []
        self.userfields: dict[str, list[dict]] = {e: [] for e in _USERFIELD_ENTITIES}
        self.types: list[dict] = []
        self.departments: list[dict] = []
        # Stage codes / (entityTypeId, category id) that hold CRM items.
        self.busy_statuses: set[str] = set()
        self.busy_categories: set[tuple[int, int]] = set()
        self.busy_types: set[int] = set()  # entityTypeIds of smart processes with items
        self.calls: list[tuple[str, dict]] = []
        self._next_id = 1000

    # --- helpers --------------------------------------------------------------

    def _id(self) -> int:
        self._next_id += 1
        return self._next_id

    @property
    def writes(self) -> list[tuple[str, dict]]:
        return [c for c in self.calls if c[0].rsplit(".", 1)[-1] not in ("list", "get")]

    def client(self) -> BitrixClient:
        return BitrixClient(WEBHOOK, transport=self.transport(), sleep=lambda _: None)

    def writable_client(self) -> BitrixClient:
        return BitrixClient(
            WEBHOOK, transport=self.transport(), sleep=lambda _: None, read_only=False
        )

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            method = request.url.path.rsplit("/", 1)[-1].removesuffix(".json")
            params = json.loads(request.content or b"{}")
            self.calls.append((method, copy.deepcopy(params)))
            try:
                body = self.dispatch(method, params)
            except ApiError as exc:
                return httpx.Response(
                    400, json={"error": exc.code, "error_description": exc.description}
                )
            return httpx.Response(200, json=body)

        return httpx.MockTransport(handle)

    @staticmethod
    def _page(items: list[dict], params: dict, size: int = 50) -> dict:
        start = int(params.get("start", 0))
        body: dict[str, Any] = {"result": items[start : start + size], "total": len(items)}
        if start + size < len(items):
            body["next"] = start + size
        return body

    # --- seeding -----------------------------------------------------------------

    def add_category(self, entity_type_id: int, name: str, *, default: bool = False,
                     cat_id: int | None = None, sort: int = 500) -> int:  # fmt: skip
        cat_id = self._id() if cat_id is None else cat_id
        self.categories.setdefault(entity_type_id, []).append(
            {"id": cat_id, "name": name, "sort": sort, "isDefault": "Y" if default else "N",
             "entityTypeId": entity_type_id}
        )  # fmt: skip
        defaults = _DEAL_DEFAULT_STAGES if entity_type_id == 2 else _SMART_DEFAULT_STAGES
        entity_id = self._stage_entity(entity_type_id, cat_id)
        for n, (code, stage_name, semantics) in enumerate(defaults):
            self.statuses.append(
                {"ID": str(self._id()), "ENTITY_ID": entity_id,
                 "STATUS_ID": self._prefix(entity_id) + code, "NAME": stage_name,
                 "SORT": str((n + 1) * 10), "COLOR": "#39A8EF", "SEMANTICS": semantics or None,
                 "SYSTEM": "Y"}
            )  # fmt: skip
        return cat_id

    def add_smart_process(self, title: str, entity_type_id: int) -> None:
        self.types.append(
            {"id": len(self.types) + 1, "title": title, "code": "", "entityTypeId": entity_type_id,
             "isCategoriesEnabled": "N", "isStagesEnabled": "Y", "isClientEnabled": "N"}
        )  # fmt: skip
        self.add_category(entity_type_id, "Общая воронка", default=True)

    @staticmethod
    def _stage_entity(entity_type_id: int, cat_id: int) -> str:
        if entity_type_id == 2:
            return "DEAL_STAGE" if cat_id == 0 else f"DEAL_STAGE_{cat_id}"
        return f"DYNAMIC_{entity_type_id}_STAGE_{cat_id}"

    @staticmethod
    def _prefix(entity_id: str) -> str:
        if m := re.fullmatch(r"DEAL_STAGE_(\d+)", entity_id):
            return f"C{m.group(1)}:"
        if m := re.fullmatch(r"DYNAMIC_(\d+)_STAGE_(\d+)", entity_id):
            return f"DT{m.group(1)}_{m.group(2)}:"
        return ""

    # --- dispatch ----------------------------------------------------------------

    def dispatch(self, method: str, p: dict) -> dict:
        if m := re.fullmatch(r"crm\.(lead|deal|contact|company)\.userfield\.(\w+)", method):
            return getattr(self, f"_uf_{m.group(2)}")(m.group(1), p)
        handler = getattr(self, "_" + method.replace(".", "_"), None)
        if handler is None:
            raise ApiError("ERROR_METHOD_NOT_FOUND", method)
        return handler(p)

    # categories
    def _crm_category_list(self, p: dict) -> dict:
        cats = self.categories.get(int(p["entityTypeId"]), [])
        return {"result": {"categories": copy.deepcopy(cats)}}

    def _find_category(self, p: dict) -> dict:
        for cat in self.categories.get(int(p["entityTypeId"]), []):
            if cat["id"] == int(p["id"]):
                return cat
        raise ApiError("NOT_FOUND", "category not found")

    def _crm_category_add(self, p: dict) -> dict:
        f = p["fields"]
        if not str(f.get("name", "")).strip():
            raise ApiError("ERROR_CORE", "name is empty")
        cat_id = self.add_category(int(p["entityTypeId"]), f["name"], sort=int(f.get("sort", 500)))
        return {"result": {"category": copy.deepcopy(self._find_category(
            {"entityTypeId": p["entityTypeId"], "id": cat_id}))}}  # fmt: skip

    def _crm_category_update(self, p: dict) -> dict:
        cat = self._find_category(p)
        for key in ("name", "sort"):
            if key in p["fields"]:
                cat[key] = p["fields"][key]
        return {"result": {"category": copy.deepcopy(cat)}}

    def _crm_category_delete(self, p: dict) -> dict:
        cat = self._find_category(p)
        if cat["isDefault"] == "Y":
            raise ApiError("ERROR_CORE", "Default category cannot be deleted")
        if (int(p["entityTypeId"]), cat["id"]) in self.busy_categories:
            raise ApiError("ERROR_CORE", "Category has items")
        self.categories[int(p["entityTypeId"])].remove(cat)
        entity_id = self._stage_entity(int(p["entityTypeId"]), cat["id"])
        self.statuses = [s for s in self.statuses if s["ENTITY_ID"] != entity_id]
        return {"result": None}

    # statuses
    def _crm_status_list(self, p: dict) -> dict:
        entity_id = p.get("filter", {}).get("ENTITY_ID")
        items = [s for s in self.statuses if entity_id is None or s["ENTITY_ID"] == entity_id]
        items.sort(key=lambda s: int(s["SORT"]))
        return self._page(copy.deepcopy(items), p)

    def _find_status(self, status_id: Any) -> dict:
        for s in self.statuses:
            if s["ID"] == str(status_id):
                return s
        raise ApiError("NOT_FOUND", "status not found")

    def _crm_status_get(self, p: dict) -> dict:
        return {"result": copy.deepcopy(self._find_status(p["id"]))}

    def _crm_status_add(self, p: dict) -> dict:
        f = p["fields"]
        for required in ("ENTITY_ID", "STATUS_ID", "NAME"):
            if not f.get(required):
                raise ApiError("ERROR_CORE", f"{required} is required")
        code = f["STATUS_ID"]
        if ":" not in code:
            code = self._prefix(f["ENTITY_ID"]) + code
        if any(s["ENTITY_ID"] == f["ENTITY_ID"] and s["STATUS_ID"] == code for s in self.statuses):
            raise ApiError("ERROR_CORE", "STATUS_ID is not unique")
        new_id = str(self._id())
        self.statuses.append(
            {"ID": new_id, "ENTITY_ID": f["ENTITY_ID"], "STATUS_ID": code, "NAME": f["NAME"],
             "SORT": str(f.get("SORT", 10)), "COLOR": f.get("COLOR"),
             "SEMANTICS": f.get("SEMANTICS") or None, "SYSTEM": "N"}
        )  # fmt: skip
        return {"result": int(new_id)}

    def _crm_status_update(self, p: dict) -> dict:
        status = self._find_status(p["id"])
        for key in ("NAME", "SORT", "COLOR"):
            if key in p["fields"]:
                status[key] = str(p["fields"][key]) if key == "SORT" else p["fields"][key]
        return {"result": True}

    def _crm_status_delete(self, p: dict) -> dict:
        status = self._find_status(p["id"])
        if status["SYSTEM"] == "Y" and (p.get("params") or {}).get("FORCED") != "Y":
            raise ApiError(
                "ERROR_CORE",
                "CRM System Status can be deleted only if parameter FORCED is specified and "
                'equal to "Y".',
            )
        if status["STATUS_ID"] in self.busy_statuses:
            raise ApiError("ERROR_CORE", "There are active items in this status.")
        self.statuses.remove(status)
        return {"result": True}

    # user fields
    def _uf_list(self, entity: str, p: dict) -> dict:
        items = sorted(self.userfields[entity], key=lambda f: int(f["SORT"]))
        return self._page(copy.deepcopy(items), p)

    def _find_uf(self, entity: str, field_id: Any) -> dict:
        for f in self.userfields[entity]:
            if f["ID"] == str(field_id):
                return f
        raise ApiError("NOT_FOUND", "field not found")

    def _uf_get(self, entity: str, p: dict) -> dict:
        return {"result": copy.deepcopy(self._find_uf(entity, p["id"]))}

    def _uf_add(self, entity: str, p: dict) -> dict:
        f = p["fields"]
        code = str(f.get("FIELD_NAME", "")).upper()
        if not re.fullmatch(r"[A-Z0-9_]+", code):
            raise ApiError("ERROR_CORE", "bad FIELD_NAME")
        if code.startswith("UF_") and not code.startswith("UF_CRM_"):
            code = "UF_CRM_" + code[3:]
        elif not code.startswith("UF_CRM_"):
            code = "UF_CRM_" + code
        if len(code) > 50 or any(x["FIELD_NAME"] == code for x in self.userfields[entity]):
            raise ApiError("ERROR_CORE", "FIELD_NAME is too long or not unique")
        new_id = str(self._id())
        label = f.get("EDIT_FORM_LABEL") or f.get("LABEL") or code
        field = {
            "ID": new_id, "FIELD_NAME": code, "USER_TYPE_ID": f["USER_TYPE_ID"],
            "MULTIPLE": f.get("MULTIPLE", "N"), "MANDATORY": f.get("MANDATORY", "N"),
            "SORT": str(f.get("SORT", 100)), "EDIT_FORM_LABEL": label,
            "SETTINGS": copy.deepcopy(f.get("SETTINGS") or {}), "XML_ID": f.get("XML_ID"),
        }  # fmt: skip
        if f["USER_TYPE_ID"] == "boolean":
            # A live portal fills these in for a yes/no field created without settings.
            field["SETTINGS"] = {"DEFAULT_VALUE": 0, "DISPLAY": "CHECKBOX", "LABEL": ["", ""],
                                 **field["SETTINGS"]}  # fmt: skip
        if f["USER_TYPE_ID"] == "enumeration":
            field["LIST"] = []
            self._apply_list(field, f.get("LIST") or [])
        self.userfields[entity].append(field)
        return {"result": int(new_id)}

    def _apply_list(self, field: dict, items: list[dict]) -> None:
        for item in items:
            if "ID" in item:
                existing = next((v for v in field["LIST"] if v["ID"] == str(item["ID"])), None)
                if existing is None:
                    raise ApiError("ERROR_CORE", f"unknown list item {item['ID']}")
                if item.get("DEL") == "Y":
                    field["LIST"].remove(existing)
                    continue
                for key in ("VALUE", "SORT", "DEF", "XML_ID"):
                    if key in item:
                        existing[key] = str(item[key])
            elif item.get("VALUE"):
                field["LIST"].append(
                    {"ID": str(self._id()), "VALUE": item["VALUE"],
                     "SORT": str(item.get("SORT", 500)), "DEF": item.get("DEF", "N"),
                     "XML_ID": item.get("XML_ID") or f"{self._next_id:032x}"}
                )  # fmt: skip

    def _uf_update(self, entity: str, p: dict) -> dict:
        field = self._find_uf(entity, p["id"])
        f = p["fields"]
        if "USER_TYPE_ID" in f or "MULTIPLE" in f:
            raise ApiError("ERROR_CORE", "USER_TYPE_ID and MULTIPLE cannot be changed")
        if "EDIT_FORM_LABEL" in f:
            field["EDIT_FORM_LABEL"] = f["EDIT_FORM_LABEL"]
        for key in ("MANDATORY", "SORT"):
            if key in f:
                field[key] = str(f[key])
        if "SETTINGS" in f:
            field["SETTINGS"].update(f["SETTINGS"])  # only passed values are overwritten
        if "LIST" in f:
            self._apply_list(field, f["LIST"])
        return {"result": True}

    def _uf_delete(self, entity: str, p: dict) -> dict:
        self.userfields[entity].remove(self._find_uf(entity, p["id"]))
        return {"result": True}

    # smart processes
    def _crm_type_list(self, p: dict) -> dict:
        return {"result": {"types": copy.deepcopy(self.types)}, "total": len(self.types)}

    def _find_type(self, type_id: Any) -> dict:
        for t in self.types:
            if t["id"] == int(type_id):
                return t
        raise ApiError("0", "Smart process not found")

    def _crm_type_add(self, p: dict) -> dict:
        f = p["fields"]
        if not str(f.get("title", "")).strip():
            raise ApiError("ERROR_CORE", "title is required")
        used = {t["entityTypeId"] for t in self.types}
        entity_type_id = next(n for n in range(1030, 10000, 2) if n not in used)
        new = {
            "id": max((t["id"] for t in self.types), default=0) + 1,
            "title": f["title"], "code": f.get("code", ""), "entityTypeId": entity_type_id,
            "isCategoriesEnabled": f.get("isCategoriesEnabled", "N"),
            "isStagesEnabled": f.get("isStagesEnabled", "N"),
            "isClientEnabled": f.get("isClientEnabled", "N"),
        }  # fmt: skip
        self.types.append(new)
        # Like Bitrix24: a default pipeline with default stages comes with every smart process.
        self.add_category(entity_type_id, "Общая", default=True)
        return {"result": {"type": copy.deepcopy(new)}}

    def _crm_type_update(self, p: dict) -> dict:
        t = self._find_type(p["id"])
        for key in ("title", "code", "isCategoriesEnabled", "isStagesEnabled", "isClientEnabled"):
            if key in p.get("fields", {}):
                t[key] = p["fields"][key]
        return {"result": {"type": copy.deepcopy(t)}}

    def _crm_type_delete(self, p: dict) -> dict:
        t = self._find_type(p["id"])
        etid = t["entityTypeId"]
        if etid in self.busy_types:
            raise ApiError("BX_ERROR", "You cannot delete an entity type that has elements")
        self.types.remove(t)
        self.categories.pop(etid, None)
        prefix = f"DYNAMIC_{etid}_STAGE_"
        self.statuses = [s for s in self.statuses if not s["ENTITY_ID"].startswith(prefix)]
        return {"result": None}

    # departments
    def _department_get(self, p: dict) -> dict:
        return self._page(copy.deepcopy(self.departments), p)

    def _find_department(self, dep_id: Any) -> dict:
        for d in self.departments:
            if d["ID"] == str(dep_id):
                return d
        raise ApiError("NOT_FOUND", "department not found")

    def _department_add(self, p: dict) -> dict:
        if not p.get("NAME") or not p.get("PARENT"):
            raise ApiError("ERROR_CORE", "NAME and PARENT are required")
        self._find_department(p["PARENT"])
        new_id = str(self._id())
        self.departments.append(
            {"ID": new_id, "NAME": p["NAME"], "SORT": str(p.get("SORT", 500)),
             "PARENT": str(p["PARENT"])}
        )  # fmt: skip
        return {"result": int(new_id)}

    def _department_update(self, p: dict) -> dict:
        dep = self._find_department(p["ID"])
        for key in ("NAME", "SORT", "PARENT"):
            if key in p:
                dep[key] = str(p[key])
        return {"result": True}

    def _department_delete(self, p: dict) -> dict:
        dep = self._find_department(p["ID"])
        if any(d.get("PARENT") == dep["ID"] for d in self.departments):
            raise ApiError("ERROR_CORE", "department has children")
        self.departments.remove(dep)
        return {"result": True}


def seeded_portal() -> FakeBitrix:
    """A small portal: two deal pipelines, a few fields, a smart process, departments."""
    fake = FakeBitrix()
    fake.add_category(2, "Общая", default=True, cat_id=0, sort=100)
    fake.add_category(2, "Сервис", cat_id=1, sort=200)
    fake.add_smart_process("Заказы", 1036)
    fake.userfields["deal"] = [
        {"ID": "57", "FIELD_NAME": "UF_CRM_SOURCE", "USER_TYPE_ID": "enumeration",
         "MULTIPLE": "N", "MANDATORY": "N", "SORT": "100", "EDIT_FORM_LABEL": "Источник",
         "SETTINGS": {"DISPLAY": "LIST", "LIST_HEIGHT": 1},
         "LIST": [{"ID": "1", "VALUE": "Сайт", "SORT": "10", "DEF": "N", "XML_ID": "SITE"},
                  {"ID": "2", "VALUE": "Звонок", "SORT": "20", "DEF": "N", "XML_ID": "CALL"}]},
        {"ID": "58", "FIELD_NAME": "UF_CRM_PRINTER", "USER_TYPE_ID": "string", "MULTIPLE": "N",
         "MANDATORY": "N", "SORT": "200", "EDIT_FORM_LABEL": "Тип принтера",
         "SETTINGS": {"SIZE": 20, "ROWS": 1}},
    ]  # fmt: skip
    fake.departments = [
        {"ID": "1", "NAME": "Компания", "SORT": "500"},
        {"ID": "2", "NAME": "Продажи", "SORT": "500", "PARENT": "1"},
    ]
    return fake
