"""AI designer: turns a brief (ТЗ) into edits of the portal spec with Claude.

Claude never touches the portal. It edits the spec through three tools
(create / update / delete by spec address); every edit is validated against the
schema and rolled back if it breaks it. The result goes through the usual
``plan`` -> ``apply`` path with all its safety checks.

Only the portal structure (names of pipelines, stages, fields, departments) is
sent to the model, never CRM records.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import yaml
from pydantic import ValidationError

from crmbuilder.spec.models import EntityType, PortalSpec

DEFAULT_MODEL = "claude-opus-5-5"
DEFAULT_EFFORT = "high"
MAX_TURNS = 40
MAX_TOKENS = 16000

# USD per million tokens: input, output, cache read. Cache writes cost 1.25x input.
PRICES = {
    "claude-opus-5-5": (4.0, 20.0, 0.20),
    "claude-sonnet-5-5": (2.0, 10.0, 0.20),
    # Haiku 5.5 rates for prompts up to 100K tokens; cache read assumed at 0.1x input.
    "claude-haiku-5-5": (0.10, 0.50, 0.01),
}

FALLBACK_BETA = "server-side-fallback-2026-07-01"

NOT_AVAILABLE_ANSWER = (
    "The user is not available to answer. Proceed with reasonable assumptions typical "
    "for this kind of business and list them in the final summary."
)


def make_client() -> Any:
    """An Anthropic client that trusts the same extra CA as the Bitrix client.

    HTTPS inspection (antivirus, corporate proxy) usually covers both APIs, so
    ``BITRIX_CA_BUNDLE`` applies here too. Raises ``ValueError`` if it cannot be loaded.
    """
    import anthropic

    from crmbuilder.bitrix.client import ssl_context

    return anthropic.Anthropic(http_client=anthropic.DefaultHttpxClient(verify=ssl_context()))


class DesignError(RuntimeError):
    pass


class EditError(ValueError):
    pass


# --- spec editing --------------------------------------------------------------------

_ADDRESS_HELP = (
    "valid addresses: crm.deal_categories.<cat>, crm.deal_categories.<cat>.stages.<stage>, "
    "crm.fields.<lead|deal|contact|company>.<field>, "
    "crm.fields.<entity>.<field>.values.<value>, "
    "crm.smart_processes.<sp>.categories.<cat>[.stages.<stage>], structure.departments.<dep>, "
    "automation.<rule>"
)


class SpecEditor:
    """Edits a spec by address; every change is validated and reverted on failure."""

    def __init__(self, spec: PortalSpec) -> None:
        self.data = spec.model_dump(mode="json")
        self.log: list[str] = []

    @property
    def spec(self) -> PortalSpec:
        data = copy.deepcopy(self.data)
        data["crm"]["fields"] = {e: f for e, f in data["crm"]["fields"].items() if f}
        return PortalSpec.model_validate(data)

    # address -> (list holding the object, key, kind)
    def _resolve(self, address: str, *, create: bool = False) -> tuple[list, str, str]:
        p = address.strip().split(".")
        crm = self.data["crm"]
        if p[:2] == ["structure", "departments"] and len(p) == 3:
            return self.data["structure"]["departments"], p[2], "department"
        if p[0] == "automation" and len(p) == 2:
            return self.data.setdefault("automation", []), p[1], "automation"
        if p[:2] == ["crm", "deal_categories"]:
            return self._category_path(crm["deal_categories"], p[2:], address)
        if p[:2] == ["crm", "fields"] and len(p) in (4, 6):
            entity = p[2]
            if entity not in {e.value for e in EntityType}:
                raise EditError(
                    f"{address}: unknown entity {entity!r}, use lead/deal/contact/company"
                )
            fields = (
                crm["fields"].setdefault(entity, []) if create else crm["fields"].get(entity, [])
            )
            if len(p) == 4:
                return fields, p[3], "field"
            if p[4] == "values":
                return self._find(fields, p[3], address).setdefault("values", []), p[5], "value"
        if p[:2] == ["crm", "smart_processes"]:
            if len(p) == 3:
                return crm["smart_processes"], p[2], "smart_process"
            if len(p) >= 5 and p[3] == "categories":
                sp = self._find(crm["smart_processes"], p[2], address)
                return self._category_path(sp.setdefault("categories", []), p[4:], address)
        raise EditError(f"{address}: not a valid address; {_ADDRESS_HELP}")

    def _category_path(self, categories: list, rest: list[str], address: str):
        if len(rest) == 1:
            return categories, rest[0], "category"
        if len(rest) == 3 and rest[1] == "stages":
            category = self._find(categories, rest[0], address)
            return category.setdefault("stages", []), rest[2], "stage"
        raise EditError(f"{address}: not a valid address; {_ADDRESS_HELP}")

    @staticmethod
    def _find(items: list, key: str, address: str) -> dict:
        for item in items:
            if item.get("key") == key:
                return item
        raise EditError(f"{address}: {key!r} does not exist")

    def _commit(self, before: dict, action: str, address: str) -> str:
        try:
            PortalSpec.model_validate(self.data)
        except ValidationError as exc:
            self.data = before
            problems = "; ".join(
                f"{'.'.join(str(x) for x in err['loc'])}: {err['msg']}" for err in exc.errors()
            )
            raise EditError(
                f"{action} {address} rejected, the spec would be invalid: {problems}"
            ) from None
        self.log.append(f"{action} {address}")
        return f"ok: {action} {address}"

    def create(self, address: str, obj: dict) -> str:
        before = copy.deepcopy(self.data)
        items, key, kind = self._resolve(address, create=True)
        if not isinstance(obj, dict):
            raise EditError("object must be a JSON object")
        if kind == "smart_process":
            obj = self._new_smart_process(obj)
        if obj.get("key", key) != key:
            raise EditError(f"object key {obj['key']!r} does not match the address key {key!r}")
        if any(item.get("key") == key for item in items):
            raise EditError(f"{address} already exists; use update to change it")
        items.append({"key": key, **{k: v for k, v in obj.items() if k != "key"}})
        if kind == "automation":
            self._enable_automation(obj.get("stage"))
        return self._commit(before, "create", address)

    def _enable_automation(self, stage: Any) -> None:
        """Robots on a smart process stage need automation switched on for that smart process."""
        p = str(stage or "").split(".")
        if len(p) == 7 and p[:2] == ["crm", "smart_processes"]:
            for sp in self.data["crm"]["smart_processes"]:
                if sp.get("key") == p[2]:
                    sp["automation_enabled"] = True

    @staticmethod
    def _new_smart_process(obj: dict) -> dict:
        """Bitrix24 gives every new smart process exactly one default pipeline."""
        obj = copy.deepcopy(obj)
        categories = obj.setdefault("categories", [])
        if not categories:
            raise EditError(
                "a new smart process needs its default pipeline in `categories` "
                "(e.g. key obshchaya, name Общая, default true, with stages if stages_enabled)"
            )
        if not any(c.get("default") for c in categories):
            categories[0]["default"] = True
        if obj.get("stages_enabled"):
            default = next(c for c in categories if c.get("default"))
            if not default.get("stages"):
                raise EditError("stages_enabled is true: give the default pipeline its stages")
        elif any(c.get("stages") for c in categories):
            raise EditError("stages are given but stages_enabled is not true")
        if len(categories) > 1:
            obj["categories_enabled"] = True
        return obj

    def update(self, address: str, changes: dict) -> str:
        before = copy.deepcopy(self.data)
        items, key, kind = self._resolve(address)
        if kind == "smart_process" and "stages_enabled" in (changes or {}):
            raise EditError(
                "stages of an existing smart process cannot be turned on or off by the tool: "
                "list it as a manual step"
            )
        if not isinstance(changes, dict) or not changes:
            raise EditError("changes must be a non-empty JSON object")
        if "key" in changes and changes["key"] != key:
            raise EditError(
                "keys cannot be renamed: change name/label instead, or create a new object "
                "and delete the old one"
            )
        self._find(items, key, address).update(changes)
        if kind == "automation" and "stage" in changes:
            self._enable_automation(changes["stage"])
        return self._commit(before, "update", address)

    def delete(self, address: str) -> str:
        before = copy.deepcopy(self.data)
        items, key, kind = self._resolve(address)
        items.remove(self._find(items, key, address))
        return self._commit(before, "delete", address)

    def view(self, address: str | None = None) -> str:
        if not address:
            return _to_yaml(self.data)
        items, key, _ = self._resolve(address)
        return _to_yaml(self._find(items, key, address))


def _to_yaml(data: Any) -> str:
    return yaml.safe_dump(data, allow_unicode=True, sort_keys=False, width=100)


# --- the conversation ---------------------------------------------------------------

SYSTEM_PROMPT = """\
You are an experienced Bitrix24 CRM implementation consultant. You receive the
current configuration of a Bitrix24 portal as a YAML spec and a brief (a task
description, often in Russian) from an integrator. Your job is to change the spec
so the portal will match the brief. You never touch the portal directly: the
integrator reviews the resulting plan and applies it.

How the spec works
- Every object has a stable `key` (lowercase latin snake_case, transliterated
  from Russian: "Запись на приём" -> zapis_na_priem). Bitrix IDs are not in the spec.
- Objects are addressed by dotted paths: crm.deal_categories.<cat>,
  crm.deal_categories.<cat>.stages.<stage>, crm.fields.<lead|deal|contact|company>.<field>,
  crm.fields.<entity>.<field>.values.<value>,
  crm.smart_processes.<sp>.categories.<cat>.stages.<stage>, structure.departments.<dep>,
  automation.<rule>.
- Edit with the tools create / update / delete. Each edit is validated; if a tool
  returns an error, read it and fix the edit. view_spec shows the current state.

Bitrix24 rules you must respect
- Stage `semantic` is process, success or failure. In a pipeline the process stages
  come first, then exactly one success stage, then failure stages, with `sort`
  increasing in that order (e.g. 10, 20, 30...).
- New pipelines: give the first stage key `new`, the success stage key `won` and the
  first failure stage key `lose`; other keys freely.
- Existing pipelines already have their success stage: never create a second one,
  rename the existing one instead. A stage cannot move to another group: to change it,
  create a new stage and delete the old one (only if the brief requires it).
- Never rename keys of existing objects; change `name` / `label` instead.
- Do not change `default` of pipelines.
- Field types: string, integer, double, boolean, date, datetime, enumeration (with
  `values`, each {key, value, sort}), employee, crm, file, url, money, address.
  CRM link fields use settings like {"CONTACT": "Y", "COMPANY": "Y"} or
  {"SMART_PROCESS:<sp_key>": "Y"}. Type and `multiple` of an existing field cannot be
  changed without losing its data: avoid it unless the brief explicitly asks.
- New departments need a `parent` (an existing department key or one you create).
- Smart processes (crm.smart_processes.<key>) are created as {title, stages_enabled,
  categories_enabled, client_enabled, categories: [...]}. Bitrix24 gives every new smart
  process one default pipeline: include it in `categories` with default true, and with its
  stages when stages_enabled is true (use keys new / success / fail for the first, success
  and failure stages). client_enabled links it to contacts and companies. Turning stages
  of an existing smart process on or off is a manual step.
- Robots and triggers on stages go to automation.<rule> as {stage, kind, action or event,
  title, when, condition, responsible, details}. `stage` is the stage address
  (crm.deal_categories.<cat>.stages.<stage> or
  crm.smart_processes.<sp>.categories.<cat>.stages.<stage>). A robot (kind robot) runs
  when an item enters the stage; action is one of task, notify, sms, email, activity,
  change_responsible, change_stage, update_fields, create_document, comment, webhook, other.
  A trigger (kind trigger) moves an item to the stage when its event happens; event is one
  of webform, call, missed_call, email, openline, payment, field_changed, webhook, other.
  title says what it does ("Задача менеджеру: перезвонить клиенту"); when is the delay in
  plain words ("сразу", "через 1 день", "за 1 день до даты визита"); details holds the
  task/message text or field values. Bitrix24 cannot set robots up through the API: the
  tool turns these rules into instructions for the integrator, so add one for every robot,
  reminder or automatic action the brief asks for. Do not touch rules with done: true.
- Business processes, access rights, telephony, integrations, reports and CRM records
  (contacts, deals) are not part of the spec: list them as manual steps.

Working style
- Keep everything the brief does not mention. Delete existing objects only when the
  brief clearly asks for it.
- If an answer would materially change the structure and you cannot infer it, ask
  with ask_user: put all questions in one call, at most two rounds. Otherwise choose
  what is typical for this kind of business and record it as an assumption.
- Use the same language as the brief for names, labels and values.
- When done, reply in Russian with a short summary: what you set up, the assumptions
  you made, and the manual steps that remain. Do not repeat the whole spec.
"""

TOOLS: list[dict[str, Any]] = [
    {
        "name": "view_spec",
        "description": "Show the current spec as YAML, or one object if an address is given.",
        "input_schema": {
            "type": "object",
            "properties": {"address": {"type": "string", "description": "optional address"}},
            "additionalProperties": False,
        },
    },
    {
        "name": "create",
        "description": (
            "Create a new object at an address. The object uses the spec format; nested "
            "children (stages of a pipeline, values of a list field) may be included. "
            "Example: address crm.deal_categories.tender, object {name, sort, stages: [...]}."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "object": {"type": "object", "description": "the object without its key"},
            },
            "required": ["address", "object"],
            "additionalProperties": False,
        },
    },
    {
        "name": "update",
        "description": (
            "Change attributes of an existing object; only the given attributes change. "
            "Passing a list (stages, values) replaces it entirely, so prefer editing children "
            "by their own addresses."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "address": {"type": "string"},
                "changes": {"type": "object"},
            },
            "required": ["address", "changes"],
            "additionalProperties": False,
        },
    },
    {
        "name": "delete",
        "description": "Delete an object and everything nested in it.",
        "input_schema": {
            "type": "object",
            "properties": {"address": {"type": "string"}},
            "required": ["address"],
            "additionalProperties": False,
        },
    },
    {
        "name": "ask_user",
        "description": (
            "Ask the integrator clarifying questions (in Russian). Put all questions in one "
            "call. Returns their answers."
        ),
        "input_schema": {
            "type": "object",
            "properties": {"questions": {"type": "array", "items": {"type": "string"}}},
            "required": ["questions"],
            "additionalProperties": False,
        },
    },
]


@dataclass
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cache_read_tokens: int = 0
    cache_write_tokens: int = 0
    requests: int = 0

    def add(self, usage: Any) -> None:
        self.requests += 1
        self.input_tokens += getattr(usage, "input_tokens", 0) or 0
        self.output_tokens += getattr(usage, "output_tokens", 0) or 0
        self.cache_read_tokens += getattr(usage, "cache_read_input_tokens", 0) or 0
        self.cache_write_tokens += getattr(usage, "cache_creation_input_tokens", 0) or 0

    def cost(self, model: str) -> float | None:
        prices = PRICES.get(model)
        if prices is None:
            return None
        price_in, price_out, price_cache = prices
        return (
            self.input_tokens * price_in
            + self.cache_write_tokens * price_in * 1.25
            + self.cache_read_tokens * price_cache
            + self.output_tokens * price_out
        ) / 1_000_000


@dataclass
class DesignResult:
    spec: PortalSpec
    summary: str
    usage: Usage
    edits: list[str] = field(default_factory=list)


Asker = Callable[[list[str]], list[str]]
Reporter = Callable[[str], None]


def design(
    client: Any,
    spec: PortalSpec,
    brief: str,
    *,
    ask: Asker | None = None,
    report: Reporter | None = None,
    model: str = DEFAULT_MODEL,
    effort: str = DEFAULT_EFFORT,
    max_turns: int = MAX_TURNS,
) -> DesignResult:
    """Run the design conversation and return the edited spec.

    ``client`` is an ``anthropic.Anthropic`` instance (or a test double with the same
    ``beta.messages.create``). ``ask`` answers clarifying questions; without it Claude
    is told to proceed on assumptions.
    """
    editor = SpecEditor(spec)
    report = report or (lambda line: None)
    usage = Usage()
    messages: list[dict[str, Any]] = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": f"<current_spec>\n{editor.view()}</current_spec>"},
                {"type": "text", "text": f"<brief>\n{brief.strip()}\n</brief>"},
            ],
        }
    ]
    extra: dict[str, Any] = {}
    if not model.startswith("claude-haiku"):
        # A declined request is re-run on Anthropic's recommended model instead of failing.
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}

    for _ in range(max_turns):
        response = client.beta.messages.create(
            model=model,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
            thinking={"type": "adaptive"},
            output_config={"effort": effort},
            cache_control={"type": "ephemeral"},
            **extra,
        )
        usage.add(response.usage)
        stop = response.stop_reason
        if stop == "refusal":
            details = getattr(response, "stop_details", None)
            category = getattr(details, "category", None)
            raise DesignError(f"the model declined the request (category: {category})")
        if stop == "max_tokens":
            raise DesignError("the model's answer was cut off (max_tokens); try a shorter brief")

        # Append the whole content (thinking and fallback blocks included) unchanged.
        messages.append({"role": "assistant", "content": response.content})
        if stop in ("end_turn", "stop_sequence"):
            summary = "\n".join(b.text for b in response.content if b.type == "text").strip()
            return DesignResult(editor.spec, summary, usage, editor.log)
        if stop == "pause_turn":
            continue

        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            content, is_error = _run_tool(editor, block.name, block.input, ask, report)
            result: dict[str, Any] = {
                "type": "tool_result",
                "tool_use_id": block.id,
                "content": content,
            }
            if is_error:
                result["is_error"] = True
            results.append(result)
        if not results:
            raise DesignError(f"unexpected stop reason {stop!r} without tool calls")
        messages.append({"role": "user", "content": results})

    raise DesignError(f"no result after {max_turns} rounds; try a more specific brief")


def _run_tool(
    editor: SpecEditor, name: str, args: Any, ask: Asker | None, report: Reporter
) -> tuple[str, bool]:
    if isinstance(args, str):
        try:
            args = json.loads(args)
        except ValueError:
            return "Error: tool input is not valid JSON", True
    if not isinstance(args, dict):
        return "Error: tool input must be an object", True
    try:
        if name == "view_spec":
            return editor.view(args.get("address")), False
        if name == "create":
            out = editor.create(args["address"], args["object"])
        elif name == "update":
            out = editor.update(args["address"], args["changes"])
        elif name == "delete":
            out = editor.delete(args["address"])
        elif name == "ask_user":
            questions = [str(q) for q in args.get("questions", []) if str(q).strip()]
            if not questions:
                return "Error: no questions given", True
            if ask is None:
                return NOT_AVAILABLE_ANSWER, False
            answers = ask(questions)
            return "\n".join(
                f"Q: {q}\nA: {a or '(no answer, decide yourself)'}"
                for q, a in zip(questions, answers, strict=False)
            ), False
        else:
            return f"Error: unknown tool {name}", True
    except KeyError as exc:
        return f"Error: missing argument {exc}", True
    except EditError as exc:
        report(f"  ! {exc}")
        return f"Error: {exc}", True
    report(f"  {out.removeprefix('ok: ')}")
    return out, False
