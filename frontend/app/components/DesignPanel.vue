<script setup lang="ts">
import type { AiConfig, DesignResult, JobDetail, JobLogEntry, PlanResult, Project } from '~/types'
import { ACTION_TITLES } from '~/labels'

const props = defineProps<{
  projectId: number
  project: Project
  ai: AiConfig | null
  /** The YAML editor has unsaved edits: saving a design result would replace them. */
  specDirty: boolean
}>()
const emit = defineEmits<{ saved: [project: Project] }>()

const BRIEF_EXAMPLE = `Например: Мы продаём и устанавливаем кондиционеры.
Нужна воронка «Продажи»: новая заявка → замер → коммерческое предложение → договор → монтаж → успешно; отказ с причиной.
Отдельная воронка «Сервис» для гарантийных обращений.
В сделке поля: тип объекта (квартира, офис, склад), площадь в м², дата монтажа, монтажник (сотрудник).
Отделы: продажи, монтаж, сервис.`

// Form state survives tab switches (tabs unmount their content).
const brief = useState(`design-brief-${props.projectId}`, () => '')
const model = useState(`design-model-${props.projectId}`, () => '')
const allowQuestions = useState(`design-questions-${props.projectId}`, () => true)
const answers = useState<string[]>(`design-answers-${props.projectId}`, () => [])
const answersFor = useState(`design-answers-for-${props.projectId}`, () => '')

const { job, jobs, active, anyActive, track, open, close, loadJobs, restore } = useJob<DesignResult>(
  props.projectId,
  'design'
)

const busy = ref<'' | 'start' | 'answer' | 'cancel' | 'save'>('')
const error = ref('')
const showLog = ref(false)

const aiReady = computed(() => !!props.ai?.available)
const modelItems = computed(() => (props.ai?.models || []).map(m => ({ label: m.label, value: m.id })))

watch(() => props.ai, (ai) => {
  if (ai && !ai.models.some(m => m.id === model.value)) model.value = ai.default_model
}, { immediate: true })

// Questions still open. Right after answering the job may stay `waiting` for a moment
// without questions, until the model picks the answers up.
const pendingQuestions = computed(() =>
  job.value?.status === 'waiting' ? job.value.questions || [] : []
)

// Polling replaces the job object every second; reset the answer boxes only when the
// questions themselves change, not on every poll.
watch(
  () => pendingQuestions.value.length
    ? `${job.value?.id}:${JSON.stringify(pendingQuestions.value)}`
    : '',
  (key) => {
    if (!key || key === answersFor.value) return
    answersFor.value = key
    answers.value = pendingQuestions.value.map(() => '')
  },
  { immediate: true }
)

const result = computed(() => job.value?.status === 'done' ? job.value.result : null)
const planResult = computed<PlanResult | null>(() =>
  result.value
    ? { plan: result.value.plan, fingerprint: '', blockers: [], manual: [], warnings: [] }
    : null
)

// Edits of the robot checklist; the portal plan does not compare `automation`.
const automationEdits = computed(() =>
  (result.value?.edits || []).filter(e => /\sautomation\./.test(` ${e}`))
)

async function act(kind: typeof busy.value, action: () => Promise<void>) {
  error.value = ''
  busy.value = kind
  try {
    await action()
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = ''
  }
}

function start() {
  return act('start', async () => {
    const started = await api<JobDetail<DesignResult>>(`/projects/${props.projectId}/design`, {
      method: 'POST',
      body: {
        brief: brief.value,
        model: model.value || null,
        questions: allowQuestions.value
      }
    })
    showLog.value = false
    track(started)
    await loadJobs()
  })
}

function sendAnswers(empty: boolean) {
  const current = job.value
  if (!current) return
  return act('answer', async () => {
    const body = {
      answers: pendingQuestions.value.map((_, i) => empty ? '' : (answers.value[i] || '').trim())
    }
    track(await api<JobDetail<DesignResult>>(
      `/projects/${props.projectId}/jobs/${current.id}/answers`,
      { method: 'POST', body }
    ))
  })
}

function cancel() {
  const current = job.value
  if (!current) return
  return act('cancel', async () => {
    track(await api<JobDetail<DesignResult>>(
      `/projects/${props.projectId}/jobs/${current.id}/cancel`,
      { method: 'POST' }
    ))
  })
}

function save() {
  const current = job.value
  if (!current) return
  if (props.specDirty && !confirm(
    'В редакторе спецификации есть несохранённые правки. Они пропадут: спецификация будет '
    + 'заменена результатом проектировщика. Продолжить?'
  )) return
  return act('save', async () => {
    const saved = await api<{ project: Project }>(
      `/projects/${props.projectId}/jobs/${current.id}/save`,
      { method: 'POST' }
    )
    await open(current.id)
    await loadJobs()
    emit('saved', saved.project)
  })
}

function openJob(jobId: number) {
  return act('', async () => {
    showLog.value = false
    await open(jobId)
  })
}

function logAction(entry: JobLogEntry): { action: string, address: string } | null {
  const [verb, address] = entry.text.split(/\s+/, 2)
  const action = ACTION_TITLES[verb || '']
  return action && address ? { action, address } : null
}

const logVisible = computed(() =>
  !!job.value?.log.length && (active.value || showLog.value)
)

onMounted(() => {
  restore(false).catch((e) => { error.value = errorText(e) })
})
</script>

<template>
  <div class="flex flex-col gap-4">
    <B24Alert
      v-if="ai && !ai.available"
      color="air-primary-warning"
      title="ИИ недоступен"
      description="На сервере не задан ключ Anthropic API. Добавьте строку ANTHROPIC_API_KEY=... в файл .env рядом с docker-compose.yml и перезапустите сервис (docker compose up -d). Аудит и отчёт без ИИ работают и так."
    />
    <B24Alert v-if="error" color="air-primary-alert" :description="error" close @update:open="error = ''" />

    <B24Card title="Задание для проектировщика">
      <div class="flex flex-col gap-4">
        <p class="text-sm opacity-70">
          Опишите бизнес клиента и что нужно настроить. Модель изменит спецификацию проекта:
          воронки, стадии, поля, смарт-процессы, отделы и чек-лист роботов. Портал не меняется,
          пока вы не сохраните результат, не построите план и не примените его.
        </p>
        <B24FormField label="ТЗ" required>
          <B24Textarea
            v-model="brief"
            :rows="7"
            autoresize
            :maxrows="20"
            class="w-full"
            :placeholder="BRIEF_EXAMPLE"
            :maxlength="20000"
            :disabled="!aiReady"
          />
        </B24FormField>
        <div class="flex flex-wrap items-end gap-4">
          <B24FormField label="Модель" class="min-w-64">
            <B24Select
              v-model="model"
              :items="modelItems"
              class="w-full"
              :disabled="!aiReady"
            />
          </B24FormField>
          <B24Checkbox
            variant="list"
            v-model="allowQuestions"
            label="Модель может задавать вопросы"
            :disabled="!aiReady"
          />
        </div>
        <div class="flex flex-wrap items-center gap-3">
          <B24Button
            color="air-primary"
            label="Запустить"
            :loading="busy === 'start'"
            :disabled="!aiReady || !brief.trim() || anyActive || !!busy"
            @click="start"
          />
          <span v-if="anyActive" class="text-sm opacity-70">
            Проектировщик уже работает над этим проектом.
          </span>
        </div>
      </div>
    </B24Card>

    <B24Card v-if="job">
      <div class="flex flex-col gap-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <JobHeader :job="job" label="Проектирование" />
          <div class="flex flex-wrap gap-2">
            <B24Button
              v-if="active"
              size="sm"
              color="air-secondary-alert"
              label="Отменить"
              :loading="busy === 'cancel'"
              :disabled="!!busy"
              @click="cancel"
            />
            <B24Button
              v-else-if="job.log.length"
              size="sm"
              :label="showLog ? 'Скрыть журнал' : `Журнал (${job.log.length})`"
              @click="showLog = !showLog"
            />
          </div>
        </div>

        <div v-if="active && !pendingQuestions.length" class="flex flex-col gap-2">
          <B24Progress :model-value="null" />
          <p class="text-sm opacity-70">Модель работает над спецификацией. Это может занять несколько минут.</p>
        </div>

        <ul v-if="logVisible" class="flex max-h-[28rem] flex-col gap-2 overflow-auto text-sm">
          <li v-for="(entry, i) in job.log" :key="i">
            <div v-if="entry.type === 'questions'" class="flex justify-start">
              <div class="max-w-[85%] rounded-lg bg-(--ui-color-bg-content-secondary) px-3 py-2">
                <div class="mb-1 text-xs opacity-60">Модель спрашивает</div>
                <ol v-if="entry.items?.length" class="list-decimal pl-5">
                  <li v-for="(q, k) in entry.items" :key="k">{{ q }}</li>
                </ol>
                <div v-else class="whitespace-pre-line">{{ entry.text }}</div>
              </div>
            </div>
            <div v-else-if="entry.type === 'answers'" class="flex justify-end">
              <div class="max-w-[85%] rounded-lg bg-(--ui-color-accent-soft-blue-2) px-3 py-2">
                <div class="mb-1 text-xs opacity-60">Ваши ответы</div>
                <ol v-if="entry.items?.length" class="list-decimal pl-5">
                  <li v-for="(a, k) in entry.items" :key="k">
                    <span v-if="a.trim()" class="whitespace-pre-line">{{ a }}</span>
                    <span v-else class="opacity-60">модель решит сама</span>
                  </li>
                </ol>
                <div v-else class="whitespace-pre-line">{{ entry.text }}</div>
              </div>
            </div>
            <div v-else-if="entry.type === 'error'" class="flex items-start gap-2 text-red-600">
              <span>⚠️</span><span class="break-all">{{ entry.text }}</span>
            </div>
            <div v-else-if="entry.type === 'edit'" class="flex items-start gap-2">
              <span>✏️</span>
              <template v-if="logAction(entry)">
                <span class="opacity-60">{{ logAction(entry)!.action }}</span>
                <code class="break-all">{{ logAction(entry)!.address }}</code>
              </template>
              <span v-else class="break-all">{{ entry.text }}</span>
            </div>
            <div v-else class="flex items-start gap-2 opacity-70">
              <span>ℹ️</span><span>{{ entry.text }}</span>
            </div>
          </li>
        </ul>

        <div v-if="pendingQuestions.length" class="flex flex-col gap-3 rounded-lg border border-(--ui-color-divider-default) p-3">
          <p class="font-medium">Модель ждёт ответов</p>
          <B24FormField
            v-for="(q, i) in pendingQuestions"
            :key="`${job.id}-${i}`"
            :label="`${i + 1}. ${q}`"
          >
            <B24Textarea
              v-model="answers[i]"
              :rows="2"
              autoresize
              class="w-full"
              placeholder="Оставьте пустым — модель решит сама"
              :maxlength="5000"
            />
          </B24FormField>
          <div class="flex flex-wrap gap-2">
            <B24Button
              color="air-primary"
              label="Ответить"
              :loading="busy === 'answer'"
              :disabled="!!busy"
              @click="sendAnswers(false)"
            />
            <B24Button
              label="Пусть решает сама"
              :disabled="!!busy"
              @click="sendAnswers(true)"
            />
          </div>
        </div>

        <B24Alert
          v-if="job.status === 'failed' || job.status === 'interrupted'"
          color="air-primary-alert"
          :description="job.error || (job.status === 'interrupted'
            ? 'Работа прервалась: сервис перезапускался. Запустите проектировщик ещё раз.'
            : 'Проектировщик завершился с ошибкой.')"
        />
        <B24Alert
          v-if="job.status === 'cancelled'"
          color="air-secondary"
          :description="job.error || 'Проектирование отменено. Спецификация не изменилась.'"
        />

        <template v-if="result && planResult">
          <div v-if="result.summary" class="rounded-lg bg-(--ui-color-bg-content-secondary) p-3">
            <Markdown :source="result.summary" />
          </div>

          <div class="flex flex-col gap-2">
            <p class="font-medium">Предложенные изменения спецификации</p>
            <B24Alert
              v-if="!result.edits.length"
              color="air-secondary"
              description="Модель не предложила изменений спецификации."
            />
            <PlanView v-else-if="result.plan.changes.length" :result="planResult" />
            <!-- Robot rules are not part of the portal plan: list them separately. -->
            <div v-if="automationEdits.length" class="text-sm">
              <p class="font-medium">Чек-лист роботов и триггеров</p>
              <ul class="list-disc pl-5">
                <li v-for="e in automationEdits" :key="e"><code>{{ e }}</code></li>
              </ul>
            </div>
          </div>

          <B24Alert
            v-if="job.saved"
            color="air-primary-success"
            description="Сохранено. Постройте план, чтобы применить."
          />
          <div v-else-if="result.edits.length" class="flex flex-wrap items-center gap-2">
            <B24Button
              color="air-primary"
              label="Сохранить в спецификацию"
              :loading="busy === 'save'"
              :disabled="!!busy"
              @click="save"
            />
            <B24Button label="Отбросить" :disabled="!!busy" @click="close" />
            <span class="text-sm opacity-70">Портал не изменится: после сохранения постройте план.</span>
          </div>
        </template>
      </div>
    </B24Card>

    <JobList
      title="Предыдущие задания"
      :jobs="jobs"
      :active-id="job?.id ?? null"
      @open="openJob"
    />
  </div>
</template>
