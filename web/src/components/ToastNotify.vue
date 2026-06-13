<script setup lang="ts">
import { ref } from 'vue'

const show = ref(false)
const message = ref('')
const type = ref<'success' | 'error'>('success')
let timer: ReturnType<typeof setTimeout> | null = null

function showToast(msg: string, t: 'success' | 'error' = 'success') {
  message.value = msg
  type.value = t
  show.value = true
  if (timer) clearTimeout(timer)
  timer = setTimeout(() => { show.value = false }, 3000)
}

defineExpose({ showToast })
</script>

<template>
  <div
    v-if="show"
    :class="type === 'success' ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-rose-50 border-rose-200 text-rose-800'"
    class="fixed bottom-6 right-6 px-4 py-3 rounded-lg border shadow-xl flex items-center gap-2 text-xs transition duration-300 z-50"
  >
    <span>{{ message }}</span>
  </div>
</template>
