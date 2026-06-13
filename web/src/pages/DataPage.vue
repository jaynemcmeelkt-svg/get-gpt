<script setup lang="ts">
import { ref, computed, onMounted, inject } from 'vue'
import { getAccounts } from '../api/client'

const showToast = inject<(msg: string, type?: 'success' | 'error') => void>('showToast', () => {})

const accounts = ref<any[]>([])
const loading = ref(false)
const searchQuery = ref('')

const filtered = computed(() => {
  if (!searchQuery.value) return accounts.value
  const q = searchQuery.value.toLowerCase().trim()
  return accounts.value.filter((a: any) => (a.phone && a.phone.includes(q)) || (a.email && a.email.toLowerCase().includes(q)))
})

async function fetchAccounts() {
  loading.value = true
  try {
    accounts.value = await getAccounts()
  } catch (e) {
    console.error(e)
  } finally {
    loading.value = false
  }
}

function truncate(token: string) {
  if (!token) return ''
  if (token.length <= 16) return token
  return token.substring(0, 10) + '...' + token.substring(token.length - 8)
}

function copy(token: string, name: string) {
  navigator.clipboard.writeText(token).then(() => {
    showToast(`${name} 已复制到剪贴板`)
  }).catch(() => {
    showToast('复制失败', 'error')
  })
}

onMounted(fetchAccounts)
</script>

<template>
  <div class="space-y-6">
    <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
      <div class="flex flex-col md:flex-row justify-between items-start md:items-center border-b border-slate-100 pb-3 mb-4 gap-4">
        <div>
          <h2 class="text-base font-bold text-slate-900 uppercase tracking-wider">SQLite 数据库已存资产</h2>
          <p class="text-xs text-slate-500 mt-0.5">每次运行成功生成的 ChatGPT 账号信息均会持久化保存</p>
        </div>
        <div class="flex gap-2 w-full md:w-auto shrink-0">
          <input v-model="searchQuery" type="text" placeholder="检索邮箱或手机号..." class="bg-slate-50 border border-slate-200 rounded px-3 py-1.5 text-xs text-slate-800 focus:outline-none focus:bg-white focus:border-amber-500 w-full md:w-56 shadow-inner" />
          <button @click="fetchAccounts" :disabled="loading" class="bg-slate-800 hover:bg-slate-700 disabled:opacity-50 text-white text-xs px-3.5 py-1.5 rounded font-bold shrink-0 shadow-sm">
            {{ loading ? '刷新中...' : '刷新' }}
          </button>
        </div>
      </div>

      <div class="overflow-x-auto border border-slate-200 rounded-lg">
        <table class="w-full text-left text-sm border-collapse bg-white">
          <thead>
            <tr class="bg-slate-50 text-slate-600 border-b border-slate-200 text-xs uppercase tracking-wider">
              <th class="p-3">手机号</th>
              <th class="p-3">邮箱</th>
              <th class="p-3">密码</th>
              <th class="p-3">Access Token</th>
              <th class="p-3">Session Token</th>
              <th class="p-3">Refresh Token</th>
            </tr>
          </thead>
          <tbody class="divide-y divide-slate-100 text-xs font-mono text-slate-700">
            <tr v-if="filtered.length === 0">
              <td colspan="6" class="p-8 text-center text-slate-400">{{ loading ? '加载中...' : '未检索到记录' }}</td>
            </tr>
            <tr v-for="acc in filtered" :key="acc.phone" class="hover:bg-slate-50 transition">
              <td class="p-3 font-semibold text-slate-900">{{ acc.phone }}</td>
              <td class="p-3">{{ acc.email || '-' }}</td>
              <td class="p-3 text-slate-600">{{ acc.password || '-' }}</td>
              <td class="p-3">
                <span v-if="acc.access_token" @click="copy(acc.access_token, 'Access Token')" class="text-amber-600 hover:text-amber-700 cursor-pointer border-b border-dashed border-amber-500/40 pb-0.5">{{ truncate(acc.access_token) }}</span>
                <span v-else class="text-slate-400">-</span>
              </td>
              <td class="p-3">
                <span v-if="acc.session_token" @click="copy(acc.session_token, 'Session Token')" class="text-cyan-600 hover:text-cyan-700 cursor-pointer border-b border-dashed border-cyan-500/40 pb-0.5">{{ truncate(acc.session_token) }}</span>
                <span v-else class="text-slate-400">-</span>
              </td>
              <td class="p-3">
                <span v-if="acc.refresh_token" @click="copy(acc.refresh_token, 'Refresh Token')" class="text-purple-600 hover:text-purple-700 cursor-pointer border-b border-dashed border-purple-400/40 pb-0.5">{{ truncate(acc.refresh_token) }}</span>
                <span v-else class="text-slate-400">-</span>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
  </div>
</template>
