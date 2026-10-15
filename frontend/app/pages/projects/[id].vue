<script setup lang="ts">
import type { PlanResult, Project, Spec } from '~/types'

const route = useRoute()
const id = Number(route.params.id)

const project = ref<Project | null>(null)
const spec = ref<Spec | null>(null)
const yaml = ref('')
const savedYaml = ref('')
const plan = ref<PlanResult | null>(null)
const warnings = ref<string[]>([])
const error = ref('')
const notice = ref('')
const busy = ref<'' | 'import' | 'plan' | 'save' | 'settings' | 'delete'>('')
const tab = ref('structure')

const settingsName = ref('')
const settingsWebhook = ref('')

async function load() {
  try {
    project.value = await api<Project>(`/projects/${id}`)
    settingsName.value = project.value.name
    if (project.value.imported_at) await loadSpec()
  } catch (e) {
    error.value = errorText(e)
  }
}

async function loadSpec() {
  const data = await api<{ spec: Spec, yaml: string }>(`/projects/${id}/spec`)
  spec.value = data.spec
  yaml.value = savedYaml.value = data.yaml
}

async function run(kind: typeof busy.value, action: () => Promise<void>) {
  error.value = ''
  notice.value = ''
  busy.value = kind
  try {
    await action()
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = ''
  }
}

function importPortal() {
  return run('import', async () => {
    const result = await api<{ project: Project, warnings: string[] }>(
      `/projects/${id}/import`, { method: 'POST' }
    )
    project.value = result.project
    warnings.value = result.warnings
    plan.value = null
    await loadSpec()
    notice.value = 'Структура портала загружена.'
    tab.value = 'structure'
  })
}

function makePlan() {
  return run('plan', async () => {
    plan.value = await api<PlanResult>(`/projects/${id}/plan`, { method: 'POST' })
    tab.value = 'plan'
  })
}

function saveSpec() {
  return run('save', async () => {
    const data = await api<{ spec: Spec, yaml: string }>(`/projects/${id}/spec`, {
      method: 'PUT',
      body: { yaml: yaml.value }
    })
    spec.value = data.spec
    yaml.value = savedYaml.value = data.yaml
    plan.value = null
    notice.value = 'Спецификация сохранена. Нажмите «Показать план», чтобы увидеть изменения.'
  })
}

function saveSettings() {
  return run('settings', async () => {
    const body: Record<string, string> = { name: settingsName.value }
    if (settingsWebhook.value.trim()) body.webhook = settingsWebhook.value
    project.value = await api<Project>(`/projects/${id}`, { method: 'PATCH', body })
    settingsWebhook.value = ''
    notice.value = 'Настройки сохранены.'
  })
}

function removeProject() {
  if (!confirm(`Удалить проект «${project.value?.name}»? Портал не изменится.`)) return
  return run('delete', async () => {
    await api(`/projects/${id}`, { method: 'DELETE' })
    await navigateTo('/')
  })
}

const tabs = computed(() => [
  { label: 'Структура', value: 'structure', slot: 'structure' as const },
  { label: 'План', value: 'plan', slot: 'plan' as const },
  { label: 'Спецификация', value: 'spec', slot: 'spec' as const },
  { label: 'Настройки', value: 'settings', slot: 'settings' as const }
])

onMounted(load)
</script>

<template>
  <div class="flex flex-col gap-5">
    <NuxtLink to="/" class="text-sm opacity-70 hover:opacity-100">← Все проекты</NuxtLink>

    <div v-if="project" class="flex flex-wrap items-start justify-between gap-4">
      <div>
        <h1 class="text-2xl font-semibold">{{ project.name }}</h1>
        <div class="text-sm opacity-70">
          {{ project.portal }} ·
          {{ project.imported_at
            ? `импорт ${new Date(project.imported_at).toLocaleString('ru-RU')}`
            : 'ещё не импортирован' }}
        </div>
      </div>
      <div class="flex flex-wrap gap-2">
        <B24Button
          :color="project.imported_at ? 'air-secondary' : 'air-primary'"
          :loading="busy === 'import'"
          :disabled="!!busy"
          :label="project.imported_at ? 'Обновить с портала' : 'Импортировать портал'"
          @click="importPortal"
        />
        <B24Button
          v-if="project.imported_at"
          color="air-primary"
          :loading="busy === 'plan'"
          :disabled="!!busy"
          label="Показать план"
          @click="makePlan"
        />
      </div>
    </div>

    <B24Alert v-if="error" color="air-primary-alert" :description="error" close @update:open="error = ''" />
    <B24Alert v-if="notice" color="air-primary-success" :description="notice" close @update:open="notice = ''" />
    <B24Alert v-for="w in warnings" :key="w" color="air-primary-warning" :description="w" />

    <B24Empty
      v-if="project && !project.imported_at"
      title="Портал ещё не импортирован"
      description="Импорт только читает портал: воронки, стадии, поля, смарт-процессы и отделы. Ничего не меняется."
    />

    <B24Tabs v-else-if="project" v-model="tab" :items="tabs" class="w-full">
      <template #structure>
        <div class="pt-4">
          <StructureView v-if="spec" :spec="spec" />
        </div>
      </template>

      <template #plan>
        <div class="pt-4">
          <PlanView v-if="plan" :result="plan" />
          <B24Empty
            v-else
            title="План ещё не построен"
            description="План сравнивает портал со спецификацией и показывает, что изменится. Портал при этом не меняется."
            :actions="[{ label: 'Показать план', color: 'air-primary', onClick: makePlan }]"
          />
        </div>
      </template>

      <template #spec>
        <div class="flex flex-col gap-3 pt-4">
          <p class="text-sm opacity-70">
            Желаемая конфигурация портала в формате YAML. После сохранения посмотрите план:
            изменения на портал пока применяются через консоль (`crmbuilder apply`).
          </p>
          <B24Textarea
            v-model="yaml"
            :rows="28"
            autoresize
            class="w-full"
            :b24ui="{ base: 'font-mono text-sm' }"
          />
          <div class="flex gap-2">
            <B24Button
              color="air-primary"
              :loading="busy === 'save'"
              :disabled="!!busy || yaml === savedYaml"
              label="Сохранить"
              @click="saveSpec"
            />
            <B24Button :disabled="yaml === savedYaml" label="Отменить правки" @click="yaml = savedYaml" />
          </div>
        </div>
      </template>

      <template #settings>
        <div class="flex max-w-xl flex-col gap-4 pt-4">
          <B24FormField label="Название">
            <B24Input v-model="settingsName" class="w-full" />
          </B24FormField>
          <B24FormField
            label="Новый вебхук"
            description="Оставьте пустым, чтобы не менять. Вебхук должен быть от того же портала."
          >
            <B24Input v-model="settingsWebhook" class="w-full" placeholder="https://..." />
          </B24FormField>
          <div class="flex flex-wrap gap-2">
            <B24Button color="air-primary" :loading="busy === 'settings'" label="Сохранить" @click="saveSettings" />
            <B24Button color="air-secondary-alert" :loading="busy === 'delete'" label="Удалить проект" @click="removeProject" />
          </div>
        </div>
      </template>
    </B24Tabs>
  </div>
</template>
