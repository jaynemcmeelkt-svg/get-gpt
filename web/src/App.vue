<script setup lang="ts">
import { ref, onMounted, provide } from 'vue'
import { useRouter, useRoute } from 'vue-router'
import { getConfig } from './api/client'

const router = useRouter()
const route = useRoute()
const backendConnected = ref(false)

const tabs = [
  { key: 'monitor', label: '执行与监控', path: '/monitor' },
  { key: 'config', label: '参数配置', path: '/config' },
  { key: 'database', label: '账号资产库', path: '/database' },
]

const activeTab = ref('monitor')

// 全局 toast
const toast = ref({ show: false, message: '', type: 'success' as 'success' | 'error' })
let toastTimer: ReturnType<typeof setTimeout> | null = null

function showToast(msg: string, type: 'success' | 'error' = 'success') {
  toast.value = { show: true, message: msg, type }
  if (toastTimer) clearTimeout(toastTimer)
  toastTimer = setTimeout(() => { toast.value.show = false }, 3000)
}

provide('showToast', showToast)

function switchTab(tab: typeof tabs[0]) {
  activeTab.value = tab.key
  router.push(tab.path)
}

onMounted(async () => {
  const current = tabs.find(t => t.path === route.path)
  if (current) activeTab.value = current.key

  try {
    await getConfig()
    backendConnected.value = true
  } catch {
    backendConnected.value = false
  }
})
</script>

<template>
  <div class="container mx-auto p-4 max-w-7xl">
    <header class="flex flex-col sm:flex-row justify-between items-start sm:items-center pb-4 mb-6 border-b border-slate-200 gap-4">
      <div>
        <div class="flex items-center gap-2">
          <span class="text-amber-600 font-bold text-xl">$</span>
          <h1 class="text-2xl font-bold tracking-wider text-slate-900">GET-GPT CONTROL</h1>
          <span class="text-xs bg-amber-500/10 text-amber-600 border border-amber-500/30 px-2 py-0.5 rounded">v3.0</span>
        </div>
        <p class="text-xs text-slate-500 mt-1">ChatGPT 手机号注册登录 // 协议直连模式智能管理后台</p>
      </div>
      <div class="flex items-center gap-4">
        <nav class="flex space-x-1 bg-slate-200/60 p-1 rounded-lg border border-slate-300 text-sm">
          <button
            v-for="tab in tabs"
            :key="tab.key"
            @click="switchTab(tab)"
            :class="activeTab === tab.key ? 'bg-amber-500 text-slate-950 font-bold' : 'text-slate-600 hover:text-slate-900 hover:bg-slate-200/30'"
            class="px-4 py-1.5 rounded-md transition duration-150"
          >{{ tab.label }}</button>
        </nav>
        <div class="hidden md:flex items-center gap-2 text-sm bg-white border border-slate-200 p-2 rounded-lg shadow-sm">
          <span class="text-slate-500">服务状态:</span>
          <span :class="backendConnected ? 'text-emerald-600' : 'text-rose-600'" class="font-bold flex items-center gap-1.5">
            <span class="w-2 h-2 rounded-full" :class="backendConnected ? 'bg-emerald-500 animate-pulse' : 'bg-rose-500'"></span>
            {{ backendConnected ? '已连接' : '已断开' }}
          </span>
        </div>
      </div>
    </header>

    <router-view />

    <!-- 全局 Toast -->
    <div
      v-if="toast.show"
      :class="toast.type === 'success' ? 'bg-emerald-50 border-emerald-200 text-emerald-800' : 'bg-rose-50 border-rose-200 text-rose-800'"
      class="fixed bottom-6 right-6 px-4 py-3 rounded-lg border shadow-xl flex items-center gap-2 text-xs transition duration-300 z-50"
    >
      <span>{{ toast.message }}</span>
    </div>
  </div>
</template>
