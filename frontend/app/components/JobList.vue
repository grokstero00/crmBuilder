<script setup lang="ts">
import type { JobSummary } from '~/types'
import { JOB_STATUS } from '~/labels'
import { formatCost, formatDateTime } from '~/utils/format'

defineProps<{ title: string, jobs: JobSummary[], activeId: number | null }>()
const emit = defineEmits<{ open: [jobId: number] }>()
</script>

<template>
  <B24Card v-if="jobs.length" :title="title">
    <ul class="flex flex-col divide-y divide-(--ui-color-divider-default)">
      <li v-for="j in jobs" :key="j.id">
        <button
          type="button"
          class="flex w-full flex-wrap items-center gap-2 rounded px-1 py-2 text-left hover:bg-(--ui-color-bg-content-secondary)"
          :class="j.id === activeId ? 'bg-(--ui-color-bg-content-secondary)' : ''"
          @click="emit('open', j.id)"
        >
          <span class="min-w-0 flex-1 truncate font-medium">{{ j.title }}</span>
          <B24Badge
            :color="JOB_STATUS[j.status]?.color || 'air-secondary'"
            :label="JOB_STATUS[j.status]?.label || j.status"
          />
          <span class="text-sm opacity-60">{{ formatDateTime(j.created_at) }}</span>
          <span v-if="j.cost_usd !== null" class="text-sm opacity-60">· {{ formatCost(j.cost_usd) }}</span>
        </button>
      </li>
    </ul>
  </B24Card>
</template>
