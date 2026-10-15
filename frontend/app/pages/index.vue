<script setup lang="ts">
import type { Project } from '~/types'

const { data: projects, error, pending } = await useAsyncData('projects', () =>
  api<Project[]>('/projects')
)

function formatDate(value: string | null) {
  return value ? new Date(value).toLocaleString('ru-RU') : 'ещё не импортирован'
}
</script>

<template>
  <div class="flex flex-col gap-6">
    <div class="flex items-center justify-between gap-4">
      <h1 class="text-2xl font-semibold">Проекты</h1>
      <B24Button color="air-primary" label="Новый проект" to="/projects/new" />
    </div>

    <B24Alert v-if="error" color="air-primary-alert" :description="errorText(error)" />

    <B24Empty
      v-else-if="!pending && !projects?.length"
      title="Пока нет проектов"
      description="Проект — это портал Битрикс24 и его вебхук. Добавьте первый, чтобы импортировать структуру."
      :actions="[{ label: 'Добавить портал', to: '/projects/new', color: 'air-primary' }]"
    />

    <div v-else class="grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
      <NuxtLink v-for="p in projects" :key="p.id" :to="`/projects/${p.id}`">
        <B24Card class="h-full transition hover:shadow-md">
          <div class="flex flex-col gap-2">
            <div class="text-lg font-semibold">{{ p.name }}</div>
            <div class="text-sm opacity-70">{{ p.portal }}</div>
            <div class="text-xs opacity-60">Импорт: {{ formatDate(p.imported_at) }}</div>
            <div v-if="p.counts" class="mt-2 flex flex-wrap gap-2">
              <B24Badge :label="`воронок: ${p.counts.deal_categories}`" />
              <B24Badge :label="`полей: ${p.counts.fields}`" />
              <B24Badge v-if="p.counts.smart_processes" :label="`смарт-процессов: ${p.counts.smart_processes}`" />
              <B24Badge v-if="p.allow_write" color="air-primary-warning" label="изменения разрешены" />
              <B24Badge
                v-if="p.counts.automation_pending"
                color="air-primary-warning"
                :label="`роботов настроить: ${p.counts.automation_pending}`"
              />
            </div>
          </div>
        </B24Card>
      </NuxtLink>
    </div>
  </div>
</template>
