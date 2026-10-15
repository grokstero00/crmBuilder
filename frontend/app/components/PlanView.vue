<script setup lang="ts">
import type { Change, PlanResult } from '~/types'
import { ACTION_TITLES } from '~/labels'

defineProps<{ result: PlanResult }>()

const COLORS = {
  create: 'air-primary-success',
  update: 'air-primary',
  delete: 'air-primary-alert',
  replace: 'air-primary-alert'
} as const

function show(value: unknown): string {
  return value === null || value === undefined ? '—' : JSON.stringify(value)
}

function attributes(change: Change) {
  return Object.entries(change.attributes)
}
</script>

<template>
  <div class="flex flex-col gap-4">
    <B24Alert
      v-for="w in result.warnings"
      :key="w"
      color="air-primary-warning"
      :description="w"
    />

    <B24Alert
      v-if="!result.plan.changes.length"
      color="air-primary-success"
      title="Изменений нет"
      description="Портал совпадает со спецификацией."
    />
    <template v-else>
      <div class="flex flex-wrap gap-2">
        <template v-for="(count, action) in result.plan.summary" :key="action">
          <B24Badge v-if="count" :color="COLORS[action as Change['action']]" :label="`${ACTION_TITLES[action]}: ${count}`" />
        </template>
      </div>
      <B24Alert
        v-if="result.plan.changes.some(c => c.destructive)"
        color="air-primary-alert"
        title="Есть опасные изменения"
        description="Удаление и пересоздание могут привести к потере данных на портале."
      />
      <B24Card v-for="c in result.plan.changes" :key="c.address + c.action">
        <div class="flex flex-col gap-1.5">
          <div class="flex flex-wrap items-center gap-2">
            <B24Badge :color="COLORS[c.action]" :label="ACTION_TITLES[c.action]" />
            <code class="text-sm break-all">{{ c.address }}</code>
          </div>
          <ul v-if="attributes(c).length" class="ml-1 text-sm">
            <li v-for="[name, v] in attributes(c)" :key="name">
              <span class="mr-1 opacity-60">{{ name }}:</span>
              <span v-if="c.action === 'create'">{{ show(v.desired) }}</span>
              <span v-else>{{ show(v.current) }} → <b>{{ show(v.desired) }}</b></span>
            </li>
          </ul>
          <div v-if="c.note" class="text-sm" :class="c.destructive ? 'text-red-600' : 'opacity-70'">
            {{ c.note }}
          </div>
        </div>
      </B24Card>
    </template>

    <B24Card v-if="result.manual.length" title="Настроить вручную">
      <p class="mb-2 text-sm opacity-70">
        Роботы и триггеры не настраиваются через API: после применения добавьте их в Битрикс24.
      </p>
      <ul class="list-disc pl-5 text-sm">
        <li v-for="m in result.manual" :key="m.key">{{ m.title }}</li>
      </ul>
    </B24Card>
  </div>
</template>
