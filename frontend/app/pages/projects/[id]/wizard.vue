<script setup lang="ts">
// The guided flow for people who are not CRM specialists: describe the business, answer
// a few questions, look at what will change, press one button. It drives the same API
// as the specialist tabs (design → save → plan → apply → report, rollback).
import type {
  AiConfig, DesignResult, JobDetail, PlanResult, Project, ReportResult, RunDetail
} from '~/types'
import { ACTIVE_JOB_STATUSES, FINAL_STATUSES } from '~/labels'

const route = useRoute()
const id = Number(route.params.id)

type Step = 'loading' | 'describe' | 'design' | 'review' | 'applying' | 'done'

const project = ref<Project | null>(null)
const ai = ref<AiConfig | null>(null)
const step = ref<Step>('loading')
const error = ref('')
const status = ref('')

const brief = useState(`wizard-brief-${id}`, () => '')
const answers = ref<string[]>([])
const answersFor = ref('')

const { job, track, open } = useJob<DesignResult>(id, 'design')
const run = ref<RunDetail | null>(null)
const undone = ref(false)

const confirmOpen = ref(false)
const confirmMode = ref<'apply' | 'rollback'>('apply')
const confirmBusy = ref(false)
const confirmError = ref('')
const rollbackPlan = ref<PlanResult | null>(null)

const reportBusy = ref(false)
const reportUrl = ref('')

let runTimer: ReturnType<typeof setTimeout> | null = null
onBeforeUnmount(() => { if (runTimer) clearTimeout(runTimer) })

const EXAMPLE = `Например: Мы монтируем и обслуживаем кондиционеры. Заявки приходят с сайта и по телефону. `
  + `Нужно вести сделки от заявки и замера до договора и монтажа, отмечать отказы. `
  + `В сделке важно знать тип объекта и дату замера. Инженеру при выезде на замер нужна задача.`

// --- loading ------------------------------------------------------------------------

async function load() {
  try {
    const [p, config] = await Promise.all([
      api<Project>(`/projects/${id}`),
      api<AiConfig>('/ai').catch(() => null)
    ])
    project.value = p
    ai.value = config
    if (!p.imported_at) {
      status.value = 'Читаю текущую структуру портала…'
      const imported = await api<{ project: Project }>(`/projects/${id}/import`, { method: 'POST' })
      project.value = imported.project
      status.value = ''
    }
    // Pick up a design that is still running (e.g. after a page reload).
    const jobs = await api<{ id: number, status: string }[]>(`/projects/${id}/jobs`, {
      query: { kind: 'design' }
    })
    const active = jobs.find(j => ACTIVE_JOB_STATUSES.has(j.status))
    if (active) {
      await open(active.id)
      step.value = 'design'
    } else {
      step.value = 'describe'
    }
  } catch (e) {
    error.value = errorText(e)
    status.value = ''
    step.value = 'describe'
  }
}

onMounted(load)

// --- designing ----------------------------------------------------------------------

const aiReady = computed(() => !!ai.value?.available)
const pendingQuestions = computed(() =>
  job.value?.status === 'waiting' ? job.value.questions || [] : []
)
watch(
  () => pendingQuestions.value.length ? `${job.value?.id}:${pendingQuestions.value.join('|')}` : '',
  (key) => {
    if (!key || key === answersFor.value) return
    answersFor.value = key
    answers.value = pendingQuestions.value.map(() => '')
  }
)

// The job finished: move on to the review, or back to the description on failure.
watch(() => job.value?.status, (value) => {
  if (step.value !== 'design' || !value || ACTIVE_JOB_STATUSES.has(value)) return
  if (value === 'done') step.value = 'review'
  else {
    error.value = job.value?.error || 'Проектировщик остановился.'
    step.value = 'describe'
  }
})

async function startDesign() {
  error.value = ''
  try {
    const started = await api<JobDetail<DesignResult>>(`/projects/${id}/design`, {
      method: 'POST',
      body: { brief: brief.value, model: null, questions: true }
    })
    track(started)
    step.value = 'design'
  } catch (e) {
    error.value = errorText(e)
  }
}

async function sendAnswers(empty: boolean) {
  const current = job.value
  if (!current) return
  try {
    track(await api<JobDetail<DesignResult>>(`/projects/${id}/jobs/${current.id}/answers`, {
      method: 'POST',
      body: { answers: pendingQuestions.value.map((_, i) => empty ? '' : (answers.value[i] || '').trim()) }
    }))
  } catch (e) {
    error.value = errorText(e)
  }
}

async function cancelDesign() {
  const current = job.value
  if (!current) return
  try {
    track(await api<JobDetail<DesignResult>>(`/projects/${id}/jobs/${current.id}/cancel`, {
      method: 'POST'
    }))
  } catch (e) {
    error.value = errorText(e)
  }
}

// Chat bubbles from the job log: the brief, questions, answers; edits as small lines.
const chat = computed(() => {
  const items: { who: 'me' | 'ai' | 'edit', text: string, list?: string[] }[] = []
  if (job.value?.params?.brief) items.push({ who: 'me', text: String(job.value.params.brief) })
  for (const entry of job.value?.log || []) {
    if (entry.type === 'questions') items.push({ who: 'ai', text: 'Уточню несколько моментов:', list: entry.items })
    else if (entry.type === 'answers') items.push({ who: 'me', text: '', list: entry.items })
    else if (entry.type === 'edit') items.push({ who: 'edit', text: entry.text })
  }
  return items
})

// --- review ---------------------------------------------------------------------------

const result = computed(() => job.value?.status === 'done' ? job.value.result : null)
const sections = computed(() => {
  const groups = new Map<string, string[]>()
  for (const line of result.value?.described || []) {
    groups.set(line.section, [...(groups.get(line.section) || []), line.text])
  }
  return [...groups.entries()].map(([title, lines]) => ({ title, lines }))
})
const robots = computed(() =>
  (result.value?.edits || []).filter(e => /^create automation\./.test(e)).length
)
const hasPortalChanges = computed(() => !!result.value?.plan.changes.length)
const destructive = computed(() => !!result.value?.plan.changes.some(c => c.destructive))

function startOver() {
  run.value = null
  undone.value = false
  reportUrl.value = ''
  error.value = ''
  step.value = 'describe'
}

// --- applying ------------------------------------------------------------------------

function askApply() {
  confirmMode.value = 'apply'
  confirmError.value = ''
  confirmOpen.value = true
}

async function confirmed(allowDestructive: boolean, typed: string) {
  if (confirmMode.value === 'rollback') return doRollback(allowDestructive, typed)
  const current = job.value
  if (!current || !project.value) return
  confirmBusy.value = true
  confirmError.value = ''
  try {
    if (!current.saved) {
      await api(`/projects/${id}/jobs/${current.id}/save`, { method: 'POST' })
      await open(current.id)
    }
    if (!hasPortalChanges.value) {
      // Only the robot checklist changed: nothing to write to the portal.
      confirmOpen.value = false
      step.value = 'done'
      return
    }
    if (!project.value.allow_write) {
      project.value = await api<Project>(`/projects/${id}`, {
        method: 'PATCH',
        body: { allow_write: true, confirm: typed }
      })
    }
    const plan = await api<PlanResult>(`/projects/${id}/plan`, { method: 'POST' })
    if (plan.blockers.length) {
      throw new Error('Битрикс24 не позволяет сделать часть изменений: '
        + plan.blockers.map(b => b.reason).join('; '))
    }
    const started = await api<RunDetail>(`/projects/${id}/apply`, {
      method: 'POST',
      body: { confirm: typed, fingerprint: plan.fingerprint, allow_destructive: allowDestructive }
    })
    confirmOpen.value = false
    run.value = started
    step.value = 'applying'
    watchRun(started.id)
  } catch (e) {
    confirmError.value = errorText(e)
  } finally {
    confirmBusy.value = false
  }
}

function watchRun(runId: number) {
  if (runTimer) clearTimeout(runTimer)
  runTimer = setTimeout(async () => {
    try {
      run.value = await api<RunDetail>(`/projects/${id}/runs/${runId}`)
    } catch {
      // retried on the next tick
    }
    if (run.value && FINAL_STATUSES.has(run.value.status)) {
      if (step.value === 'applying') step.value = 'done'
      return
    }
    watchRun(runId)
  }, 1000)
}

const progress = computed(() =>
  run.value?.total ? Math.round((run.value.done / run.value.total) * 100) : null
)
const runOk = computed(() => !run.value || ['verified', 'unchanged'].includes(run.value.status))

// --- done: report and undo ------------------------------------------------------------

async function makeReport() {
  reportBusy.value = true
  error.value = ''
  try {
    let report = await api<JobDetail<ReportResult>>(`/projects/${id}/report`, {
      method: 'POST',
      body: { title: '', since_run_id: null, ai: aiReady.value, brief: brief.value, model: null }
    })
    while (ACTIVE_JOB_STATUSES.has(report.status)) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      report = await api<JobDetail<ReportResult>>(`/projects/${id}/jobs/${report.id}`)
    }
    if (report.status !== 'done') throw new Error(report.error || 'Отчёт не сформирован')
    reportUrl.value = `/api/projects/${id}/jobs/${report.id}/download`
    window.location.href = reportUrl.value
  } catch (e) {
    error.value = errorText(e)
  } finally {
    reportBusy.value = false
  }
}

async function askRollback() {
  if (!run.value) return
  error.value = ''
  try {
    rollbackPlan.value = await api<PlanResult>(`/projects/${id}/runs/${run.value.id}/rollback-plan`, {
      method: 'POST'
    })
    confirmMode.value = 'rollback'
    confirmError.value = ''
    confirmOpen.value = true
  } catch (e) {
    error.value = errorText(e)
  }
}

async function doRollback(allowDestructive: boolean, typed: string) {
  if (!run.value || !rollbackPlan.value) return
  confirmBusy.value = true
  confirmError.value = ''
  try {
    let undo = await api<RunDetail>(`/projects/${id}/runs/${run.value.id}/rollback`, {
      method: 'POST',
      body: { confirm: typed, fingerprint: rollbackPlan.value.fingerprint, allow_destructive: allowDestructive }
    })
    while (!FINAL_STATUSES.has(undo.status)) {
      await new Promise(resolve => setTimeout(resolve, 1000))
      undo = await api<RunDetail>(`/projects/${id}/runs/${undo.id}`)
    }
    if (!['verified', 'unchanged'].includes(undo.status)) {
      throw new Error(undo.error || 'Откат прошёл не полностью: подробности во вкладке «История».')
    }
    confirmOpen.value = false
    undone.value = true
  } catch (e) {
    confirmError.value = errorText(e)
  } finally {
    confirmBusy.value = false
  }
}

const STEPS = [
  { key: 'describe', label: 'Описание' },
  { key: 'design', label: 'Вопросы' },
  { key: 'review', label: 'Проверка' },
  { key: 'applying', label: 'Настройка' },
  { key: 'done', label: 'Готово' }
]
const stepIndex = computed(() => STEPS.findIndex(s => s.key === step.value))
</script>

<template>
  <div class="mx-auto flex max-w-3xl flex-col gap-6">
    <div class="flex items-center justify-between gap-4">
      <NuxtLink :to="`/projects/${id}`" class="text-sm opacity-70 hover:opacity-100">
        ← Режим специалиста
      </NuxtLink>
      <span v-if="project" class="text-sm opacity-60">{{ project.portal }}</span>
    </div>

    <div>
      <h1 class="text-2xl font-semibold">Настройка портала по описанию</h1>
      <p class="mt-1 opacity-70">
        Расскажите о бизнесе своими словами — ИИ спроектирует CRM, покажет, что изменится, и
        настроит портал после вашего подтверждения.
      </p>
    </div>

    <ol class="flex flex-wrap gap-2 text-sm">
      <li
        v-for="(s, i) in STEPS"
        :key="s.key"
        class="rounded-full px-3 py-1"
        :class="i === stepIndex
          ? 'bg-(--ui-color-accent-main-primary) text-white'
          : i < stepIndex ? 'bg-(--ui-color-bg-content-secondary)' : 'opacity-50'"
      >
        {{ i + 1 }}. {{ s.label }}
      </li>
    </ol>

    <B24Alert v-if="error" color="air-primary-alert" :description="error" close @update:open="error = ''" />

    <!-- loading -->
    <B24Card v-if="step === 'loading'">
      <div class="flex flex-col gap-3">
        <p>{{ status || 'Загружаю проект…' }}</p>
        <B24Progress />
      </div>
    </B24Card>

    <!-- 1. describe -->
    <B24Card v-else-if="step === 'describe'">
      <div class="flex flex-col gap-4">
        <B24Alert
          v-if="ai && !ai.available"
          color="air-primary-warning"
          title="ИИ недоступен"
          description="На сервере не задан ANTHROPIC_API_KEY: добавьте его в .env и перезапустите сервис."
        />
        <B24FormField label="Опишите бизнес и что нужно в CRM" required>
          <B24Textarea
            v-model="brief"
            :rows="9"
            autoresize
            :placeholder="EXAMPLE"
            class="w-full"
            :maxlength="20000"
          />
        </B24FormField>
        <p class="text-sm opacity-70">
          Можно вставить готовое ТЗ. Если чего-то не хватит, ИИ задаст вопросы. Портал не
          изменится, пока вы не нажмёте «Настроить портал».
        </p>
        <div>
          <B24Button
            color="air-primary"
            size="lg"
            label="Спроектировать"
            :disabled="!aiReady || !brief.trim()"
            @click="startDesign"
          />
        </div>
      </div>
    </B24Card>

    <!-- 2. design: chat with the model -->
    <B24Card v-else-if="step === 'design'">
      <div class="flex flex-col gap-3">
        <template v-for="(item, i) in chat" :key="i">
          <div v-if="item.who === 'me'" class="ml-auto max-w-[85%] rounded-2xl rounded-br-sm bg-(--ui-color-accent-soft-blue-2) px-4 py-2 whitespace-pre-wrap">
            <template v-if="item.text">{{ item.text }}</template>
            <ul v-else class="list-disc pl-5">
              <li v-for="(a, j) in item.list" :key="j">{{ a || 'Решите сами' }}</li>
            </ul>
          </div>
          <div v-else-if="item.who === 'ai'" class="max-w-[85%] rounded-2xl rounded-bl-sm bg-(--ui-color-bg-content-secondary) px-4 py-2">
            <p>{{ item.text }}</p>
            <ol class="mt-1 list-decimal pl-5">
              <li v-for="(q, j) in item.list" :key="j">{{ q }}</li>
            </ol>
          </div>
          <div v-else class="text-sm opacity-60">✏️ {{ item.text }}</div>
        </template>

        <div v-if="pendingQuestions.length" class="flex flex-col gap-3 rounded-xl border border-(--ui-color-divider-default) p-4">
          <B24FormField v-for="(q, i) in pendingQuestions" :key="i" :label="`${i + 1}. ${q}`">
            <B24Textarea v-model="answers[i]" :rows="2" autoresize class="w-full" placeholder="Можно оставить пустым — ИИ решит сам" />
          </B24FormField>
          <div class="flex flex-wrap gap-2">
            <B24Button color="air-primary" label="Ответить" @click="sendAnswers(false)" />
            <B24Button label="Пусть решает сам" @click="sendAnswers(true)" />
          </div>
        </div>
        <div v-else class="flex items-center gap-3 text-sm opacity-70">
          <B24Progress class="max-w-40" />
          <span>ИИ проектирует CRM…</span>
        </div>
        <div>
          <B24Button size="sm" color="air-secondary-alert" label="Остановить" @click="cancelDesign" />
        </div>
      </div>
    </B24Card>

    <!-- 3. review -->
    <template v-else-if="step === 'review' && result">
      <B24Card v-if="result.summary">
        <Markdown :source="result.summary" />
      </B24Card>
      <B24Card title="Что будет настроено">
        <div v-if="!result.edits.length" class="opacity-70">ИИ не предложил изменений.</div>
        <div v-for="s in sections" :key="s.title" class="mb-4 last:mb-0">
          <p class="mb-1 font-medium">{{ s.title }}</p>
          <ul class="flex flex-col gap-1 pl-1">
            <li v-for="(line, i) in s.lines" :key="i">✅ {{ line }}</li>
          </ul>
        </div>
        <p v-if="robots" class="mt-3 text-sm">
          🤖 Роботов и триггеров: {{ robots }}. Битрикс24 не даёт ставить их автоматически — после
          настройки вы получите пошаговую инструкцию в отчёте.
        </p>
        <p v-if="job?.cost_usd" class="mt-3 text-sm opacity-60">Стоимость работы ИИ: ≈ ${{ job.cost_usd.toFixed(4) }}</p>
      </B24Card>
      <div class="flex flex-wrap gap-2">
        <B24Button
          v-if="result.edits.length"
          color="air-primary"
          size="lg"
          :label="hasPortalChanges ? 'Настроить портал' : 'Сохранить'"
          @click="askApply"
        />
        <B24Button size="lg" label="Изменить описание" @click="startOver" />
      </div>
    </template>

    <!-- 4. applying -->
    <B24Card v-else-if="step === 'applying'">
      <div class="flex flex-col gap-3">
        <p class="font-medium">Настраиваю портал…</p>
        <B24Progress :model-value="progress" />
        <p class="text-sm opacity-70">
          Выполнено {{ run?.done || 0 }} из {{ run?.total || '…' }} изменений. Не закрывайте
          страницу — после настройки портал будет проверен ещё раз.
        </p>
      </div>
    </B24Card>

    <!-- 5. done -->
    <template v-else-if="step === 'done'">
      <B24Alert
        v-if="undone"
        color="air-secondary"
        title="Изменения отменены"
        description="Портал возвращён к состоянию до настройки."
      />
      <B24Alert
        v-else-if="runOk"
        color="air-primary-success"
        title="Портал настроен ✅"
        :description="hasPortalChanges ? 'Все изменения внесены и проверены.' : 'Изменения сохранены в проекте.'"
      />
      <B24Alert
        v-else
        color="air-primary-alert"
        title="Настройка прошла не полностью"
        :description="(run?.error || 'Часть изменений не применилась.') + ' Подробности — во вкладке «История» режима специалиста.'"
      />
      <B24Card v-if="!undone && sections.length" title="Что сделано">
        <div v-for="s in sections" :key="s.title" class="mb-3 last:mb-0">
          <p class="mb-1 font-medium">{{ s.title }}</p>
          <ul class="flex flex-col gap-1 pl-1">
            <li v-for="(line, i) in s.lines" :key="i">✅ {{ line }}</li>
          </ul>
        </div>
        <p v-if="robots" class="mt-3 text-sm">
          🤖 Осталось вручную: {{ robots }} {{ pluralRu(robots, ['робот', 'робота', 'роботов']) }} — инструкция в отчёте.
        </p>
      </B24Card>
      <div class="flex flex-wrap gap-2">
        <B24Button
          v-if="!undone"
          color="air-primary"
          :loading="reportBusy"
          :label="reportBusy ? 'Готовлю отчёт…' : 'Скачать отчёт для клиента'"
          @click="makeReport"
        />
        <B24Button
          v-if="project"
          :href="`https://${project?.portal}/crm/deal/kanban/`"
          target="_blank"
          label="Открыть портал"
        />
        <B24Button
          v-if="run && runOk && !undone && run.can_rollback !== false"
          color="air-secondary-alert"
          label="Отменить изменения"
          @click="askRollback"
        />
        <B24Button label="Новый запрос" @click="startOver" />
      </div>
    </template>

    <ConfirmRun
      v-if="project"
      v-model:open="confirmOpen"
      :title="confirmMode === 'apply' ? 'Настроить портал' : 'Отменить изменения'"
      :portal="project.portal"
      :destructive="confirmMode === 'apply' ? destructive : !!rollbackPlan?.plan.changes.some(c => c.destructive)"
      :busy="confirmBusy"
      :error="confirmError"
      @confirm="confirmed"
    >
      <p v-if="confirmMode === 'apply'" class="text-sm">
        Изменения будут внесены на портал {{ project?.portal }}. Их можно будет отменить одной кнопкой.
      </p>
      <p v-else class="text-sm">
        Портал вернётся к состоянию до настройки: созданные воронки, поля и смарт-процессы будут удалены.
      </p>
    </ConfirmRun>
  </div>
</template>
