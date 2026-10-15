const PUBLIC = new Set(['/login'])

export default defineNuxtRouteMiddleware(async (to) => {
  const { user, checked, refresh } = useAuth()
  if (!checked.value) await refresh()
  if (!user.value && !PUBLIC.has(to.path)) return navigateTo('/login')
  if (user.value && to.path === '/login') return navigateTo('/')
})
