export default defineNuxtConfig({
  compatibilityDate: '2026-10-01',
  modules: ['@bitrix24/b24ui-nuxt'],
  css: ['~/assets/css/main.css'],
  ssr: false,
  devtools: { enabled: false },
  app: {
    head: {
      htmlAttrs: { lang: 'ru' },
      title: 'crmbuilder'
    }
  },
  runtimeConfig: {
    // Where server/api/[...path].ts forwards /api requests; set NUXT_API_URL in Docker.
    apiUrl: 'http://localhost:8000'
  }
})
