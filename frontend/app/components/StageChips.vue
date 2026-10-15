<script setup lang="ts">
import type { Category } from '~/types'

defineProps<{ category: Category }>()

const COLORS = {
  process: 'air-tertiary',
  success: 'air-primary-success',
  failure: 'air-primary-alert'
} as const
</script>

<template>
  <div class="flex flex-wrap items-center gap-1.5">
    <template v-for="(stage, i) in category.stages" :key="stage.key">
      <span v-if="i > 0 && stage.semantic !== 'failure'" class="opacity-40">→</span>
      <B24Badge :color="COLORS[stage.semantic]" :label="stage.name" />
    </template>
  </div>
</template>
