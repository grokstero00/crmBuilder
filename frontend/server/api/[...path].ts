// Forwards /api/* to the Python API, so the browser stays on one origin and the
// session cookie needs no CORS. The target comes from NUXT_API_URL at runtime.
export default defineEventHandler((event) => {
  const { apiUrl } = useRuntimeConfig(event)
  const target = new URL(event.path, apiUrl)
  return proxyRequest(event, target.toString())
})
