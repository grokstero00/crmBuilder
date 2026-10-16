import type { JobDetail, JobKind, JobSummary } from '~/types'
import { ACTIVE_JOB_STATUSES } from '~/labels'

/**
 * The job a panel shows. While it is running or waiting for answers it is polled every
 * second; polling stops when the job ends, another job is opened or the panel unmounts.
 * The opened job id survives tab switches (tabs unmount their content), so `restore()`
 * brings the same job back.
 */
export function useJob<R>(projectId: number, kind: JobKind) {
  const job = shallowRef<JobDetail<R> | null>(null)
  const jobs = ref<JobSummary[]>([])
  const openedId = useState<number | null>(`job-${kind}-${projectId}`, () => null)
  let timer: ReturnType<typeof setTimeout> | null = null
  // Bumped on every stop: a poll that comes back after it belongs to an old job.
  let generation = 0
  // Set when the panel unmounts: requests still in flight must not start polling again.
  let disposed = false

  const active = computed(() => !!job.value && ACTIVE_JOB_STATUSES.has(job.value.status))
  const anyActive = computed(() =>
    active.value || jobs.value.some(j => ACTIVE_JOB_STATUSES.has(j.status))
  )
  // A running job that is not the one shown is not polled: refresh the list until it ends,
  // so the start buttons unlock without leaving the tab.
  let listTimer: ReturnType<typeof setInterval> | null = null
  watch(
    () => jobs.value.some(j => ACTIVE_JOB_STATUSES.has(j.status) && j.id !== job.value?.id),
    (others) => {
      if (listTimer) clearInterval(listTimer)
      listTimer = null
      if (others && !disposed) listTimer = setInterval(() => loadJobs().catch(() => {}), 3000)
    }
  )

  function stop() {
    generation++
    if (timer) clearTimeout(timer)
    timer = null
  }

  function fetchJob(jobId: number) {
    return api<JobDetail<R>>(`/projects/${projectId}/jobs/${jobId}`)
  }

  function schedule() {
    const current = job.value
    if (disposed || !current || !ACTIVE_JOB_STATUSES.has(current.status)) return
    const gen = generation
    timer = setTimeout(async () => {
      let fresh: JobDetail<R> | null = null
      try {
        fresh = await fetchJob(current.id)
      } catch {
        // a missed poll is retried on the next tick
      }
      if (gen !== generation) return
      if (fresh) {
        job.value = fresh
        if (!ACTIVE_JOB_STATUSES.has(fresh.status)) {
          timer = null
          await loadJobs().catch(() => {}) // the list shows the final status and cost
          return
        }
      }
      schedule()
    }, 1000)
  }

  /** Show a job the API just returned (start, answers, cancel) and poll it if needed. */
  function track(detail: JobDetail<R>) {
    if (disposed) return
    stop()
    job.value = detail
    openedId.value = detail.id
    // The list may still show this job as running if it ended while another one was open.
    const listed = jobs.value.find(j => j.id === detail.id)
    if (listed && listed.status !== detail.status) loadJobs().catch(() => {})
    schedule()
  }

  async function open(jobId: number) {
    stop()
    const gen = generation
    const detail = await fetchJob(jobId)
    if (gen === generation) track(detail)
  }

  /** Hide the shown job; it stays in the list. */
  function close() {
    stop()
    job.value = null
    openedId.value = null
  }

  async function loadJobs() {
    jobs.value = await api<JobSummary[]>(`/projects/${projectId}/jobs`, { query: { kind } })
  }

  /**
   * Load the list and reopen what was shown before the tab was left. With nothing
   * remembered, a running job is opened, or the newest one when `latest` is set.
   */
  async function restore(latest: boolean) {
    await loadJobs()
    const remembered = openedId.value
    const fallback = jobs.value.find(j => ACTIVE_JOB_STATUSES.has(j.status))
      || (latest ? jobs.value[0] : undefined)
    const target = remembered ?? fallback?.id
    if (target === undefined) return
    try {
      await open(target)
    } catch {
      openedId.value = null
    }
  }

  onScopeDispose(() => {
    disposed = true
    stop()
    if (listTimer) clearInterval(listTimer)
  })

  return { job, jobs, openedId, active, anyActive, track, open, close, loadJobs, restore }
}
