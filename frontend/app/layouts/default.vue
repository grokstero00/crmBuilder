<script setup lang="ts">
const { user, logout } = useAuth()
</script>

<template>
  <div class="min-h-screen bg-(--ui-color-bg-content-primary)">
    <header class="border-b border-(--ui-color-divider-default) bg-(--ui-color-bg-content-secondary)">
      <div class="mx-auto flex max-w-6xl items-center justify-between gap-4 px-4 py-3">
        <NuxtLink to="/" class="text-lg font-semibold">crmbuilder</NuxtLink>
        <div v-if="user" class="flex items-center gap-3 text-sm">
          <span class="hidden sm:inline">{{ user.email }}</span>
          <B24Button size="sm" label="Выйти" @click="logout" />
        </div>
      </div>
    </header>
    <main class="mx-auto max-w-6xl px-4 py-6">
      <slot />
    </main>
  </div>
</template>
