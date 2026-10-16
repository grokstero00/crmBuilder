<script setup lang="ts">
import type { AiConfig, PlanResult, Project, RunDetail, RunSummary, Spec } from '~/types'
import { FINAL_STATUSES, RUN_KIND, RUN_STATUS } from '~/labels'

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

// Apply history; the opened run is polled while it is still going.
const runs = ref<RunSummary[]>([])
const openRun = ref<RunDetail | null>(null)
let poller: ReturnType<typeof setInterval> | null = null

// The confirmation dialog serves both apply and rollback.
const confirmOpen = ref(false)
const confirmMode = ref<'apply' | 'rollback'>('apply')
const confirmPlan = ref<PlanResult | null>(null)
const confirmRunId = ref<number | null>(null)
const confirmBusy = ref(false)
const confirmError = ref('')

// AI availability and models, shared by the designer, audit and report tabs.
const ai = ref<AiConfig | null>(null)

const writeOpen = ref(false)
const writeTyped = ref('')
const writeError = ref('')

async function load() {
  api<AiConfig>('/ai').then((data) => { ai.value = data }).catch(() => {})
  try {
    project.value = await api<Project>(`/projects/${id}`)
    settingsName.value = project.value.name
    if (project.value.imported_at) await loadSpec()
    await loadRuns()
    const active = runs.value.find(r => !FINAL_STATUSES.has(r.status))
    if (active) await showRun(active.id)
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

const specDirty = computed(() => yaml.value !== savedYaml.value)

// The designer wrote its result into the spec: show what that means for the portal.
async function onDesignSaved(saved: Project) {
  project.value = saved
  plan.value = null
  tab.value = 'plan'
  await run('plan', async () => {
    await loadSpec()
    plan.value = await api<PlanResult>(`/projects/${id}/plan`, { method: 'POST' })
    notice.value = 'Результат проектировщика сохранён в спецификацию. Ниже план: что изменится на портале.'
  })
}

async function loadRuns() {
  runs.value = await api<RunSummary[]>(`/projects/${id}/runs`)
}

function stopPolling() {
  if (poller) clearInterval(poller)
  poller = null
}

async function showRun(runId: number) {
  stopPolling()
  openRun.value = await api<RunDetail>(`/projects/${id}/runs/${runId}`)
  tab.value = 'history'
  if (FINAL_STATUSES.has(openRun.value.status)) return
  poller = setInterval(async () => {
    try {
      openRun.value = await api<RunDetail>(`/projects/${id}/runs/${runId}`)
    } catch {
      return // a missed poll is retried on the next tick
    }
    if (FINAL_STATUSES.has(openRun.value.status)) {
      stopPolling()
      await loadRuns()
      // A rollback rewrites the spec; any shown plan is out of date after a run.
      if (openRun.value.kind === 'rollback') await loadSpec()
      plan.value = null
    }
  }, 1000)
}

onBeforeUnmount(stopPolling)

function askApply() {
  confirmMode.value = 'apply'
  confirmPlan.value = plan.value
  confirmError.value = ''
  confirmOpen.value = true
}

function askRollback(runId: number) {
  return run('plan', async () => {
    confirmPlan.value = await api<PlanResult>(`/projects/${id}/runs/${runId}/rollback-plan`, {
      method: 'POST'
    })
    confirmMode.value = 'rollback'
    confirmRunId.value = runId
    confirmError.value = ''
    confirmOpen.value = true
  })
}

const confirmDestructive = computed(() =>
  !!confirmPlan.value?.plan.changes.some(c => c.destructive)
)

async function startRun(allowDestructive: boolean, typed: string) {
  if (!confirmPlan.value) return
  confirmBusy.value = true
  confirmError.value = ''
  try {
    const path = confirmMode.value === 'apply'
      ? `/projects/${id}/apply`
      : `/projects/${id}/runs/${confirmRunId.value}/rollback`
    const started = await api<RunDetail>(path, {
      method: 'POST',
      body: {
        confirm: typed,
        fingerprint: confirmPlan.value.fingerprint,
        allow_destructive: allowDestructive
      }
    })
    confirmOpen.value = false
    plan.value = null
    await loadRuns()
    await showRun(started.id)
  } catch (e) {
    confirmError.value = errorText(e)
  } finally {
    confirmBusy.value = false
  }
}

function toggleWrite(value: boolean) {
  if (value) {
    writeTyped.value = ''
    writeError.value = ''
    writeOpen.value = true
    return
  }
  return run('settings', async () => {
    project.value = await api<Project>(`/projects/${id}`, {
      method: 'PATCH',
      body: { allow_write: false }
    })
    notice.value = 'Изменения на портале запрещены.'
  })
}

async function enableWrite() {
  writeError.value = ''
  try {
    project.value = await api<Project>(`/projects/${id}`, {
      method: 'PATCH',
      body: { allow_write: true, confirm: writeTyped.value.trim() }
    })
    writeOpen.value = false
    notice.value = 'Изменения на портале разрешены. Применение всегда требует подтверждения.'
  } catch (e) {
    writeError.value = errorText(e)
  }
}

const tabs = computed(() => [
  { label: 'Структура', value: 'structure', slot: 'structure' as const },
  { label: 'План', value: 'plan', slot: 'plan' as const },
  {
    label: 'История',
    value: 'history',
    slot: 'history' as const,
    badge: runs.value.length || undefined
  },
  { label: 'Проектировщик', value: 'design', slot: 'design' as const },
  { label: 'Аудит', value: 'audit', slot: 'audit' as const },
  { label: 'Отчёт', value: 'report', slot: 'report' as const },
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
          <div v-if="plan" class="flex flex-col gap-4">
            <PlanView :result="plan" />
            <B24Card v-if="plan.blockers.length" title="Эти изменения Битрикс24 не позволяет сделать">
              <ul class="list-disc pl-5 text-sm">
                <li v-for="b in plan.blockers" :key="b.address">
                  <code>{{ b.address }}</code>: {{ b.reason }}
                </li>
              </ul>
              <p class="mt-2 text-sm opacity-70">Исправьте спецификацию и постройте план заново.</p>
            </B24Card>
            <B24Card v-else-if="plan.plan.changes.length">
              <div v-if="!project.allow_write" class="flex flex-wrap items-center justify-between gap-3">
                <p class="text-sm">
                  Изменения на портале для этого проекта запрещены. Их можно разрешить в настройках.
                </p>
                <B24Button size="sm" label="Открыть настройки" @click="tab = 'settings'" />
              </div>
              <div v-else class="flex flex-wrap items-center justify-between gap-3">
                <p class="text-sm">
                  Перед записью план будет проверен ещё раз. Каждое применение попадает в историю,
                  его можно откатить.
                </p>
                <B24Button color="air-primary" label="Применить на портал" @click="askApply" />
              </div>
            </B24Card>
          </div>
          <B24Empty
            v-else
            title="План ещё не построен"
            description="План сравнивает портал со спецификацией и показывает, что изменится. Портал при этом не меняется."
            :actions="[{ label: 'Показать план', color: 'air-primary', onClick: makePlan }]"
          />
        </div>
      </template>

      <template #history>
        <div class="flex flex-col gap-4 pt-4">
          <B24Card v-if="openRun">
            <RunView :run="openRun" @rollback="askRollback(openRun.id)" />
          </B24Card>
          <B24Empty
            v-if="!runs.length"
            title="Применений ещё не было"
            description="Здесь появится каждое применение и откат: что изменилось, журнал вызовов и кнопка отката."
          />
          <B24Card v-else title="Все запуски">
            <ul class="flex flex-col divide-y divide-(--ui-color-divider-default)">
              <li v-for="r in runs" :key="r.id" class="flex flex-wrap items-center gap-2 py-2">
                <span class="font-medium">{{ RUN_KIND[r.kind] }} №{{ r.id }}</span>
                <B24Badge :color="(RUN_STATUS[r.status]?.color || 'air-secondary') as never" :label="RUN_STATUS[r.status]?.label || r.status" />
                <span class="text-sm opacity-60">{{ new Date(r.created_at).toLocaleString('ru-RU') }}</span>
                <span v-if="r.total" class="text-sm opacity-60">· {{ r.done }} из {{ r.total }}</span>
                <B24Button class="ml-auto" size="sm" label="Подробнее" @click="showRun(r.id)" />
              </li>
            </ul>
          </B24Card>
        </div>
      </template>

      <template #design>
        <div class="pt-4">
          <DesignPanel
            :project-id="id"
            :project="project"
            :ai="ai"
            :spec-dirty="specDirty"
            @saved="onDesignSaved"
          />
        </div>
      </template>

      <template #audit>
        <div class="pt-4">
          <AuditPanel :project-id="id" :project="project" :ai="ai" />
        </div>
      </template>

      <template #report>
        <div class="pt-4">
          <ReportPanel :project-id="id" :project="project" :ai="ai" :runs="runs" />
        </div>
      </template>

      <template #spec>
        <div class="flex flex-col gap-3 pt-4">
          <p class="text-sm opacity-70">
            Желаемая конфигурация портала в формате YAML. После сохранения постройте план:
            он покажет, что изменится, и позволит применить изменения.
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
          <B24Card title="Изменения на портале">
            <div class="flex flex-col gap-3">
              <p class="text-sm opacity-80">
                Пока выключено, сервис только читает портал: импорт и план. Для рабочих порталов
                клиентов, которые нельзя трогать, оставьте выключенным.
              </p>
              <B24Switch
                :model-value="project.allow_write"
                label="Разрешить изменения на портале"
                :loading="busy === 'settings'"
                @update:model-value="toggleWrite"
              />
            </div>
          </B24Card>
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

    <ConfirmRun
      v-if="project"
      v-model:open="confirmOpen"
      :title="confirmMode === 'apply' ? 'Применить изменения на портал' : 'Откатить применение'"
      :portal="project.portal"
      :destructive="confirmDestructive"
      :busy="confirmBusy"
      :error="confirmError"
      @confirm="startRun"
    >
      <p v-if="confirmPlan && !confirmPlan.plan.changes.length" class="text-sm">
        Портал уже совпадает с целью: менять нечего.
      </p>
      <div v-else-if="confirmPlan" class="max-h-72 overflow-auto">
        <PlanView :result="confirmPlan" />
      </div>
    </ConfirmRun>

    <B24Modal v-if="project" v-model:open="writeOpen" title="Разрешить изменения на портале">
      <template #body>
        <div class="flex flex-col gap-4">
          <p class="text-sm">
            Сервис сможет менять воронки, стадии, поля, смарт-процессы и отделы на портале
            {{ project.portal }}. Каждое применение всё равно нужно будет подтверждать.
          </p>
          <B24FormField :label="`Введите имя портала: ${project.portal}`" required>
            <B24Input v-model="writeTyped" class="w-full" :placeholder="project.portal" autocomplete="off" />
          </B24FormField>
          <B24Alert v-if="writeError" color="air-primary-alert" :description="writeError" />
        </div>
      </template>
      <template #footer>
        <div class="flex w-full justify-end gap-2">
          <B24Button label="Отмена" @click="writeOpen = false" />
          <B24Button
            color="air-primary"
            label="Разрешить"
            :disabled="writeTyped.trim() !== project.portal"
            @click="enableWrite"
          />
        </div>
      </template>
    </B24Modal>
  </div>
</template>
