<script setup lang="ts">
import type { JobDetail } from '~/types'
import { JOB_STATUS } from '~/labels'
import { formatDateTime, formatUsage } from '~/utils/format'

defineProps<{ job: JobDetail<unknown>, label: string }>()
</script>

<template>
  <div class="flex flex-wrap items-center gap-2">
    <span class="font-semibold">{{ label }} №{{ job.id }}</span>
    <B24Badge
      :color="JOB_STATUS[job.status]?.color || 'air-secondary'"
      :label="JOB_STATUS[job.status]?.label || job.status"
    />
    <span class="text-sm opacity-60">{{ formatDateTime(job.created_at) }}</span>
    <span v-if="formatUsage(job.usage)" class="text-sm opacity-60" :title="job.usage?.model">
      · {{ formatUsage(job.usage) }}
    </span>
  </div>
</template>
