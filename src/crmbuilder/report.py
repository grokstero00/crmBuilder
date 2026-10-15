"""Setup report for the client: what was done, the resulting CRM structure, manual steps.

The "what was done" part is the plan between the portal before the project (a
snapshot saved by ``apply``) and now, rewritten in plain Russian. Manual steps
come from Claude (optional): it reads the brief and the changes and writes
step-by-step instructions for what the tool does not configure.
"""

from __future__ import annotations

import itertools
import json
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from crmbuilder.apply import _parse
from crmbuilder.plan import Action, Plan, diff_specs
from crmbuilder.spec.models import (
    Category,
    Department,
    EntityType,
    Field,
    FieldType,
    PortalSpec,
    StageSemantic,
)

ENTITY_TITLES = {
    EntityType.deal: "Сделки",
    EntityType.lead: "Лиды",
    EntityType.contact: "Контакты",
    EntityType.company: "Компании",
}
ENTITY_WHERE = {
    EntityType.deal: "в сделках",
    EntityType.lead: "в лидах",
    EntityType.contact: "в контактах",
    EntityType.company: "в компаниях",
}
FIELD_TYPES = {
    FieldType.string: "строка",
    FieldType.integer: "целое число",
    FieldType.double: "число",
    FieldType.boolean: "да/нет",
    FieldType.date: "дата",
    FieldType.datetime: "дата и время",
    FieldType.enumeration: "список",
    FieldType.employee: "сотрудник",
    FieldType.crm: "привязка к CRM",
    FieldType.file: "файл",
    FieldType.url: "ссылка",
    FieldType.money: "деньги",
    FieldType.address: "адрес",
    FieldType.iblock_element: "элемент инфоблока",
    FieldType.iblock_section: "раздел инфоблока",
    FieldType.other: "другой тип",
}
SEMANTIC_TITLES = {
    StageSemantic.process: "в работе",
    StageSemantic.success: "успех",
    StageSemantic.failure: "провал",
}


# --- lookups -------------------------------------------------------------------------


def _categories(spec: PortalSpec, parts: dict) -> list[Category]:
    if "sp" not in parts:
        return spec.crm.deal_categories
    sp = next((s for s in spec.crm.smart_processes if s.key == parts["sp"]), None)
    return sp.categories if sp else []


def _category(spec: PortalSpec, parts: dict) -> Category | None:
    return next((c for c in _categories(spec, parts) if c.key == parts["cat"]), None)


def _field(spec: PortalSpec, parts: dict) -> Field | None:
    fields = spec.crm.fields.get(EntityType(parts["entity"]), [])
    return next((f for f in fields if f.key == parts["field"]), None)


def _department(spec: PortalSpec, key: str) -> Department | None:
    return next((d for d in spec.structure.departments if d.key == key), None)


def _pipeline_title(spec: PortalSpec, parts: dict) -> str:
    category = _category(spec, parts)
    name = category.name if category else parts["cat"]
    if "sp" in parts:
        sp = next((s for s in spec.crm.smart_processes if s.key == parts["sp"]), None)
        return f"«{name}» смарт-процесса «{sp.title if sp else parts['sp']}»"
    return f"«{name}»"


def _stage_list(category: Category) -> str:
    """': A → B → Won; провальные: X, Y.' — failure stages are not part of the flow."""
    flow = [s.name for s in category.stages if s.semantic is not StageSemantic.failure]
    failed = [s.name for s in category.stages if s.semantic is StageSemantic.failure]
    if not flow and not failed:
        return "."
    text = ": " + " → ".join(flow)
    if failed:
        text += ("; " if flow else "") + "провальные: " + ", ".join(failed)
    return text + "."


def _depth(spec: PortalSpec, key: str) -> int:
    depth, seen = 0, set()
    dep = _department(spec, key)
    while dep is not None and dep.parent and dep.parent not in seen:
        seen.add(dep.parent)
        depth += 1
        dep = _department(spec, dep.parent)
    return depth


def _field_kind(f: Field) -> str:
    kind = FIELD_TYPES.get(f.type, f.type.value)
    if f.multiple:
        kind += ", несколько значений"
    if f.required:
        kind += ", обязательное"
    return kind


# --- what was done -------------------------------------------------------------------


@dataclass
class ChangeLine:
    section: str
    text: str
    order: int = 0  # within a section: parents before children


_PIPELINES = "Воронки и стадии"
_STRUCTURE = "Структура компании"
_SECTION_ORDER = (
    [_PIPELINES]
    + [f"Поля: {ENTITY_TITLES[e]}" for e in ENTITY_TITLES]
    + [
        "Смарт-процессы",
        _STRUCTURE,
    ]
)


def describe_changes(before: PortalSpec, after: PortalSpec) -> list[ChangeLine]:
    """The plan between two specs as plain-language sentences, grouped by section."""
    return _describe(diff_specs(before, after), before, after)


def _section(kind: str, parts: dict) -> str:
    if kind in ("category", "stage"):
        return _PIPELINES
    if kind in ("field", "value"):
        return f"Поля: {ENTITY_TITLES[EntityType(parts['entity'])]}"
    if kind == "department":
        return _STRUCTURE
    return "Смарт-процессы"


def _describe(plan: Plan, before: PortalSpec, after: PortalSpec) -> list[ChangeLine]:
    created = {c.address for c in plan.changes if c.action is Action.create}
    lines: list[ChangeLine] = []
    for change in plan.changes:
        kind, parts = _parse(change.address)
        parent = change.address.rsplit(".", 2)[0]
        if change.action is Action.create and parent in created:
            continue  # described together with its new parent
        text = _sentence(change, kind, parts, before, after)
        if text:
            depth = _depth(after, parts["dep"]) if kind == "department" else 0
            lines.append(ChangeLine(_section(kind, parts), text, depth))
    order = {name: n for n, name in enumerate(_SECTION_ORDER)}
    return sorted(lines, key=lambda line: (order.get(line.section, len(order)), line.order))


def _sentence(change, kind: str, parts: dict, before: PortalSpec, after: PortalSpec) -> str:
    action = change.action
    attrs = change.attributes
    # Deleted objects only exist in the old spec; everything else is named as it is now.
    spec = before if action is Action.delete else after
    if kind == "category":
        title = _pipeline_title(spec, parts)
        if action is Action.create:
            return f"Создана воронка {title}{_stage_list(_category(spec, parts))}"
        if action is Action.delete:
            return f"Удалена воронка {title}."
        if "name" in attrs:
            return f"Воронка «{attrs['name'][0]}» переименована в «{attrs['name'][1]}»."
        return f"Изменён порядок воронки {title}."
    if kind == "stage":
        pipeline = _pipeline_title(spec, parts)
        category = _category(spec, parts)
        stage = next((s for s in category.stages if s.key == parts["stage"]), None)
        name = stage.name if stage else parts["stage"]
        if action is Action.create:
            group = SEMANTIC_TITLES[stage.semantic]
            return f"В воронке {pipeline} добавлена стадия «{name}» ({group})."
        if action is Action.delete:
            return f"Из воронки {pipeline} удалена стадия «{name}»."
        if "name" in attrs:
            old, new = attrs["name"]
            return f"В воронке {pipeline} стадия «{old}» переименована в «{new}»."
        if "sort" in attrs:
            return f"В воронке {pipeline} изменён порядок стадии «{name}»."
        return f"В воронке {pipeline} изменён цвет стадии «{name}»."
    if kind == "field":
        f = _field(spec, parts)
        where = ENTITY_WHERE[EntityType(parts["entity"])]
        label = f.label if f else parts["field"]
        if action is Action.create:
            text = f"Добавлено поле «{label}» {where} ({_field_kind(f)})"
            if f.values:
                text += ": " + ", ".join(v.value for v in f.values)
            return text + "."
        if action is Action.delete:
            return f"Удалено поле «{label}» {where}."
        if action is Action.replace:
            return f"Поле «{label}» {where} пересоздано с типом «{_field_kind(f)}»."
        if "label" in attrs:
            old, new = attrs["label"]
            return f"Поле «{old}» {where} переименовано в «{new}»."
        if "required" in attrs:
            state = "обязательным" if attrs["required"][1] else "необязательным"
            return f"Поле «{label}» {where} сделано {state}."
        return f"Изменены настройки поля «{label}» {where}."
    if kind == "value":
        f = _field(after, parts) or _field(before, parts)  # the list under its current name
        where = ENTITY_WHERE[EntityType(parts["entity"])]
        holder = _field(spec, parts)
        value = next(
            (v for v in (holder.values if holder else []) if v.key == parts["value"]), None
        )
        name = value.value if value else parts["value"]
        if action is Action.create:
            return f"В список «{f.label}» {where} добавлено значение «{name}»."
        if action is Action.delete:
            return f"Из списка «{f.label}» {where} удалено значение «{name}»."
        if "value" in attrs:
            old, new = attrs["value"]
            return f"В списке «{f.label}» {where} значение «{old}» переименовано в «{new}»."
        return f"В списке «{f.label}» {where} изменён порядок значения «{name}»."
    if kind == "department":
        dep = _department(spec, parts["dep"])
        name = dep.name if dep else parts["dep"]
        if action is Action.create:
            parent = _department(spec, dep.parent) if dep.parent else None
            where = f" в составе «{parent.name}»" if parent else ""
            return f"Создан отдел «{name}»{where}."
        if action is Action.delete:
            return f"Удалён отдел «{name}»."
        if "name" in attrs:
            return f"Отдел «{attrs['name'][0]}» переименован в «{attrs['name'][1]}»."
        return f"Изменено положение отдела «{name}» в структуре."
    if kind == "smart_process":
        return f"Изменены настройки смарт-процесса «{parts['sp']}»."
    return ""


# --- structure -----------------------------------------------------------------------


def _structure(spec: PortalSpec) -> list[str]:
    lines = ["## Структура CRM", ""]
    if spec.crm.deal_categories:
        lines += ["### Воронки сделок", ""]
        for category in spec.crm.deal_categories:
            lines += _pipeline_block(category)
    for sp in spec.crm.smart_processes:
        lines += [f"### Смарт-процесс «{sp.title}»", ""]
        if not sp.categories or not sp.stages_enabled:
            lines += ["Без стадий.", ""]
        for category in sp.categories:
            lines += _pipeline_block(category)
    for entity in EntityType:
        fields = spec.crm.fields.get(entity, [])
        if not fields:
            continue
        lines += [f"### Дополнительные поля: {ENTITY_TITLES[entity]}", ""]
        lines += ["| Поле | Тип | Варианты |", "|---|---|---|"]
        for f in fields:
            values = ", ".join(v.value for v in f.values)
            lines.append(f"| {f.label} | {_field_kind(f)} | {values} |")
        lines.append("")
    if spec.structure.departments:
        lines += ["### Структура компании", ""]
        lines += _department_tree(spec.structure.departments)
        lines.append("")
    return lines


def _pipeline_block(category: Category) -> list[str]:
    title = f"**{category.name}**" + (" (основная)" if category.default else "")
    lines = [title, ""]
    for n, stage in enumerate(category.stages, 1):
        marker = {StageSemantic.success: " ✅", StageSemantic.failure: " ❌"}.get(
            stage.semantic, ""
        )
        lines.append(f"{n}. {stage.name}{marker}")
    return lines + [""]


def _department_tree(deps: list[Department]) -> list[str]:
    children: dict[str | None, list[Department]] = {}
    for dep in deps:
        children.setdefault(dep.parent, []).append(dep)
    lines: list[str] = []

    def walk(parent: str | None, depth: int) -> None:
        for dep in sorted(children.get(parent, []), key=lambda d: (d.sort or 0, d.name)):
            lines.append(f"{'  ' * depth}- {dep.name}")
            walk(dep.key, depth + 1)

    walk(None, 0)
    return lines


# --- manual steps with Claude ---------------------------------------------------------

AI_SYSTEM_PROMPT = """\
You are a Bitrix24 implementation consultant writing the hand-over document for a
client. You receive the client's brief, the list of changes that were configured
automatically, and the resulting CRM structure. The automatic tool configures only
pipelines, stages, custom fields, list values and departments.

Write, in Russian:
- summary: 2-4 sentences for the client's manager on what the setup gives them.
- manual_steps: everything from the brief that the tool did not configure (robots,
  business processes, access rights, smart processes, telephony, integrations,
  forms, reports, data import, onboarding). For each: a short title, why it is needed,
  and concrete numbered steps in the Bitrix24 interface. Interface paths change between
  Bitrix24 versions, so describe them as they usually are and never invent settings
  that do not exist. Skip anything already done automatically.
Do not invent requirements that are not in the brief or implied by it.
"""

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "summary": {"type": "string"},
        "manual_steps": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "why": {"type": "string"},
                    "steps": {"type": "array", "items": {"type": "string"}},
                },
                "required": ["title", "why", "steps"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["summary", "manual_steps"],
    "additionalProperties": False,
}


@dataclass
class ManualStep:
    title: str
    why: str
    steps: list[str] = field(default_factory=list)


@dataclass
class AiPart:
    summary: str
    manual_steps: list[ManualStep]
    usage: Any


def ai_manual_steps(
    client: Any,
    spec: PortalSpec,
    changes: list[ChangeLine],
    brief: str,
    *,
    model: str,
    effort: str,
) -> AiPart:
    from crmbuilder.design import FALLBACK_BETA, Usage, _to_yaml

    structure = _to_yaml(spec.model_dump(mode="json", exclude_defaults=True))
    done = "\n".join(f"- {line.text}" for line in changes) or "nothing"
    content = [
        {"type": "text", "text": f"<brief>\n{brief.strip()}\n</brief>"},
        {
            "type": "text",
            "text": f"<configured_automatically>\n{done}\n</configured_automatically>",
        },
        {"type": "text", "text": f"<crm_structure>\n{structure}</crm_structure>"},
    ]
    extra: dict[str, Any] = {}
    if not model.startswith("claude-haiku"):
        extra = {"betas": [FALLBACK_BETA], "fallbacks": "default"}
    response = client.beta.messages.create(
        model=model,
        max_tokens=16000,
        system=AI_SYSTEM_PROMPT,
        messages=[{"role": "user", "content": content}],
        thinking={"type": "adaptive"},
        output_config={"effort": effort, "format": {"type": "json_schema", "schema": AI_SCHEMA}},
        **extra,
    )
    usage = Usage()
    usage.add(response.usage)
    if response.stop_reason == "refusal":
        raise RuntimeError("the model declined the request")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("the model's answer was cut off")
    text = next((b.text for b in response.content if b.type == "text"), "")
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise RuntimeError("the model returned an answer that is not valid JSON") from exc
    steps = [
        ManualStep(s.get("title", ""), s.get("why", ""), [str(x) for x in s.get("steps", [])])
        for s in data.get("manual_steps", [])
    ]
    return AiPart(data.get("summary", ""), steps, usage)


# --- the document ----------------------------------------------------------------------


def render(
    spec: PortalSpec,
    *,
    changes: list[ChangeLine] | None,
    title: str = "",
    ai: AiPart | None = None,
    today: date | None = None,
) -> str:
    today = today or date.today()
    lines = [f"# {title or f'Отчёт о настройке портала {spec.portal}'}", ""]
    lines += [f"Портал: {spec.portal}  ", f"Дата: {today.strftime('%d.%m.%Y')}", ""]
    if ai and ai.summary:
        lines += [ai.summary, ""]
    if changes is not None:
        lines += ["## Что сделано", ""]
        if not changes:
            lines += ["Изменений структуры не было.", ""]
        for section, group in itertools.groupby(changes, key=lambda line: line.section):
            lines += [f"### {section}", ""]
            lines += [f"- {line.text}" for line in group]
            lines.append("")
    if ai and ai.manual_steps:
        lines += ["## Что нужно настроить вручную", ""]
        for n, step in enumerate(ai.manual_steps, 1):
            lines += [f"### {n}. {step.title}", ""]
            if step.why:
                lines += [step.why, ""]
            lines += [f"{i}. {s}" for i, s in enumerate(step.steps, 1)] + [""]
        lines += [
            "> Пути в интерфейсе указаны для текущей версии Битрикс24 и могут немного "
            "отличаться на вашем портале.",
            "",
        ]
    lines += _structure(spec)
    return "\n".join(lines).rstrip() + "\n"
