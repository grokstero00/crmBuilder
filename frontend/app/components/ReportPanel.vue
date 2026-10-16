<script setup lang="ts">
import type { AiConfig, JobDetail, Project, ReportResult, RunSummary } from '~/types'
import { formatDateTime } from '~/utils/format'

const props = defineProps<{
  projectId: number
  project: Project
  ai: AiConfig | null
  runs: RunSummary[]
}>()

const FROM_START = 'start'

const title = useState(`report-title-${props.projectId}`, () => '')
const since = useState(`report-since-${props.projectId}`, () => FROM_START)
const withAi = useState(`report-ai-${props.projectId}`, () => false)
const brief = useState(`report-brief-${props.projectId}`, () => '')

const { job, jobs, active, anyActive, track, open, loadJobs, restore } = useJob<ReportResult>(
  props.projectId,
  'report'
)

const busy = ref(false)
const error = ref('')

const aiReady = computed(() => !!props.ai?.available)
watch(aiReady, (ready) => {
  if (!ready) withAi.value = false
}, { immediate: true })

// Only an apply has a "before" snapshot to count changes from.
// Only runs that read the portal before writing can be a base for «что сделано».
const applyRuns = computed(() => props.runs.filter(r => r.kind === 'apply' && r.has_before))
const sinceItems = computed(() => [
  { label: 'Начала проекта (первое применение)', value: FROM_START },
  ...applyRuns.value.map(r => ({
    label: `Применение №${r.id} от ${formatDateTime(r.created_at)}`,
    value: String(r.id)
  }))
])
watch(sinceItems, (items) => {
  if (!items.some(i => i.value === since.value)) since.value = FROM_START
}, { immediate: true })

const result = computed(() => job.value?.status === 'done' ? job.value.result : null)
const ready = computed(() => !withAi.value || !!brief.value.trim())

async function build() {
  error.value = ''
  busy.value = true
  try {
    const started = await api<JobDetail<ReportResult>>(`/projects/${props.projectId}/report`, {
      method: 'POST',
      body: {
        title: title.value.trim(),
        since_run_id: since.value === FROM_START ? null : Number(since.value),
        ai: withAi.value,
        brief: withAi.value ? brief.value : '',
        model: null
      }
    })
    track(started)
    await loadJobs()
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}

async function openJob(jobId: number) {
  error.value = ''
  try {
    await open(jobId)
  } catch (e) {
    error.value = errorText(e)
  }
}

onMounted(() => {
  restore(true).catch((e) => { error.value = errorText(e) })
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <B24Alert v-if="error" color="air-primary-alert" :description="error" close @update:open="error = ''" />

    <B24Card title="Отчёт для клиента">
      <div class="flex flex-col gap-4">
        <p class="text-sm opacity-70">
          Отчёт в Markdown для клиента: что сделано на портале, структура CRM по текущей
          спецификации (воронки, стадии, поля, смарт-процессы, отделы) и что нужно настроить
          вручную.
        </p>
        <B24FormField label="Заголовок">
          <B24Input
            v-model="title"
            class="w-full"
            :maxlength="200"
            :placeholder="`Настройка портала ${project.portal}`"
          />
        </B24FormField>
        <B24FormField
          label="Что сделано — считать от"
          :description="applyRuns.length
            ? undefined
            : 'Применений ещё не было: отчёт опишет только текущую структуру.'"
        >
          <B24Select v-model="since" :items="sinceItems" class="w-full max-w-xl" />
        </B24FormField>
        <B24Checkbox
          variant="list"
          v-model="withAi"
          label="Добавить инструкции по ручным шагам (ИИ)"
          description="Модель распишет, что настроить руками: роботов, триггеры и то, что не делается через API."
          :disabled="!aiReady"
        />
        <B24Alert
          v-if="ai && !ai.available"
          color="air-primary-warning"
          description="Инструкции ИИ недоступны: на сервере не задан ключ ANTHROPIC_API_KEY. Добавьте его в файл .env и перезапустите сервис. Отчёт без ИИ формируется и так."
        />
        <B24FormField
          v-if="withAi"
          label="ТЗ клиента"
          description="По нему модель поймёт, что клиент хотел получить."
          required
        >
          <B24Textarea v-model="brief" :rows="5" autoresize :maxrows="16" class="w-full" :maxlength="20000" />
        </B24FormField>
        <div>
          <B24Button
            color="air-primary"
            label="Сформировать"
            :loading="busy"
            :disabled="busy || anyActive || !ready"
            @click="build"
          />
        </div>
      </div>
    </B24Card>

    <B24Card v-if="job">
      <div class="flex flex-col gap-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <JobHeader :job="job" label="Отчёт" />
          <B24Button
            v-if="result"
            size="sm"
            color="air-primary"
            label="Скачать .md"
            :href="`/api/projects/${projectId}/jobs/${job.id}/download`"
            external
            download
          />
        </div>

        <B24Progress v-if="active" :model-value="null" />
        <B24Alert
          v-if="job.status === 'failed' || job.status === 'interrupted' || job.status === 'cancelled'"
          color="air-primary-alert"
          :description="job.error || 'Отчёт не сформировался. Попробуйте ещё раз.'"
        />

        <template v-if="result">
          <div class="flex flex-wrap gap-2 text-sm">
            <B24Badge :label="`изменений: ${result.changes}`" />
            <B24Badge
              v-if="result.changes"
              :label="result.since_run_id ? `с применения №${result.since_run_id}` : 'с начала проекта'"
            />
            <B24Badge v-if="result.manual_steps" color="air-primary-warning" :label="`ручных шагов: ${result.manual_steps}`" />
          </div>
          <div class="rounded-lg border border-(--ui-color-divider-default) p-4">
            <Markdown :source="result.markdown" />
          </div>
        </template>
      </div>
    </B24Card>

    <JobList
      title="Предыдущие отчёты"
      :jobs="jobs"
      :active-id="job?.id ?? null"
      @open="openJob"
    />
  </div>
</template>
