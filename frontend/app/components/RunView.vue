<script setup lang="ts">
import type { Change, RunDetail } from '~/types'
import { ACTION_TITLES, RUN_KIND, RUN_STATUS } from '~/labels'

const props = defineProps<{ run: RunDetail }>()
defineEmits<{ rollback: [] }>()

type Row = { change: Change, state: 'done' | 'failed' | 'current' | 'pending' }

// Carried-out changes in execution order, then the failed one, then what is left.
const rows = computed<Row[]>(() => {
  const changes = props.run.plan?.changes || []
  const byAddress = new Map(changes.map(c => [c.address, c]))
  const result: Row[] = []
  for (const address of props.run.done_addresses) {
    const change = byAddress.get(address)
    if (change) result.push({ change, state: 'done' })
  }
  const seen = new Set(props.run.done_addresses)
  if (props.run.failed && byAddress.has(props.run.failed)) {
    result.push({ change: byAddress.get(props.run.failed)!, state: 'failed' })
    seen.add(props.run.failed)
  }
  let first = props.run.status === 'running'
  for (const change of changes) {
    if (seen.has(change.address)) continue
    result.push({ change, state: first ? 'current' : 'pending' })
    first = false
  }
  return result
})

const ICONS = { done: '✅', failed: '❌', current: '⏳', pending: '⚪' }
const status = computed(() => RUN_STATUS[props.run.status] || RUN_STATUS.failed!)
const showJournal = ref(false)
</script>

<template>
  <div class="flex flex-col gap-3">
    <div class="flex flex-wrap items-center gap-2">
      <span class="font-semibold">{{ RUN_KIND[run.kind] }} №{{ run.id }}</span>
      <B24Badge :color="status.color as never" :label="status.label" />
      <span class="text-sm opacity-60">
        {{ new Date(run.created_at).toLocaleString('ru-RU') }}
        <template v-if="run.rollback_of"> · откат применения №{{ run.rollback_of }}</template>
      </span>
      <span v-if="run.total" class="text-sm opacity-60">· {{ run.done }} из {{ run.total }}</span>
    </div>

    <B24Progress
      v-if="run.status === 'running' || run.status === 'pending'"
      :model-value="run.total ? Math.round((run.done / run.total) * 100) : null"
    />

    <B24Alert v-if="run.error" color="air-primary-alert" :description="run.error" />
    <B24Alert v-for="w in run.warnings" :key="w" color="air-primary-warning" :description="w" />

    <ul v-if="rows.length" class="flex flex-col gap-1 text-sm">
      <li v-for="row in rows" :key="row.change.address" class="flex items-start gap-2">
        <span>{{ ICONS[row.state] }}</span>
        <span class="opacity-60">{{ ACTION_TITLES[row.change.action] }}</span>
        <code class="break-all">{{ row.change.address }}</code>
      </li>
    </ul>

    <div v-if="run.residual" class="text-sm">
      <p class="font-medium">После применения портал всё ещё отличается:</p>
      <ul class="list-disc pl-5">
        <li v-for="c in run.residual.changes" :key="c.address">
          {{ ACTION_TITLES[c.action] }} <code>{{ c.address }}</code>
        </li>
      </ul>
    </div>

    <div class="flex flex-wrap gap-2">
      <B24Button
        v-if="run.can_rollback"
        size="sm"
        color="air-secondary-alert"
        label="Откатить"
        @click="$emit('rollback')"
      />
      <B24Button
        v-if="run.journal_total"
        size="sm"
        :label="showJournal ? 'Скрыть журнал' : `Журнал вызовов (${run.journal_total})`"
        @click="showJournal = !showJournal"
      />
    </div>
    <ol v-if="showJournal" class="max-h-80 overflow-auto rounded border border-(--ui-color-divider-default) p-2 font-mono text-xs">
      <li v-for="(entry, i) in run.journal" :key="i" :class="entry.error ? 'text-red-600' : ''">
        {{ entry.time?.slice(11, 19) }} {{ entry.method }}<template v-if="entry.error"> — {{ entry.error }}</template>
      </li>
    </ol>
  </div>
</template>
