<script setup lang="ts">
import type { AutomationRule, Department, Spec } from '~/types'
import { ENTITY_TITLES, FIELD_TYPES, ROBOT_TITLES, TRIGGER_TITLES } from '~/labels'

const props = defineProps<{ spec: Spec }>()

const fieldGroups = computed(() =>
  Object.entries(props.spec.crm.fields)
    .filter(([, fields]) => fields.length)
    .map(([entity, fields]) => ({ entity, title: ENTITY_TITLES[entity] || entity, fields }))
)

// Departments as an indented list, parents first.
const departments = computed(() => {
  const children = new Map<string | null, Department[]>()
  for (const d of props.spec.structure.departments) {
    const list = children.get(d.parent) || []
    list.push(d)
    children.set(d.parent, list)
  }
  const rows: { dep: Department, depth: number }[] = []
  const walk = (parent: string | null, depth: number) => {
    for (const dep of children.get(parent) || []) {
      rows.push({ dep, depth })
      walk(dep.key, depth + 1)
    }
  }
  walk(null, 0)
  return rows
})

function stageName(address: string): string {
  const p = address.split('.')
  const categories = p[1] === 'deal_categories'
    ? props.spec.crm.deal_categories
    : props.spec.crm.smart_processes.find(s => s.key === p[2])?.categories || []
  const catKey = p[1] === 'deal_categories' ? p[2] : p[4]
  const stageKey = p[p.length - 1]
  const category = categories.find(c => c.key === catKey)
  const stage = category?.stages.find(s => s.key === stageKey)
  return category && stage ? `${category.name} → ${stage.name}` : address
}

function ruleType(rule: AutomationRule): string {
  return rule.kind === 'robot'
    ? `Робот «${ROBOT_TITLES[rule.action || 'other']}»`
    : `Триггер «${TRIGGER_TITLES[rule.event || 'other']}»`
}
</script>

<template>
  <div class="flex flex-col gap-6">
    <B24Card title="Воронки сделок">
      <div v-if="!spec.crm.deal_categories.length" class="opacity-70">Нет воронок</div>
      <div v-for="c in spec.crm.deal_categories" :key="c.key" class="mb-4 last:mb-0">
        <div class="mb-1.5 font-medium">
          {{ c.name }}
          <span v-if="c.default" class="text-sm font-normal opacity-60">(основная)</span>
        </div>
        <StageChips :category="c" />
      </div>
    </B24Card>

    <B24Card v-for="sp in spec.crm.smart_processes" :key="sp.key" :title="`Смарт-процесс «${sp.title}»`">
      <div class="mb-3 flex flex-wrap gap-2 text-sm">
        <B24Badge v-if="sp.client_enabled" label="привязка к клиентам" />
        <B24Badge :label="sp.automation_enabled ? 'роботы включены' : 'роботы выключены'" />
      </div>
      <div v-if="!sp.stages_enabled" class="opacity-70">Без стадий</div>
      <template v-else>
        <div v-for="c in sp.categories" :key="c.key" class="mb-4 last:mb-0">
          <div v-if="sp.categories.length > 1" class="mb-1.5 font-medium">{{ c.name }}</div>
          <StageChips :category="c" />
        </div>
      </template>
    </B24Card>

    <B24Card v-for="g in fieldGroups" :key="g.entity" :title="`Поля: ${g.title}`">
      <div class="overflow-x-auto">
        <table class="w-full text-left text-sm">
          <thead class="opacity-60">
            <tr>
              <th class="py-1.5 pr-4 font-medium">Поле</th>
              <th class="py-1.5 pr-4 font-medium">Тип</th>
              <th class="py-1.5 font-medium">Варианты</th>
            </tr>
          </thead>
          <tbody>
            <tr v-for="f in g.fields" :key="f.key" class="border-t border-(--ui-color-divider-default)">
              <td class="py-1.5 pr-4">
                {{ f.label }}<span v-if="f.required" class="text-red-500"> *</span>
              </td>
              <td class="py-1.5 pr-4">
                {{ FIELD_TYPES[f.type] || f.type }}<span v-if="f.multiple">, несколько</span>
              </td>
              <td class="py-1.5">{{ f.values.map(v => v.value).join(', ') }}</td>
            </tr>
          </tbody>
        </table>
      </div>
    </B24Card>

    <B24Card v-if="spec.automation.length" title="Роботы и триггеры">
      <p class="mb-3 text-sm opacity-70">
        Битрикс24 не даёт настраивать роботов через API: они настраиваются вручную по отчёту.
      </p>
      <ul class="flex flex-col gap-2">
        <li v-for="r in spec.automation" :key="r.key" class="flex items-start gap-2">
          <span>{{ r.done ? '✅' : '⬜' }}</span>
          <div>
            <div class="font-medium">{{ r.title }}</div>
            <div class="text-sm opacity-70">{{ ruleType(r) }} · {{ stageName(r.stage) }}<span v-if="r.when"> · {{ r.when }}</span></div>
          </div>
        </li>
      </ul>
    </B24Card>

    <B24Card v-if="departments.length" title="Структура компании">
      <ul class="text-sm">
        <li v-for="row in departments" :key="row.dep.key" :style="{ paddingLeft: `${row.depth * 1.25}rem` }" class="py-0.5">
          {{ row.dep.name }}
        </li>
      </ul>
    </B24Card>
  </div>
</template>
