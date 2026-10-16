import type { Usage } from '~/types'

/** Russian plural: pluralRu(3, ['запрос', 'запроса', 'запросов']) -> 'запроса'. */
export function pluralRu(n: number, forms: [string, string, string]): string {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return forms[0]
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 12 || mod100 > 14)) return forms[1]
  return forms[2]
}

export function formatDateTime(value: string | null | undefined): string {
  return value ? new Date(value).toLocaleString('ru-RU') : ''
}

/** '$0.004': two significant digits below a dollar, cents above. */
export function formatCost(cost: number | null | undefined): string {
  if (cost === null || cost === undefined) return ''
  return `$${cost >= 1 ? cost.toFixed(2) : Number(cost.toPrecision(2))}`
}

/** '≈ $0.004, 3 запроса' for a job that called the model. */
export function formatUsage(usage: Usage | null | undefined): string {
  if (!usage || !usage.requests) return ''
  const requests = `${usage.requests} ${pluralRu(usage.requests, ['запрос', 'запроса', 'запросов'])}`
  const cost = formatCost(usage.cost_usd)
  return cost ? `≈ ${cost}, ${requests}` : `${requests}, стоимость неизвестна`
}
