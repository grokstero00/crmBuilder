<script setup lang="ts">
// Confirmation before anything is written: type the portal name, accept destructive changes.
const props = defineProps<{
  title: string
  portal: string
  destructive: boolean
  busy: boolean
  error: string
}>()
const emit = defineEmits<{ confirm: [allowDestructive: boolean, typed: string] }>()
const open = defineModel<boolean>('open', { required: true })

const typed = ref('')
const allowDestructive = ref(false)
watch(open, (value) => {
  if (value) {
    typed.value = ''
    allowDestructive.value = false
  }
})

const ready = computed(() =>
  typed.value.trim() === props.portal && (!props.destructive || allowDestructive.value)
)
</script>

<template>
  <B24Modal v-model:open="open" :title="title" :dismissible="!busy">
    <template #body>
      <div class="flex flex-col gap-4">
        <slot />
        <B24Alert
          v-if="destructive"
          color="air-primary-alert"
          title="В плане есть удаление или пересоздание"
          description="Данные в удаляемых полях, стадиях и воронках будут потеряны. Откат вернёт структуру, но не данные."
        />
        <B24Checkbox
          v-if="destructive"
          v-model="allowDestructive"
          label="Понимаю, что данные могут быть потеряны"
        />
        <B24FormField :label="`Введите имя портала: ${portal}`" required>
          <B24Input v-model="typed" class="w-full" :placeholder="portal" autocomplete="off" />
        </B24FormField>
        <B24Alert v-if="error" color="air-primary-alert" :description="error" />
      </div>
    </template>
    <template #footer>
      <div class="flex w-full justify-end gap-2">
        <B24Button label="Отмена" :disabled="busy" @click="open = false" />
        <B24Button
          color="air-primary"
          label="Применить"
          :loading="busy"
          :disabled="!ready"
          @click="emit('confirm', allowDestructive, typed.trim())"
        />
      </div>
    </template>
  </B24Modal>
</template>
