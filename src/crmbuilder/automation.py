"""Robots and triggers on stages: the manual part of the setup.

Bitrix24 REST cannot read or configure stage robots and triggers (only
applications can register their own robot types, and placing any robot on a
stage is UI-only). So the ``automation`` section of the spec is a checklist:
``import`` carries it over from the previous spec instead of reading it,
``plan``/``apply`` remind which rules are still pending, and the report turns
them into step-by-step instructions for the Bitrix24 interface.
"""

from __future__ import annotations

from dataclasses import dataclass

from crmbuilder.spec.models import (
    AutomationKind,
    AutomationRule,
    PortalSpec,
    RobotAction,
    TriggerEvent,
    automation_target_problem,
    resolve_stage,
)

# Names as the Bitrix24 interface shows them (ru).
ROBOT_TITLES = {
    RobotAction.task: "Поставить задачу",
    RobotAction.notify: "Уведомление сотруднику",
    RobotAction.sms: "Отправить SMS клиенту",
    RobotAction.email: "Отправить письмо клиенту",
    RobotAction.activity: "Запланировать дело (звонок, встречу)",
    RobotAction.change_responsible: "Сменить ответственного",
    RobotAction.change_stage: "Сменить стадию",
    RobotAction.update_fields: "Изменить элемент",
    RobotAction.create_document: "Создать документ",
    RobotAction.comment: "Добавить комментарий",
    RobotAction.webhook: "Исходящий вебхук",
    RobotAction.other: "Другой робот",
}
TRIGGER_TITLES = {
    TriggerEvent.webform: "Заполнение CRM-формы",
    TriggerEvent.call: "Входящий звонок",
    TriggerEvent.missed_call: "Пропущенный звонок",
    TriggerEvent.email: "Входящее письмо",
    TriggerEvent.openline: "Сообщение в открытую линию",
    TriggerEvent.payment: "Оплата",
    TriggerEvent.field_changed: "Изменение полей",
    TriggerEvent.webhook: "Входящий вебхук",
    TriggerEvent.other: "Другой триггер",
}


def rule_type_title(rule: AutomationRule) -> str:
    if rule.kind is AutomationKind.robot:
        return f"робот «{ROBOT_TITLES[rule.action]}»"
    return f"триггер «{TRIGGER_TITLES[rule.event]}»"


def carry_over(new: PortalSpec, old: PortalSpec | None) -> tuple[PortalSpec, list[str]]:
    """Copy ``old.automation`` into ``new`` (an imported spec, which never has it).

    Rules whose stage is gone are dropped; the returned warnings name them.
    """
    if old is None or not old.automation:
        return new, []
    kept, warnings = [], []
    for rule in old.automation:
        problem = automation_target_problem(new, rule.stage)
        if problem:
            warnings.append(f"automation rule {rule.key!r} dropped: {problem}")
        else:
            kept.append(rule)
    return new.model_copy(update={"automation": kept}), warnings


def pending(spec: PortalSpec) -> list[AutomationRule]:
    return [rule for rule in spec.automation if not rule.done]


def render_pending(spec: PortalSpec) -> str:
    """A reminder for plan/apply output; empty when nothing is left to configure."""
    rules = pending(spec)
    if not rules:
        return ""
    lines = [
        f"Manual: {len(rules)} robot/trigger rule(s) to configure in the Bitrix24 interface "
        "(REST cannot do it; `crmbuilder report` writes the steps, set done: true when finished):"
    ]
    lines += [f"  * {rule.key}: {rule.title}  [{rule.stage}]" for rule in rules]
    return "\n".join(lines)


@dataclass
class _Group:
    title: str
    path: str
    rules: list[tuple[int, AutomationRule, str]]  # (stage position, rule, stage name)


def _groups(spec: PortalSpec) -> list[_Group]:
    groups: dict[tuple[str, str], _Group] = {}
    for rule in spec.automation:
        found = resolve_stage(spec, rule.stage)
        if found is None:
            continue
        sp, category, stage = found
        position = category.stages.index(stage)
        if sp is None:
            ident = ("", category.key)
            several = len(spec.crm.deal_categories) > 1
            title = f"Сделки, воронка «{category.name}»"
            path = "Откройте CRM → Сделки" + (
                f", выберите воронку «{category.name}»" if several else ""
            )
        else:
            ident = (sp.key, category.key)
            several = len(sp.categories) > 1
            title = f"Смарт-процесс «{sp.title}»" + (
                f", воронка «{category.name}»" if several else ""
            )
            path = f"Откройте CRM → Смарт-процессы → «{sp.title}»" + (
                f", выберите воронку «{category.name}»" if several else ""
            )
        group = groups.setdefault(ident, _Group(title, path, []))
        group.rules.append((position, rule, stage.name))
    for group in groups.values():
        group.rules.sort(key=lambda item: item[0])
    return list(groups.values())


def render_instructions(spec: PortalSpec) -> list[str]:
    """Markdown section with the robots and triggers to set up by hand."""
    if not spec.automation:
        return []
    lines = [
        "## Роботы и триггеры",
        "",
        "Битрикс24 не даёт настраивать роботов через API, поэтому их добавляют вручную. "
        "Робот срабатывает, когда элемент попадает на стадию; триггер переводит элемент "
        "на стадию, когда происходит событие.",
        "",
    ]
    for group in _groups(spec):
        lines += [f"### {group.title}", ""]
        lines += [
            f"{group.path} и нажмите «Роботы». Для каждого пункта: на нужной стадии нажмите "
            "«+», выберите робота или триггер, заполните настройки и сохраните.",
            "",
        ]
        for n, (_, rule, stage_name) in enumerate(group.rules, 1):
            mark = "✅" if rule.done else "⬜"
            lines.append(
                f"{n}. {mark} Стадия «{stage_name}» — {rule_type_title(rule)}: {rule.title}"
            )
            details = [
                ("Когда", rule.when),
                ("Условие", rule.condition),
                ("Ответственный", rule.responsible),
                ("Настройки", rule.details),
            ]
            lines += [f"   - {name}: {value}" for name, value in details if value]
        lines.append("")
    lines += ["Настроенные пункты отмечайте в спецификации `done: true`.", ""]
    return lines
