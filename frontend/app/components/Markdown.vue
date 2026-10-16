<script setup lang="ts">
// Markdown from the engine (summaries, audit, client report). Raw HTML in the source is
// escaped (`html: false`), so v-html only ever gets markup that markdown-it produced.
// Images are off: portal labels and model text could otherwise make the browser load
// any external URL as soon as a report is shown.
import MarkdownIt from 'markdown-it'

const props = defineProps<{ source: string }>()

const md = new MarkdownIt({ html: false, linkify: true }).disable('image')
const html = computed(() => md.render(props.source || ''))
</script>

<template>
  <!-- No typography plugin: element styles are set here with Tailwind variants. -->
  <div
    class="overflow-x-auto text-sm leading-relaxed break-words
      [&>*:first-child]:mt-0 [&>*:last-child]:mb-0
      [&_h1]:mt-6 [&_h1]:mb-3 [&_h1]:text-xl [&_h1]:font-semibold
      [&_h2]:mt-5 [&_h2]:mb-2 [&_h2]:text-lg [&_h2]:font-semibold
      [&_h3]:mt-4 [&_h3]:mb-2 [&_h3]:font-semibold
      [&_h4]:mt-3 [&_h4]:mb-1 [&_h4]:font-semibold
      [&_p]:my-2
      [&_ul]:my-2 [&_ul]:list-disc [&_ul]:pl-5
      [&_ol]:my-2 [&_ol]:list-decimal [&_ol]:pl-5
      [&_li]:my-0.5
      [&_a]:text-(--ui-color-accent-main-link) [&_a]:underline
      [&_code]:rounded [&_code]:bg-(--ui-color-bg-content-secondary) [&_code]:px-1 [&_code]:font-mono [&_code]:text-[0.85em]
      [&_pre]:my-3 [&_pre]:overflow-x-auto [&_pre]:rounded [&_pre]:bg-(--ui-color-bg-content-secondary) [&_pre]:p-3
      [&_pre_code]:bg-transparent [&_pre_code]:p-0
      [&_blockquote]:my-2 [&_blockquote]:border-l-4 [&_blockquote]:border-(--ui-color-divider-default) [&_blockquote]:pl-3 [&_blockquote]:opacity-80
      [&_hr]:my-4 [&_hr]:border-(--ui-color-divider-default)
      [&_table]:my-3 [&_table]:w-full [&_table]:border-collapse
      [&_th]:border [&_th]:border-(--ui-color-divider-default) [&_th]:bg-(--ui-color-bg-content-secondary) [&_th]:px-2 [&_th]:py-1 [&_th]:text-left [&_th]:font-medium
      [&_td]:border [&_td]:border-(--ui-color-divider-default) [&_td]:px-2 [&_td]:py-1 [&_td]:align-top"
    v-html="html"
  />
</template>
