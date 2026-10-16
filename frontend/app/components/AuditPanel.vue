<script setup lang="ts">
import type { AiConfig, AuditResult, Finding, JobDetail, Project, Severity } from '~/types'
import { SEVERITY } from '~/labels'

const props = defineProps<{ projectId: number, project: Project, ai: AiConfig | null }>()

const withAi = useState(`audit-ai-${props.projectId}`, () => false)
const context = useState(`audit-context-${props.projectId}`, () => '')

const { job, jobs, active, anyActive, track, open, loadJobs, restore } = useJob<AuditResult>(
  props.projectId,
  'audit'
)

const busy = ref(false)
const error = ref('')

const aiReady = computed(() => !!props.ai?.available)
watch(aiReady, (ready) => {
  if (!ready) withAi.value = false
}, { immediate: true })

const result = computed(() => job.value?.status === 'done' ? job.value.result : null)

const SEVERITIES: Severity[] = ['error', 'warning', 'info']
const groups = computed(() => {
  const findings = result.value?.findings || []
  return SEVERITIES
    .map(severity => ({
      severity,
      ...SEVERITY[severity],
      findings: findings.filter((f: Finding) => f.severity === severity)
    }))
    .filter(g => g.findings.length)
})

async function check() {
  error.value = ''
  busy.value = true
  try {
    const started = await api<JobDetail<AuditResult>>(`/projects/${props.projectId}/audit`, {
      method: 'POST',
      body: { ai: withAi.value, context: withAi.value ? context.value : '', model: null }
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

    <B24Card title="Проверка конфигурации">
      <div class="flex flex-col gap-4">
        <p class="text-sm opacity-70">
          Аудит проверяет текущую спецификацию проекта: названия (дубли, смесь алфавитов, мусор),
          стадии воронок (финальные стадии, порядок), похожие воронки, поля (пустые списки, дубли
          стандартных полей) и другое. Чтобы проверить портал в его нынешнем виде, сначала нажмите
          «Обновить с портала».
        </p>
        <B24Checkbox
          variant="list"
          v-model="withAi"
          label="Добавить разбор ИИ"
          description="Модель оценит конфигурацию с точки зрения бизнеса и добавит свои замечания."
          :disabled="!aiReady"
        />
        <B24Alert
          v-if="ai && !ai.available"
          color="air-primary-warning"
          description="Разбор ИИ недоступен: на сервере не задан ключ ANTHROPIC_API_KEY. Добавьте его в файл .env и перезапустите сервис. Проверка по правилам работает и без него."
        />
        <B24FormField
          v-if="withAi"
          label="О бизнесе клиента"
          description="Необязательно: чем занимается клиент, как продаёт, что важно. Поможет модели."
        >
          <B24Textarea v-model="context" :rows="3" autoresize class="w-full" :maxlength="5000" />
        </B24FormField>
        <div>
          <B24Button
            color="air-primary"
            label="Проверить"
            :loading="busy"
            :disabled="busy || anyActive"
            @click="check"
          />
        </div>
      </div>
    </B24Card>

    <B24Card v-if="job">
      <div class="flex flex-col gap-4">
        <div class="flex flex-wrap items-center justify-between gap-2">
          <JobHeader :job="job" :label="job.title || 'Аудит'" />
          <B24Button
            v-if="result"
            size="sm"
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
          :description="job.error || 'Проверка не завершилась. Запустите её ещё раз.'"
        />

        <template v-if="result">
          <div class="flex flex-wrap gap-2">
            <B24Badge
              v-for="s in SEVERITIES"
              :key="s"
              :color="SEVERITY[s].color"
              :label="`${SEVERITY[s].count}: ${result.counts[s] ?? 0}`"
            />
          </div>

          <B24Alert
            v-if="result.business_guess"
            color="air-secondary"
            title="Профиль бизнеса (оценка ИИ)"
            :description="result.business_guess"
          />

          <B24Alert
            v-if="!result.findings.length"
            color="air-primary-success"
            description="Проблем не найдено."
          />

          <div v-for="g in groups" :key="g.severity" class="flex flex-col gap-2">
            <p class="font-medium">{{ g.title }} ({{ g.findings.length }})</p>
            <ul class="flex flex-col gap-2">
              <li
                v-for="(f, i) in g.findings"
                :key="`${f.code}-${f.address}-${i}`"
                class="rounded-lg border border-(--ui-color-divider-default) p-3"
              >
                <div class="flex flex-wrap items-start gap-2">
                  <span class="font-medium">{{ f.message }}</span>
                  <B24Badge v-if="f.source === 'ai'" color="air-primary-copilot" label="ИИ" size="sm" />
                </div>
                <code v-if="f.address" class="text-xs break-all opacity-70">{{ f.address }}</code>
                <p v-if="f.hint" class="mt-1 text-sm opacity-80">{{ f.hint }}</p>
              </li>
            </ul>
          </div>
        </template>
      </div>
    </B24Card>

    <JobList
      title="Предыдущие проверки"
      :jobs="jobs"
      :active-id="job?.id ?? null"
      @open="openJob"
    />
  </div>
</template>
