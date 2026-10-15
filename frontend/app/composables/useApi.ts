import type { User } from '~/types'

/** Error text for the user: the API puts it in `detail` (a string, or a list for 422). */
export function errorText(error: unknown): string {
  const data = (error as { data?: { detail?: unknown } })?.data
  const detail = data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) {
    return detail.map((d: { msg?: string }) => d.msg).filter(Boolean).join('; ')
  }
  const status = (error as { statusCode?: number })?.statusCode
  if (status && status >= 500) return 'Сервер недоступен, попробуйте ещё раз'
  return (error as Error)?.message || 'Неизвестная ошибка'
}

export function api<T>(path: string, options: Parameters<typeof $fetch>[1] = {}) {
  return $fetch<T>(`/api${path}`, { credentials: 'same-origin', ...options } as never)
}

export function useAuth() {
  const user = useState<User | null>('user', () => null)
  const checked = useState('auth-checked', () => false)

  async function refresh() {
    try {
      user.value = await api<User>('/auth/me')
    } catch {
      user.value = null
    }
    checked.value = true
  }

  async function logout() {
    await api('/auth/logout', { method: 'POST' }).catch(() => {})
    user.value = null
    await navigateTo('/login')
  }

  return { user, checked, refresh, logout }
}
