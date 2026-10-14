"""Portal audit: finds problems in a spec without touching the portal.

Rule checks are free and deterministic. ``ai_review`` optionally asks Claude for the
things rules cannot see (fields that do not fit the business, leftovers of old
projects); it receives only the structure and the rule findings.
"""

from __future__ import annotations

import itertools
import json
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from enum import StrEnum
from typing import Any

from crmbuilder.spec.models import (
    Category,
    EntityType,
    FieldType,
    PortalSpec,
    StageSemantic,
)


class Severity(StrEnum):
    error = "error"
    warning = "warning"
    info = "info"


_SEVERITY_ORDER = {Severity.error: 0, Severity.warning: 1, Severity.info: 2}
_SEVERITY_TITLE = {
    Severity.error: "Ошибки",
    Severity.warning: "Предупреждения",
    Severity.info: "Рекомендации",
}


@dataclass
class Finding:
    severity: Severity
    code: str
    address: str
    message: str
    hint: str = ""
    source: str = "rules"

    def to_dict(self) -> dict[str, Any]:
        return {**asdict(self), "severity": self.severity.value}


# Requisites live in a separate entity; a custom field for them is a common, softer case.
_REQUISITES = "Реквизиты"

# Standard Bitrix24 CRM fields that custom fields often duplicate. Each entry is a
# set of normalized label stems and the name of the standard field.
_STANDARD_FIELDS: dict[EntityType, list[tuple[tuple[str, ...], str]]] = {
    EntityType.deal: [
        (("сумма", "сумма сделки", "бюджет"), "Сумма"),
        (("вероятность",), "Вероятность"),
        (("клиент", "компания", "компания клиента"), "Компания (привязка)"),
        (("контакт", "контактное лицо"), "Контакты (привязка)"),
        (("источник", "источник сделки", "источник обращения"), "Источник"),
        (("ответственный", "менеджер"), "Ответственный"),
        (("дата начала",), "Дата начала"),
        (("дата закрытия", "дата завершения"), "Предполагаемая дата закрытия"),
        (("комментарий", "комментарии", "описание"), "Комментарий"),
        (("валюта",), "Валюта"),
        (("тип сделки",), "Тип сделки"),
    ],
    EntityType.contact: [
        (("должность",), "Должность"),
        (("телефон", "мобильный телефон"), "Телефон"),
        (("email", "e-mail", "почта", "электронная почта"), "E-mail"),
        (("дата рождения", "день рождения"), "Дата рождения"),
        (("адрес",), "Адрес"),
        (("компания", "организация"), "Компания (привязка)"),
        (("источник",), "Источник"),
        (("комментарий", "комментарии"), "Комментарий"),
    ],
    EntityType.company: [
        (("телефон",), "Телефон"),
        (("email", "e-mail", "почта"), "E-mail"),
        (("сайт", "веб-сайт"), "Сайт"),
        (("адрес", "юридический адрес"), "Адрес / реквизиты"),
        (("отрасль", "сфера деятельности"), "Сфера деятельности"),
        (("тип компании",), "Тип компании"),
        (("количество сотрудников", "сотрудников", "кол-во сотрудников"), "Кол-во сотрудников"),
        (("годовой оборот", "оборот"), "Годовой оборот"),
        (("инн", "бин", "иин", "кпп", "огрн"), _REQUISITES),
        (("комментарий", "комментарии"), "Комментарий"),
    ],
    EntityType.lead: [
        (("телефон",), "Телефон"),
        (("email", "e-mail", "почта"), "E-mail"),
        (("источник",), "Источник"),
        (("сумма", "бюджет"), "Сумма"),
        (("должность",), "Должность"),
        (("компания", "название компании"), "Название компании"),
        (("комментарий", "комментарии"), "Комментарий"),
    ],
}

_JUNK = re.compile(
    r"^(тест\w*|test\w*|проверка|new field|новое поле|без названия|untitled|"
    r"поле ?\d*|field ?\d*|asdf\w*|qwe\w*|ыва\w*|\d+|x+|xxx|temp|tmp|удалить\w*)$",
    re.IGNORECASE,
)
_CYRILLIC = re.compile(r"[а-яё]", re.IGNORECASE)
_LATIN = re.compile(r"[a-z]", re.IGNORECASE)
_WORD = re.compile(r"[a-zа-яё]+", re.IGNORECASE)


def _norm(text: str) -> str:
    text = re.sub(r"[%№#.,:;!?()«»\"'/\\-]+", " ", text.lower().replace("ё", "е"))
    return re.sub(r"\s+", " ", text).strip()


class _Auditor:
    def __init__(self, spec: PortalSpec) -> None:
        self.spec = spec
        self.findings: list[Finding] = []

    def add(self, severity: Severity, code: str, address: str, message: str, hint: str = ""):
        self.findings.append(Finding(severity, code, address, message, hint))

    def run(self) -> list[Finding]:
        crm = self.spec.crm
        for category in crm.deal_categories:
            self._pipeline(f"crm.deal_categories.{category.key}", category, stages_expected=True)
        for sp in crm.smart_processes:
            for category in sp.categories:
                self._pipeline(
                    f"crm.smart_processes.{sp.key}.categories.{category.key}",
                    category,
                    stages_expected=sp.stages_enabled,
                )
        self._duplicates(
            "crm.deal_categories", [(c.key, c.name) for c in crm.deal_categories], "воронки"
        )
        self._similar_pipelines(crm.deal_categories)
        for entity, fields in crm.fields.items():
            self._fields(entity, fields)
        self._departments()
        self.findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.address))
        return self.findings

    # --- text checks ---------------------------------------------------------------

    def _text(self, address: str, text: str, what: str) -> None:
        mixed = [w for w in _WORD.findall(text) if _CYRILLIC.search(w) and _LATIN.search(w)]
        if mixed:
            self.add(
                Severity.warning,
                "mixed_script",
                address,
                f"{what} «{text}»: в слове {', '.join(f'«{w}»' for w in mixed)} смешаны "
                "русские и латинские буквы",
                "Замените латинские буквы на русские: поиск и фильтры по этому слову "
                "сейчас не находят объект.",
            )
        if text != text.strip() or "  " in text:
            self.add(
                Severity.info,
                "whitespace",
                address,
                f"{what} «{text}»: лишние пробелы",
                "Уберите пробелы в начале, в конце и двойные пробелы.",
            )
        if _JUNK.match(text.strip()):
            self.add(
                Severity.warning,
                "junk_name",
                address,
                f"{what} «{text}» похоже на тестовое или временное название",
                "Удалите объект, если он не используется, или дайте понятное название.",
            )

    def _duplicates(self, scope: str, items: list[tuple[str, str]], what: str) -> None:
        groups: dict[str, list[str]] = defaultdict(list)
        names: dict[str, str] = {}
        for key, name in items:
            groups[_norm(name)].append(key)
            names[_norm(name)] = name
        for normalized, keys in groups.items():
            if normalized and len(keys) > 1:
                self.add(
                    Severity.warning,
                    "duplicate_name",
                    scope,
                    f"{what}: одинаковое название «{names[normalized]}» у {', '.join(keys)}",
                    "Переименуйте или объедините: пользователи не различат их в интерфейсе.",
                )

    # --- pipelines -------------------------------------------------------------

    def _pipeline(self, address: str, category: Category, *, stages_expected: bool) -> None:
        self._text(address, category.name, "Воронка")
        stages = category.stages
        if not stages_expected:
            return
        if not stages:
            self.add(Severity.warning, "no_stages", address,
                     f"Воронка «{category.name}» без стадий")  # fmt: skip
            return
        for stage in stages:
            self._text(f"{address}.stages.{stage.key}", stage.name, "Стадия")
        self._duplicates(f"{address}.stages", [(s.key, s.name) for s in stages], "Стадии")

        successes = [s for s in stages if s.semantic is StageSemantic.success]
        failures = [s for s in stages if s.semantic is StageSemantic.failure]
        if len(successes) != 1:
            self.add(
                Severity.error,
                "success_stages",
                address,
                f"Воронка «{category.name}»: успешных стадий {len(successes)}, нужна ровно одна",
            )
        if not failures:
            self.add(
                Severity.info,
                "no_failure_stage",
                address,
                f"Воронка «{category.name}» без провальной стадии",
                "Без неё нельзя корректно закрыть неудачную сделку и посчитать конверсию.",
            )
        rank = {StageSemantic.process: 0, StageSemantic.success: 1, StageSemantic.failure: 2}
        ordered = sorted(
            (s for s in stages if s.sort is not None), key=lambda s: (s.sort, rank[s.semantic])
        )
        if any(rank[a.semantic] > rank[b.semantic] for a, b in itertools.pairwise(ordered)):
            self.add(
                Severity.error,
                "stage_order",
                address,
                f"Воронка «{category.name}»: порядок стадий нарушен — рабочие стадии должны "
                "идти до успешной, провальные после неё",
                "Поправьте sort стадий.",
            )

    def _similar_pipelines(self, categories: list[Category]) -> None:
        def names(c: Category) -> set[str]:
            return {_norm(s.name) for s in c.stages if s.semantic is StageSemantic.process}

        for i, a in enumerate(categories):
            for b in categories[i + 1 :]:
                sa, sb = names(a), names(b)
                if len(sa) < 3 or len(sb) < 3:
                    continue
                similarity = len(sa & sb) / len(sa | sb)
                if similarity >= 0.75:
                    self.add(
                        Severity.info,
                        "similar_pipelines",
                        f"crm.deal_categories.{a.key}",
                        f"Воронки «{a.name}» и «{b.name}» почти одинаковые "
                        f"({round(similarity * 100)}% общих рабочих стадий)",
                        "Если процесс один, удобнее одна воронка и поле-признак; если разные, "
                        "держите их в одном шаблоне, чтобы правки не расходились.",
                    )

    # --- fields ------------------------------------------------------------------

    def _fields(self, entity: EntityType, fields) -> None:
        scope = f"crm.fields.{entity.value}"
        self._duplicates(scope, [(f.key, f.label) for f in fields], "Поля")
        standards = _STANDARD_FIELDS.get(entity, [])
        for field in fields:
            address = f"{scope}.{field.key}"
            self._text(address, field.label, "Поле")
            if field.label.upper().startswith("UF_"):
                self.add(Severity.info, "no_label", address,
                         f"У поля {field.label} нет понятного названия")  # fmt: skip
            label = _norm(field.label)
            for stems, standard in standards:
                if label not in {_norm(stem) for stem in stems}:
                    continue
                if standard == _REQUISITES:
                    self.add(
                        Severity.info,
                        "requisites_field",
                        address,
                        f"Поле «{field.label}» — данные, которые в Битрикс24 хранятся в "
                        "реквизитах компании",
                        "Если используете документы и счета из Битрикс24, заполняйте реквизиты: "
                        "оттуда данные подставляются в документы.",
                    )
                else:
                    self.add(
                        Severity.warning,
                        "standard_duplicate",
                        address,
                        f"Поле «{field.label}» дублирует стандартное поле Битрикс24 «{standard}»",
                        "Используйте стандартное поле: оно участвует в отчётах, фильтрах и "
                        "интеграциях. Перенесите данные и удалите пользовательское поле.",
                    )
                break
            if field.type is FieldType.enumeration:
                self._enumeration(address, field)
            if field.type is FieldType.other:
                self.add(Severity.info, "unknown_type", address,
                         f"Поле «{field.label}»: тип {field.bitrix_type} не поддерживается "
                         "программой, правки этого поля только вручную")  # fmt: skip
            for setting in field.settings:
                if re.fullmatch(r"DYNAMIC_\d+", setting):
                    self.add(
                        Severity.warning,
                        "broken_link",
                        address,
                        f"Поле «{field.label}» ссылается на смарт-процесс, которого нет в "
                        f"спецификации ({setting})",
                        "Если файл старый, выполните import заново. Если смарт-процесс удалён, "
                        "удалите привязку или поле.",
                    )

    def _enumeration(self, address: str, field) -> None:
        if not field.values:
            self.add(Severity.warning, "empty_list", address,
                     f"Список «{field.label}» без значений")  # fmt: skip
            return
        if len(field.values) == 1:
            self.add(Severity.info, "single_value_list", address,
                     f"Список «{field.label}» из одного значения",
                     "Возможно, подойдёт поле «да/нет».")  # fmt: skip
        for value in field.values:
            self._text(f"{address}.values.{value.key}", value.value, "Значение списка")
        self._duplicates(f"{address}.values", [(v.key, v.value) for v in field.values],
                         f"Значения списка «{field.label}»")  # fmt: skip
        caption = str(field.settings.get("CAPTION_NO_VALUE", "")).strip()
        if caption and _norm(caption) in {_norm(v.value) for v in field.values}:
            self.add(
                Severity.warning,
                "caption_conflict",
                address,
                f"Список «{field.label}»: пустое значение подписано «{caption}», и такое же "
                "значение есть в списке",
                "Пользователи не отличат «не заполнено» от выбранного значения. Смените подпись "
                "пустого значения (например, «Не выбрано»).",
            )

    # --- structure ----------------------------------------------------------------

    def _departments(self) -> None:
        deps = self.spec.structure.departments
        by_parent: dict[str | None, list] = defaultdict(list)
        for dep in deps:
            self._text(f"structure.departments.{dep.key}", dep.name, "Отдел")
            by_parent[dep.parent].append(dep)
        for siblings in by_parent.values():
            self._duplicates("structure.departments", [(d.key, d.name) for d in siblings], "Отделы")


def audit(spec: PortalSpec) -> list[Finding]:
    return _Auditor(spec).run()


# --- AI review -------------------------------------------------------------------------

AI_SYSTEM_PROMPT = """\
You are a senior Bitrix24 CRM consultant auditing a client's portal before an
integrator starts work. You receive the portal structure as YAML (pipelines, stages,
custom fields, departments; no CRM records) and the findings of automatic checks.

Find what automatic rules cannot: fields, pipelines or departments that do not fit the
business (leftovers of old projects, test or demo data), overlapping fields that mean
the same thing, confusing names, missing basics a company like this usually needs, and
structural decisions that will hurt reporting. Do not repeat the automatic findings.
Be specific: name the objects by their address and quote their names. Prefer a few
well-founded findings over many weak ones; say nothing if the structure is fine.
Write titles, explanations and recommendations in Russian.
"""

AI_SCHEMA = {
    "type": "object",
    "properties": {
        "business_guess": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "severity": {"type": "string", "enum": ["error", "warning", "info"]},
                    "address": {"type": "string"},
                    "title": {"type": "string"},
                    "recommendation": {"type": "string"},
                },
                "required": ["severity", "address", "title", "recommendation"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["business_guess", "findings"],
    "additionalProperties": False,
}


@dataclass
class AiReview:
    business_guess: str
    findings: list[Finding]
    usage: Any


def ai_review(
    client: Any,
    spec: PortalSpec,
    rule_findings: list[Finding],
    *,
    context: str = "",
    model: str,
    effort: str,
) -> AiReview:
    from crmbuilder.design import FALLBACK_BETA, Usage, _to_yaml

    structure = spec.model_dump(mode="json", exclude_defaults=True)
    found = "\n".join(f"- [{f.severity.value}] {f.address}: {f.message}" for f in rule_findings)
    content = [
        {"type": "text", "text": f"<portal_structure>\n{_to_yaml(structure)}</portal_structure>"},
        {"type": "text", "text": f"<automatic_findings>\n{found or 'none'}\n</automatic_findings>"},
    ]
    if context.strip():
        content.append({"type": "text", "text": f"<about_the_client>\n{context.strip()}\n"
                                                "</about_the_client>"})  # fmt: skip
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
    findings = [
        Finding(
            Severity(item["severity"]),
            "ai",
            item["address"],
            item["title"],
            item.get("recommendation", ""),
            source="ai",
        )
        for item in data.get("findings", [])
        if item.get("severity") in {s.value for s in Severity}
    ]
    findings.sort(key=lambda f: (_SEVERITY_ORDER[f.severity], f.address))
    return AiReview(data.get("business_guess", ""), findings, usage)


# --- reports -------------------------------------------------------------------------


def render_text(portal: str, findings: list[Finding], business: str = "") -> str:
    lines = [f"Аудит портала {portal}", ""]
    if business:
        lines += [f"Похоже на: {business}", ""]
    if not findings:
        return "\n".join(lines + ["Проблем не найдено."])
    for severity in Severity:
        group = [f for f in findings if f.severity is severity]
        if not group:
            continue
        lines.append(f"{_SEVERITY_TITLE[severity]} ({len(group)}):")
        for f in group:
            marker = " [ИИ]" if f.source == "ai" else ""
            lines.append(f"  • {f.message}{marker}")
            lines.append(f"    {f.address}")
            if f.hint:
                lines.append(f"    → {f.hint}")
        lines.append("")
    counts = ", ".join(
        f"{_SEVERITY_TITLE[s].lower()}: {n}"
        for s in Severity
        if (n := sum(f.severity is s for f in findings))
    )
    lines.append(f"Итого: {counts}.")
    return "\n".join(lines)


def render_markdown(portal: str, findings: list[Finding], business: str = "") -> str:
    lines = [f"# Аудит портала {portal}", ""]
    if business:
        lines += [f"**Профиль бизнеса (оценка ИИ):** {business}", ""]
    if not findings:
        return "\n".join(lines + ["Проблем не найдено."]) + "\n"
    for severity in Severity:
        group = [f for f in findings if f.severity is severity]
        if not group:
            continue
        lines += [f"## {_SEVERITY_TITLE[severity]} ({len(group)})", ""]
        for f in group:
            marker = " *(ИИ)*" if f.source == "ai" else ""
            lines.append(f"- **{f.message}**{marker}  ")
            lines.append(f"  `{f.address}`" + ("  " if f.hint else ""))
            if f.hint:
                lines.append(f"  {f.hint}")
        lines.append("")
    return "\n".join(lines)
