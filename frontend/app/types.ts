// Shapes returned by the API (backend/src/crmbuilder_web/routes).

export interface User {
  id: number
  email: string
}

export interface Counts {
  deal_categories: number
  fields: number
  smart_processes: number
  departments: number
  automation: number
  automation_pending: number
}

export interface Project {
  id: number
  name: string
  portal: string
  imported_at: string | null
  created_at: string
  allow_write: boolean
  counts: Counts | null
}

export type Semantic = 'process' | 'success' | 'failure'

export interface Stage {
  key: string
  name: string
  semantic: Semantic
  sort: number | null
  color: string | null
}

export interface Category {
  key: string
  name: string
  default: boolean
  sort: number | null
  stages: Stage[]
}

export interface EnumValue {
  key: string
  value: string
  default: boolean
}

export interface Field {
  key: string
  label: string
  type: string
  multiple: boolean
  required: boolean
  values: EnumValue[]
}

export interface SmartProcess {
  key: string
  title: string
  stages_enabled: boolean
  client_enabled: boolean
  automation_enabled: boolean
  categories: Category[]
}

export interface Department {
  key: string
  name: string
  parent: string | null
  sort: number | null
}

export interface AutomationRule {
  key: string
  stage: string
  kind: 'robot' | 'trigger'
  action: string | null
  event: string | null
  title: string
  when: string | null
  condition: string | null
  responsible: string | null
  details: string | null
  done: boolean
}

export interface Spec {
  portal: string
  crm: {
    deal_categories: Category[]
    fields: Record<string, Field[]>
    smart_processes: SmartProcess[]
  }
  structure: { departments: Department[] }
  automation: AutomationRule[]
}

export interface Change {
  action: 'create' | 'update' | 'delete' | 'replace'
  address: string
  destructive: boolean
  attributes: Record<string, { current: unknown, desired: unknown }>
  note: string
}

export interface Plan {
  portal: string
  summary: Record<string, number>
  changes: Change[]
}

export interface PlanResult {
  plan: Plan
  fingerprint: string
  blockers: { address: string, reason: string }[]
  manual: { key: string, title: string, stage: string }[]
  warnings: string[]
}

export type RunStatus =
  | 'pending' | 'running' | 'verified' | 'diverged' | 'failed' | 'interrupted' | 'unchanged'

export interface RunSummary {
  id: number
  kind: 'apply' | 'rollback'
  status: RunStatus
  rollback_of: number | null
  total: number
  done: number
  error: string | null
  created_at: string
  finished_at: string | null
  has_before: boolean
}

export interface RunDetail extends RunSummary {
  plan: Plan | null
  done_addresses: string[]
  failed: string | null
  residual: Plan | null
  warnings: string[]
  journal: { time: string, method: string, error?: string }[]
  journal_total: number
  can_rollback: boolean
}

// AI design, audit and report jobs (routes/jobs.py).

export interface AiModel {
  id: string
  label: string
}

export interface AiConfig {
  available: boolean
  default_model: string
  models: AiModel[]
}

export type JobKind = 'design' | 'audit' | 'report'

export type JobStatus = 'running' | 'waiting' | 'done' | 'failed' | 'cancelled' | 'interrupted'

export interface Usage {
  model: string
  requests: number
  input_tokens: number
  output_tokens: number
  cache_read_tokens: number
  cache_write_tokens: number
  cost_usd: number | null
}

export interface JobLogEntry {
  type: 'edit' | 'error' | 'questions' | 'answers' | 'info'
  text: string
  items?: string[]
}

export type Severity = 'error' | 'warning' | 'info'

export interface Finding {
  severity: Severity
  code: string
  address: string
  message: string
  hint: string
  source: string
}

export interface JobSummary {
  id: number
  kind: JobKind
  status: JobStatus
  created_at: string
  finished_at: string | null
  error: string | null
  cost_usd: number | null
  title: string
}

export interface JobDetail<R = unknown> extends JobSummary {
  params: Record<string, unknown>
  questions: string[] | null
  log: JobLogEntry[]
  result: R | null
  usage: Usage | null
  saved: boolean
}

export interface DesignResult {
  spec: Spec
  summary: string
  edits: string[]
  plan: Plan
}

export interface AuditResult {
  findings: Finding[]
  business_guess: string
  markdown: string
  counts: Record<Severity, number>
}

export interface ReportResult {
  markdown: string
  changes: number
  manual_steps: number
  since_run_id: number | null
}
