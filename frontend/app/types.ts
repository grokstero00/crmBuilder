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

export interface PlanResult {
  plan: { portal: string, summary: Record<string, number>, changes: Change[] }
  manual: { key: string, title: string, stage: string }[]
  warnings: string[]
}
