<script setup lang="ts">
import { reactive, ref, watch } from 'vue'
import { testSmsKey, syncSmsGoods } from '../api/client'
import type { PaypalSmsConfig } from '../types/config'

const props = defineProps<{
  modelValue: PaypalSmsConfig
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: PaypalSmsConfig): void
  (e: 'toast', msg: string, type?: string): void
}>()

const form = reactive<PaypalSmsConfig>({
  ...props.modelValue,
  fixed_config: { ...props.modelValue.fixed_config }
})

watch(() => props.modelValue, (v) => {
  Object.assign(form, { ...v, fixed_config: { ...v.fixed_config } })
}, { deep: true })

function sync() {
  emit('update:modelValue', { ...form, fixed_config: { ...form.fixed_config } })
}

const testing = ref(false)
const syncing = ref(false)
const testState = reactive({ ok: false, balance: '', goods: [] as any[] })

async function doTest() {
  if (!form.api_key) { emit('toast', '请先填写 API 密钥！', 'error'); return }
  testing.value = true
  try {
    const res = await testSmsKey({ provider: form.provider, api_key: form.api_key })
    testState.ok = true; testState.balance = res.balance
    emit('toast', '密钥验证成功！可以同步商品数据')
  } catch (e: any) {
    testState.ok = false; testState.balance = ''; testState.goods = []
    emit('toast', `验证失败: ${e.message}`, 'error')
  } finally { testing.value = false }
}

async function doSync() {
  if (!form.api_key) { emit('toast', '请先填写 API 密钥！', 'error'); return }
  syncing.value = true
  try {
    const res = await syncSmsGoods({ provider: form.provider, api_key: form.api_key, service: form.service })
    testState.goods = res.goods || []
    emit('toast', '商品数据同步成功！')
  } catch (e: any) {
    testState.goods = []
    emit('toast', `同步失败: ${e.message}`, 'error')
  } finally { syncing.value = false }
}

function addSmsUrl() {
  form.fixed_config.sms_urls.push('')
  sync()
}

function removeSmsUrl(i: number) {
  form.fixed_config.sms_urls.splice(i, 1)
  sync()
}
</script>

<template>
  <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
    <div class="flex justify-between items-center border-b border-slate-100 pb-1.5">
      <h3 class="text-sm font-bold text-slate-800">5. PayPal 一浩接码设置</h3>
      <div class="flex items-center gap-2">
        <button @click="doTest" :disabled="testing" class="bg-slate-100 hover:bg-slate-200 border border-slate-300 text-cyan-600 text-[10px] px-2.5 py-1 rounded transition font-bold">
          {{ testing ? '验证中...' : '测试一浩 Key' }}
        </button>
        <button v-if="testState.ok" @click="doSync" :disabled="syncing" class="bg-cyan-600 hover:bg-cyan-700 border border-cyan-700 text-white text-[10px] px-2.5 py-1 rounded transition font-bold">
          {{ syncing ? '同步中...' : '同步数据' }}
        </button>
      </div>
    </div>

    <div class="text-xs space-y-3">
      <div v-if="testState.ok" class="p-2.5 bg-slate-50 border border-slate-200 rounded-lg shadow-inner">
        <div class="flex justify-between items-center">
          <span class="text-slate-500">账户余额:</span>
          <span class="text-emerald-600 font-bold">{{ testState.balance }}</span>
        </div>
      </div>

      <div class="flex gap-4">
        <label class="flex items-center gap-2 text-slate-700 cursor-pointer font-semibold">
          <input v-model="form.mode" @change="sync" type="radio" value="temp" class="accent-amber-500" /> API 下单 (临时号码)
        </label>
        <label class="flex items-center gap-2 text-slate-700 cursor-pointer font-semibold">
          <input v-model="form.mode" @change="sync" type="radio" value="fixed" class="accent-amber-500" /> 手动拉取 (固定号码)
        </label>
      </div>

      <div class="grid grid-cols-1 md:grid-cols-2 gap-3">
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">接码超时秒数</label>
          <input v-model.number="form.otp_timeout_seconds" @input="sync" type="number" min="30" step="1" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="180" />
        </div>
      </div>

      <!-- 临时号码模式 -->
      <div v-if="form.mode === 'temp'" class="space-y-3">
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">一浩 API Key</label>
          <input v-model="form.api_key" @input="sync" type="password" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="请输入一浩 API Token" />
        </div>
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">商品 ID (Goods ID)</label>
          <select v-if="testState.goods.length > 0" v-model="form.service" @change="sync" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800">
            <option v-for="g in testState.goods" :key="g.id" :value="g.id">
              [{{ g.id }}] {{ g.name }} - ${{ g.cost }} (国家: {{ g.country }})
            </option>
          </select>
          <input v-else v-model="form.service" @input="sync" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="如 12-1-7 (或测试Key并同步数据后选择)" />
        </div>
      </div>

      <!-- 固定号码模式 -->
      <div v-if="form.mode === 'fixed'" class="space-y-3">
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">固定号码 (Phone)</label>
          <input v-model="form.fixed_config.phone" @input="sync" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="填写分配的美国电话" />
        </div>
        <div class="space-y-2 border-t border-slate-100 pt-2">
          <div class="flex justify-between items-center">
            <label class="text-slate-500 font-semibold">短信提取 API 链接池</label>
            <button @click="addSmsUrl" class="text-[10px] text-amber-600 font-bold border border-amber-300 px-2 py-0.5 rounded bg-amber-50/50">+ 添加链接</button>
          </div>
          <div v-for="(_, i) in form.fixed_config.sms_urls" :key="i" class="flex gap-2 items-center">
            <input v-model="form.fixed_config.sms_urls[i]" @input="sync" type="text" class="flex-1 bg-slate-50 border border-slate-200 rounded px-3 py-1.5 text-xs text-slate-800" placeholder="http://..." />
            <button @click="removeSmsUrl(i)" class="text-rose-600 text-xs px-2 py-1 border border-rose-200 rounded bg-rose-50" :disabled="form.fixed_config.sms_urls.length <= 1">删除</button>
          </div>
          <div class="flex gap-4 items-center pt-1.5">
            <label class="text-[11px] text-slate-500 font-semibold">轮询策略:</label>
            <label class="flex items-center gap-1.5 text-xs text-slate-700 cursor-pointer"><input v-model="form.fixed_config.strategy" @change="sync" type="radio" value="fixed" class="accent-amber-500" /> 固定使用</label>
            <label class="flex items-center gap-1.5 text-xs text-slate-700 cursor-pointer"><input v-model="form.fixed_config.strategy" @change="sync" type="radio" value="random" class="accent-amber-500" /> 随机使用</label>
          </div>
        </div>
      </div>
    </div>
  </div>
</template>
