<script setup lang="ts">
import { ref, onMounted, inject } from 'vue'
import { getRuns, getRunStats, getRunLog, runFlow, runOAuth, getConfig } from '../api/client'

const showToast = inject<(msg: string, type?: 'success' | 'error') => void>('showToast', () => {})

const runs = ref<any[]>([])
const stats = ref({ total: 0, success: 0, failed: 0, success_rate: 0, errors: {} as Record<string, number> })
const selectedRunId = ref('')
const logOutput = ref('')
const running = ref(false)
const oauthRunning = ref(false)
const lastRunResult = ref<any>(null)

async function fetchData() {
  try {
    const [r, s] = await Promise.all([getRuns(), getRunStats()])
    runs.value = r
    stats.value = s
  } catch (e) { console.error(e) }
}

async function selectRun(run: any) {
  selectedRunId.value = run.run_id
  logOutput.value = `[INFO] 正在拉取运行记录 ${run.run_id} ...\n\n`
  try {
    const detail = await getRunLog(run.run_id)
    logOutput.value = `[运行记录]\n创建: ${fmtTime(run.created_at)}\n类型: ${run.flow_type === 'register' ? '注册' : '登录'}\n状态: ${run.status.toUpperCase()}\n手机: ${run.phone_used || '-'}\n邮箱: ${run.email_used || '-'}\n代理: ${run.proxy_used || '-'}\n\n[详情]:\n${JSON.stringify(detail, null, 2)}`
  } catch (e: any) { logOutput.value = `[ERROR] ${e.message}` }
}

async function doFlow() {
  running.value = true
  logOutput.value = '[START] 正在启动协议流...\n'
  lastRunResult.value = null
  try {
    const config = await getConfig()
    const result = await runFlow(config)
    lastRunResult.value = result
    logOutput.value += `\n[SUCCESS]\n${JSON.stringify(result, null, 2)}`
    showToast('协议任务执行成功！')
    await fetchData()
  } catch (e: any) {
    logOutput.value += `\n[FAILED] ${e.message}`
    showToast(`执行失败: ${e.message}`, 'error')
    await fetchData()
  } finally { running.value = false }
}

async function doOAuth() {
  if (!lastRunResult.value) return
  oauthRunning.value = true
  logOutput.value += '\n[OAuth] 开始换取令牌...'
  try {
    const config = await getConfig()
    const result = await runOAuth({ req: config, login_result: lastRunResult.value })
    logOutput.value += `\n[SUCCESS] OAuth 成功\n${JSON.stringify(result, null, 2)}`
    showToast('OAuth 令牌获取成功！')
    await fetchData()
  } catch (e: any) {
    logOutput.value += `\n[FAILED] ${e.message}`
    showToast(`OAuth 失败: ${e.message}`, 'error')
  } finally { oauthRunning.value = false }
}

function fmtTime(t: string) {
  if (!t) return '-'
  try {
    const d = new Date(t)
    return `${d.getMonth()+1}/${d.getDate()} ${String(d.getHours()).padStart(2,'0')}:${String(d.getMinutes()).padStart(2,'0')}:${String(d.getSeconds()).padStart(2,'0')}`
  } catch { return t }
}

function statusClass(s: string) {
  switch (s) {
    case 'success': return 'bg-emerald-50 text-emerald-600 border border-emerald-200'
    case 'failed': return 'bg-rose-50 text-rose-600 border border-rose-200'
    case 'running': return 'bg-amber-50 text-amber-600 border border-amber-200 animate-pulse'
    default: return 'bg-slate-100 text-slate-500'
  }
}

onMounted(fetchData)
</script>

<template>
  <div class="space-y-6">
    <!-- 统计卡片 -->
    <div class="grid grid-cols-1 md:grid-cols-4 gap-4">
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex flex-col justify-between gap-4">
        <div class="text-xs text-slate-500 uppercase tracking-wider font-semibold">快速运行控制</div>
        <div class="flex gap-2">
          <button @click="doFlow" :disabled="running" class="flex-1 bg-amber-500 hover:bg-amber-600 disabled:bg-slate-100 disabled:text-slate-400 text-slate-950 font-bold py-2 px-3 rounded text-sm transition shadow-sm">
            {{ running ? '执行中...' : '启动协议流' }}
          </button>
          <button v-if="lastRunResult?.run_id" @click="doOAuth" :disabled="oauthRunning" class="bg-cyan-500 hover:bg-cyan-600 disabled:bg-slate-100 text-slate-950 font-bold py-2 px-3 rounded text-sm transition shadow-sm">
            Codex授权
          </button>
        </div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex items-center justify-between">
        <div>
          <div class="text-xs text-slate-500 uppercase font-semibold">成功率</div>
          <div class="text-2xl font-bold mt-1" :class="stats.success_rate >= 80 ? 'text-emerald-600' : 'text-amber-600'">{{ stats.success_rate }}%</div>
        </div>
        <div class="text-slate-200 text-3xl font-bold">%</div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex items-center justify-between">
        <div>
          <div class="text-xs text-slate-500 uppercase font-semibold">运行总数</div>
          <div class="text-2xl font-bold text-slate-800 mt-1">{{ stats.total }} 次</div>
        </div>
      </div>
      <div class="bg-white border border-slate-200 rounded-xl p-4 shadow-sm flex items-center justify-between">
        <div>
          <div class="text-xs text-slate-500 uppercase font-semibold">失败数</div>
          <div class="text-2xl font-bold text-rose-600 mt-1">{{ stats.failed }} 次</div>
        </div>
      </div>
    </div>

    <!-- 运行历史 + 日志 -->
    <div class="grid grid-cols-1 lg:grid-cols-12 gap-6">
      <div class="lg:col-span-7 bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
        <div class="flex justify-between items-center border-b border-slate-100 pb-2 mb-4">
          <h2 class="text-base font-bold text-slate-900 uppercase tracking-wider">历史运行记录</h2>
          <button @click="fetchData" class="text-xs text-slate-500 hover:text-slate-800">刷新</button>
        </div>
        <div class="overflow-y-auto max-h-[460px] border border-slate-200 rounded-lg">
          <table class="w-full text-left text-xs border-collapse">
            <thead>
              <tr class="bg-slate-50 text-slate-600 border-b border-slate-200">
                <th class="p-2.5">时间</th><th class="p-2.5">类型</th><th class="p-2.5">状态</th><th class="p-2.5">手机号</th><th class="p-2.5">邮箱</th><th class="p-2.5">日志</th>
              </tr>
            </thead>
            <tbody class="divide-y divide-slate-100 text-slate-700">
              <tr v-if="runs.length === 0"><td colspan="6" class="p-6 text-center text-slate-400">暂无记录</td></tr>
              <tr v-for="run in runs" :key="run.run_id" @click="selectRun(run)" :class="selectedRunId === run.run_id ? 'bg-amber-500/10 border-l-2 border-l-amber-500' : 'hover:bg-slate-50'" class="cursor-pointer transition">
                <td class="p-2.5 font-mono text-slate-500">{{ fmtTime(run.created_at) }}</td>
                <td class="p-2.5 font-semibold" :class="run.flow_type === 'register' ? 'text-cyan-600' : 'text-purple-600'">{{ run.flow_type === 'register' ? '注册' : '登录' }}</td>
                <td class="p-2.5"><span :class="statusClass(run.status)" class="px-1.5 py-0.5 rounded text-[10px] font-bold">{{ run.status.toUpperCase() }}</span></td>
                <td class="p-2.5">{{ run.phone_used || '-' }}</td>
                <td class="p-2.5 truncate max-w-[120px]">{{ run.email_used || '-' }}</td>
                <td class="p-2.5"><button class="text-amber-600 hover:text-amber-700 underline font-bold">查看</button></td>
              </tr>
            </tbody>
          </table>
        </div>
      </div>

      <div class="lg:col-span-5 bg-white border border-slate-200 rounded-xl p-5 shadow-sm flex flex-col">
        <div class="flex justify-between items-center border-b border-slate-100 pb-2 mb-4">
          <h2 class="text-base font-bold text-slate-900 uppercase tracking-wider">详细日志</h2>
          <button @click="logOutput = ''; selectedRunId = ''" class="text-xs text-slate-500 hover:text-slate-800">清屏</button>
        </div>
        <div class="flex-1 bg-slate-50 border border-slate-200 rounded-lg overflow-hidden min-h-[300px]">
          <div class="bg-slate-200/60 px-3 py-1.5 text-[10px] text-slate-600 flex justify-between items-center border-b border-slate-200">
            <span class="font-semibold">{{ selectedRunId ? `Run ID: ${selectedRunId}` : '当前执行监控终端' }}</span>
            <span class="text-slate-500 font-bold">LOG READER</span>
          </div>
          <div class="p-3 overflow-y-auto flex-1 text-xs leading-relaxed max-h-[360px] font-mono text-slate-800">
            <div v-if="running" class="flex items-center gap-2 text-amber-600 font-semibold mb-2">
              <span class="w-2 h-2 bg-amber-500 rounded-full animate-ping"></span>
              协议逻辑正在后台执行中...
            </div>
            <pre class="whitespace-pre-wrap break-all text-slate-800" v-if="logOutput">{{ logOutput }}</pre>
            <div v-else class="text-slate-400 text-center py-16">请在左侧列表中点击运行记录查看日志</div>
          </div>
        </div>
      </div>
    </div>

    <!-- 错误统计 -->
    <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm">
      <h2 class="text-base font-bold text-rose-600 border-b border-slate-100 pb-2 mb-4 uppercase tracking-wider">错误统计</h2>
      <div v-if="Object.keys(stats.errors).length === 0" class="text-slate-400 text-sm text-center py-4">暂无失败错误统计</div>
      <div v-else class="grid grid-cols-1 md:grid-cols-2 gap-4 text-xs font-mono">
        <div v-for="(count, err) in stats.errors" :key="String(err)" class="flex items-center justify-between bg-slate-50 border border-slate-200 p-2.5 rounded-lg">
          <span class="text-rose-700 truncate mr-4">{{ err }}</span>
          <span class="bg-rose-50 text-rose-600 px-2 py-0.5 rounded-full border border-rose-200 font-bold shrink-0">{{ count }} 次</span>
        </div>
      </div>
    </div>
  </div>
</template>
