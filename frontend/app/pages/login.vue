<script setup lang="ts">
import type { User } from '~/types'

const mode = ref<'login' | 'register'>('login')
const email = ref('')
const password = ref('')
const error = ref('')
const busy = ref(false)
const { user } = useAuth()

async function submit() {
  error.value = ''
  busy.value = true
  try {
    user.value = await api<User>(`/auth/${mode.value}`, {
      method: 'POST',
      body: { email: email.value, password: password.value }
    })
    await navigateTo('/')
  } catch (e) {
    error.value = errorText(e)
  } finally {
    busy.value = false
  }
}
</script>

<template>
  <div class="mx-auto mt-10 max-w-md">
    <B24Card>
      <template #header>
        <h1 class="text-xl font-semibold">
          {{ mode === 'login' ? 'Вход' : 'Регистрация' }}
        </h1>
        <p class="mt-1 text-sm opacity-70">
          Настройка порталов Битрикс24 по ТЗ: импорт, план, применение, отчёт.
        </p>
      </template>
      <form class="flex flex-col gap-4" @submit.prevent="submit">
        <B24FormField label="Email" required>
          <B24Input v-model="email" type="email" autocomplete="email" class="w-full" />
        </B24FormField>
        <B24FormField
          label="Пароль"
          required
          :hint="mode === 'register' ? 'не короче 8 символов' : undefined"
        >
          <B24Input
            v-model="password"
            type="password"
            :autocomplete="mode === 'login' ? 'current-password' : 'new-password'"
            class="w-full"
          />
        </B24FormField>
        <B24Alert v-if="error" color="air-primary-alert" :description="error" />
        <B24Button
          type="submit"
          color="air-primary"
          block
          :loading="busy"
          :label="mode === 'login' ? 'Войти' : 'Зарегистрироваться'"
        />
        <B24Button
          color="link"
          block
          :label="mode === 'login' ? 'Нет аккаунта? Регистрация' : 'Уже есть аккаунт? Вход'"
          @click="mode = mode === 'login' ? 'register' : 'login'; error = ''"
        />
      </form>
    </B24Card>
  </div>
</template>
