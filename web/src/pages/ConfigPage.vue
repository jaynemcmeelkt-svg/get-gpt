<script setup lang="ts">
import { ref, onMounted, inject } from 'vue'
import { getConfig, saveConfig } from '../api/client'
import GptSmsConfig from '../components/GptSmsConfig.vue'
import YihaoSmsConfig from '../components/YihaoSmsConfig.vue'
import type { AppConfig } from '../types/config'

const showToast = inject<(msg: string, type?: 'success' | 'error') => void>('showToast', () => {})

const form = ref<AppConfig>({
  flow_type: 'register',
  gui_config: { login_headless: false, oauth_headless: true, auto_detect_geo: true, locale: 'en-US', timezone: 'America/New_York' },
  proxy_config: { mode: 'api', api_url: '', api_urls: [''], strategy: 'fixed', static_proxy: '' },
  sms_config: {
    gpt_sms: { provider: 'smsbower', api_key: '', service: 'dr', country: '6', preferred_countries: [], min_price: null, max_price: null, activation_id: null, otp_timeout_seconds: 180, otp_retry_attempts: 3 },
    paypal_sms: { provider: 'yihao', mode: 'temp', api_key: '', service: '', otp_timeout_seconds: 180, fixed_config: { phone: '', sms_url: '', sms_urls: [''], strategy: 'fixed' } }
  },
  account_config: { email: '', password: '', phone: '' }
})

async function load() {
  try {
    const cfg = await getConfig()
    if (cfg && Object.keys(cfg).length > 0) {
      Object.assign(form.value, cfg)
      if (!form.value.proxy_config.api_urls?.length) form.value.proxy_config.api_urls = [form.value.proxy_config.api_url || '']
      if (!form.value.sms_config.paypal_sms.fixed_config.sms_urls?.length) form.value.sms_config.paypal_sms.fixed_config.sms_urls = [form.value.sms_config.paypal_sms.fixed_config.sms_url || '']
      if (form.value.gui_config.auto_detect_geo === undefined) form.value.gui_config.auto_detect_geo = true
      if (form.value.sms_config.gpt_sms.min_price === undefined) form.value.sms_config.gpt_sms.min_price = null
      if (form.value.sms_config.gpt_sms.otp_timeout_seconds === undefined) form.value.sms_config.gpt_sms.otp_timeout_seconds = 180
      if (form.value.sms_config.gpt_sms.otp_retry_attempts === undefined) form.value.sms_config.gpt_sms.otp_retry_attempts = 3
      if (form.value.sms_config.paypal_sms.otp_timeout_seconds === undefined) form.value.sms_config.paypal_sms.otp_timeout_seconds = 180
    }
  } catch { showToast('获取配置失败', 'error') }
}

async function save() {
  try {
    if (form.value.proxy_config.api_urls?.length) form.value.proxy_config.api_url = form.value.proxy_config.api_urls[0]
    if (form.value.sms_config.paypal_sms.fixed_config.sms_urls?.length) form.value.sms_config.paypal_sms.fixed_config.sms_url = form.value.sms_config.paypal_sms.fixed_config.sms_urls[0]
    await saveConfig(form.value)
    showToast('配置已保存至 data/config.json')
  } catch { showToast('保存失败', 'error') }
}

function addProxyUrl() { form.value.proxy_config.api_urls.push('') }
function removeProxyUrl(i: number) { form.value.proxy_config.api_urls.splice(i, 1) }

function handleToast(msg: string, type?: string) {
  showToast(msg, (type as 'success' | 'error') || 'success')
}

onMounted(load)
</script>

<template>
  <div class="space-y-6">
    <div class="flex justify-between items-center bg-white border border-slate-200 rounded-xl px-5 py-3 shadow-sm">
      <div>
        <h2 class="text-sm font-bold text-slate-900 uppercase tracking-wider">配置管理器</h2>
        <p class="text-xs text-slate-500 mt-0.5">更改将长效保存在服务器端 data/config.json</p>
      </div>
      <div class="flex gap-2">
        <button @click="load" class="bg-slate-100 hover:bg-slate-200 border border-slate-200 text-slate-700 font-semibold px-4 py-1.5 rounded text-xs transition">重新加载</button>
        <button @click="save" class="bg-amber-500 hover:bg-amber-600 text-slate-950 font-bold px-5 py-1.5 rounded text-xs transition shadow-sm">保存配置</button>
      </div>
    </div>

    <div class="grid grid-cols-1 md:grid-cols-12 gap-6">
      <!-- 左栏 -->
      <div class="md:col-span-6 space-y-6">
        <!-- 1. 账号预设 -->
        <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          <h3 class="text-sm font-bold text-slate-800 border-b border-slate-100 pb-1.5">1. 账号预设参数</h3>
          <div class="grid grid-cols-1 gap-3 text-xs">
            <div>
              <label class="block text-slate-500 mb-1 font-semibold">默认电子邮箱</label>
              <input v-model="form.account_config.email" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 focus:outline-none focus:bg-white focus:border-amber-500 text-slate-800" placeholder="alias@yourdomain.com" />
            </div>
            <div>
              <label class="block text-slate-500 mb-1 font-semibold">默认登录密码 (留空自动生成)</label>
              <input v-model="form.account_config.password" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 focus:outline-none focus:bg-white focus:border-amber-500 text-slate-800" placeholder="留空将自动生成" />
            </div>
            <div>
              <label class="block text-slate-500 mb-1 font-semibold">默认手机号码 (仅登录分支)</label>
              <input v-model="form.account_config.phone" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 focus:outline-none focus:bg-white focus:border-amber-500 text-slate-800" placeholder="纯数字" />
            </div>
          </div>
        </div>

        <!-- 2. 代理网络 -->
        <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          <h3 class="text-sm font-bold text-slate-800 border-b border-slate-100 pb-1.5">2. 代理网络配置</h3>
          <div class="text-xs space-y-3">
            <div class="flex gap-4">
              <label class="flex items-center gap-2 text-slate-700 cursor-pointer font-semibold">
                <input v-model="form.proxy_config.mode" type="radio" value="api" class="accent-amber-500" /> 动态获取 (API)
              </label>
              <label class="flex items-center gap-2 text-slate-700 cursor-pointer font-semibold">
                <input v-model="form.proxy_config.mode" type="radio" value="static" class="accent-amber-500" /> 静态代理
              </label>
            </div>

            <div v-if="form.proxy_config.mode === 'api'" class="space-y-2">
              <div class="flex justify-between items-center">
                <label class="text-slate-500 font-semibold">代理 IP 提取 API 链接池</label>
                <button @click="addProxyUrl" class="text-[10px] text-amber-600 font-bold border border-amber-300 px-2 py-0.5 rounded bg-amber-50/50">+ 添加</button>
              </div>
              <div v-for="(_, i) in form.proxy_config.api_urls" :key="i" class="flex gap-2 items-center">
                <input v-model="form.proxy_config.api_urls[i]" type="text" class="flex-1 bg-slate-50 border border-slate-200 rounded px-3 py-1.5 focus:outline-none focus:bg-white focus:border-amber-500 text-slate-800 text-xs" placeholder="提取 API 链接..." />
                <button @click="removeProxyUrl(i)" class="text-rose-600 text-xs px-2 py-1 border border-rose-200 rounded bg-rose-50" :disabled="form.proxy_config.api_urls.length <= 1">删除</button>
              </div>
              <div class="flex gap-4 items-center pt-1.5 border-t border-slate-100">
                <label class="text-[11px] text-slate-500 font-semibold">提取策略:</label>
                <label class="flex items-center gap-1.5 text-xs text-slate-700"><input v-model="form.proxy_config.strategy" type="radio" value="fixed" class="accent-amber-500" /> 固定</label>
                <label class="flex items-center gap-1.5 text-xs text-slate-700"><input v-model="form.proxy_config.strategy" type="radio" value="random" class="accent-amber-500" /> 随机</label>
              </div>
            </div>

            <div v-if="form.proxy_config.mode === 'static'">
              <label class="block text-slate-500 mb-1 font-semibold">静态代理地址</label>
              <input v-model="form.proxy_config.static_proxy" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 focus:outline-none focus:bg-white focus:border-amber-500 text-slate-800" placeholder="socks5h://user:pass@host:port" />
            </div>
          </div>
        </div>

        <!-- 3. 运行环境 -->
        <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
          <h3 class="text-sm font-bold text-slate-800 border-b border-slate-100 pb-1.5">3. 运行环境与区域设定</h3>
          <div class="text-xs">
            <label class="flex items-center gap-1.5 cursor-pointer text-slate-700 font-semibold mb-3">
              <input v-model="form.gui_config.auto_detect_geo" type="checkbox" class="accent-amber-500 rounded" /> 基于代理 IP 自动定位区域与时区 (推荐)
            </label>
          </div>
          <div class="grid grid-cols-2 gap-3 text-xs">
            <div>
              <label class="block text-slate-500 mb-1 font-semibold">浏览器区域 (Locale)</label>
              <input v-model="form.gui_config.locale" :disabled="form.gui_config.auto_detect_geo" :placeholder="form.gui_config.auto_detect_geo ? '自动获取' : 'en-US'" :class="form.gui_config.auto_detect_geo ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-slate-50 text-slate-800'" class="w-full border border-slate-200 rounded px-3 py-2 focus:outline-none" />
            </div>
            <div>
              <label class="block text-slate-500 mb-1 font-semibold">浏览器时区 (Timezone)</label>
              <input v-model="form.gui_config.timezone" :disabled="form.gui_config.auto_detect_geo" :placeholder="form.gui_config.auto_detect_geo ? '自动获取' : 'America/New_York'" :class="form.gui_config.auto_detect_geo ? 'bg-slate-100 text-slate-400 cursor-not-allowed' : 'bg-slate-50 text-slate-800'" class="w-full border border-slate-200 rounded px-3 py-2 focus:outline-none" />
            </div>
            <div class="col-span-2 flex gap-4 pt-2 border-t border-slate-100">
              <label class="flex items-center gap-1.5 cursor-pointer text-slate-700"><input v-model="form.gui_config.login_headless" type="checkbox" class="accent-amber-500 rounded" /> 注册/登录 Headless</label>
              <label class="flex items-center gap-1.5 cursor-pointer text-slate-700"><input v-model="form.gui_config.oauth_headless" type="checkbox" class="accent-amber-500 rounded" /> OAuth Headless</label>
            </div>
          </div>
        </div>
      </div>

      <!-- 右栏 -->
      <div class="md:col-span-6 space-y-6">
        <GptSmsConfig v-model="form.sms_config.gpt_sms" @toast="handleToast" />
        <YihaoSmsConfig v-model="form.sms_config.paypal_sms" @toast="handleToast" />
      </div>
    </div>
  </div>
</template>
