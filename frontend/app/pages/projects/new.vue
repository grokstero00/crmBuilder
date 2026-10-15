<script setup lang="ts">
import type { Project } from '~/types'

const name = ref('')
const webhook = ref('')
const error = ref('')
const busy = ref(false)

async function submit() {
  error.value = ''
  busy.value = true
  try {
    const project = await api<Project>('/projects', {
      method: 'POST',
      body: { name: name.value, webhook: webhook.value }
    })
    await navigateTo(`/projects/${project.id}`)
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="mx-auto max-w-2xl">
    <B24Card>
      <template #header>
        <h1 class="text-xl font-semibold">Новый проект</h1>
      </template>
      <form class="flex flex-col gap-4" @submit.prevent="submit">
        <B24FormField label="Название" required hint="например, «Клиника Здоровье»">
          <B24Input v-model="name" class="w-full" />
        </B24FormField>
        <B24FormField
          label="Входящий вебхук"
          required
          description="https://<портал>.bitrix24.ru/rest/<id>/<код>/"
        >
          <B24Input v-model="webhook" class="w-full" placeholder="https://..." />
        </B24FormField>
        <div class="text-sm opacity-80">
          <p class="font-medium">Где взять вебхук</p>
          <ol class="ml-5 list-decimal">
            <li>В Битрикс24 откройте «Разработчикам» → «Другое» → «Входящий вебхук».</li>
            <li>Отметьте права: CRM (crm) и Структура компании (department).</li>
            <li>Сохраните и скопируйте адрес вебхука сюда.</li>
          </ol>
          <p class="mt-2">
            Вебхук хранится в зашифрованном виде. Пока сервис только читает портал:
            импорт и план ничего на нём не меняют.
          </p>
        </div>
        <B24Alert v-if="error" color="air-primary-alert" :description="error" />
        <div class="flex gap-2">
          <B24Button type="submit" color="air-primary" :loading="busy" label="Создать" />
          <B24Button to="/" label="Отмена" />
        </div>
      </form>
    </B24Card>
  </div>
</template>
