<script setup lang="ts">
import { computed, reactive, ref, watch } from 'vue'
import { testSmsKey, syncSmsGoods } from '../api/client'
import type { GptSmsConfig } from '../types/config'
import { filterSelectableOptions } from '../utils/search'

const props = defineProps<{
  modelValue: GptSmsConfig
}>()
const emit = defineEmits<{
  (e: 'update:modelValue', v: GptSmsConfig): void
  (e: 'toast', msg: string, type?: string): void
}>()

const form = reactive<GptSmsConfig>({ ...props.modelValue })

watch(() => props.modelValue, (v) => { Object.assign(form, v) }, { deep: true })

function sync() {
  emit('update:modelValue', { ...form })
}

const testing = ref(false)
const syncing = ref(false)
const serviceKeyword = ref('')
const countryKeyword = ref('')
const testState = reactive({
  ok: false,
  balance: '',
  goods: [] as any[],
  services: [] as Array<{ id: string; name: string }>,
  selected_service: ''
})

const filteredServices = computed(() => filterSelectableOptions(testState.services, serviceKeyword.value))
const filteredGoods = computed(() => filterSelectableOptions(testState.goods, countryKeyword.value))

function applyCountrySelection() {
  if (testState.goods.length === 0) return
  const current = testState.goods.find((g: any) => String(g.id) === String(form.country))
  const selected = current || testState.goods[0]
  form.country = String(selected.id)
  form.min_price = selected.min_price
  form.max_price = selected.max_price
  sync()
}

async function loadGoodsByService(service: string, toastMessage?: string) {
  const syncRes = await syncSmsGoods({ provider: form.provider, api_key: form.api_key, service })
  testState.services = syncRes.services || []
  testState.selected_service = syncRes.selected_service || service
  form.service = testState.selected_service || service
  testState.goods = syncRes.goods || []
  serviceKeyword.value = ''
  countryKeyword.value = ''

  if (testState.goods.length > 0) {
    applyCountrySelection()
    if (toastMessage) emit('toast', toastMessage)
  } else {
    emit('toast', '未拉取到可用国家/定价数据', 'error')
  }
}

async function doTest() {
  if (!form.api_key) { emit('toast', '请先填写 API 密钥！', 'error'); return }
  testing.value = true
  syncing.value = true
  try {
    const res = await testSmsKey({ provider: form.provider, api_key: form.api_key, service: form.service || 'dr' })
    testState.ok = true
    testState.balance = res.balance
    await loadGoodsByService(form.service || 'dr', '密钥验证成功，已拉取商品与国家下拉数据')
  } catch (e: any) {
    testState.ok = false
    testState.balance = ''
    testState.goods = []
    testState.services = []
    testState.selected_service = ''
    serviceKeyword.value = ''
    countryKeyword.value = ''
    emit('toast', `验证或拉取商品失败: ${e.message}`, 'error')
  } finally {
    testing.value = false
    syncing.value = false
  }
}

async function doSync() {
  if (!form.api_key) { emit('toast', '请先填写 API 密钥！', 'error'); return }
  syncing.value = true
  try {
    await loadGoodsByService(form.service || 'dr', '商品与定价数据同步成功！')
  } catch (e: any) {
    testState.goods = []
    emit('toast', `同步失败: ${e.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

async function onServiceChange() {
  sync()
  if (!testState.ok || !form.api_key) return
  syncing.value = true
  try {
    await loadGoodsByService(form.service || 'dr', '已切换商品并刷新国家定价')
  } catch (e: any) {
    testState.goods = []
    emit('toast', `切换商品失败: ${e.message}`, 'error')
  } finally {
    syncing.value = false
  }
}

function onCountryChange() {
  const matched = testState.goods.find((g: any) => String(g.id) === String(form.country))
  if (matched) {
    form.min_price = matched.min_price
    form.max_price = matched.max_price
    sync()
    emit('toast', `已选择国家: ${matched.name}, 价格区间 [${matched.min_price} - ${matched.max_price}]`)
  }
}
</script>

<template>
  <div class="bg-white border border-slate-200 rounded-xl p-5 shadow-sm space-y-4">
    <div class="flex justify-between items-center border-b border-slate-100 pb-1.5">
      <h3 class="text-sm font-bold text-slate-800">4. OpenAI GPT 接码设置</h3>
      <div class="flex items-center gap-2">
        <button @click="doTest" :disabled="testing" class="bg-slate-100 hover:bg-slate-200 border border-slate-300 text-amber-600 text-[10px] px-2.5 py-1 rounded transition font-bold">
          {{ testing ? '验证并拉取中...' : '测试 Key 并拉取商品' }}
        </button>
        <button v-if="testState.ok" @click="doSync" :disabled="syncing" class="bg-amber-500 hover:bg-amber-600 border border-amber-600 text-white text-[10px] px-2.5 py-1 rounded transition font-bold">
          {{ syncing ? '同步中...' : '重新同步数据' }}
        </button>
      </div>
    </div>

    <div class="text-xs space-y-3">
      <div v-if="testState.ok" class="p-2.5 bg-slate-50 border border-slate-200 rounded-lg shadow-inner">
        <div class="flex justify-between items-center">
          <span class="text-slate-500">测试结果:</span>
          <span class="text-emerald-600 font-bold">{{ testState.balance }}</span>
        </div>
      </div>

      <div class="grid grid-cols-2 gap-3">
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">服务商平台</label>
          <select v-model="form.provider" @change="sync" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800">
            <option value="smsbower">SmsBower</option>
            <option value="smsactivate">Sms-Activate</option>
            <option value="herosms">HeroSMS</option>
          </select>
        </div>
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">商品代码 (Service)</label>
          <div v-if="testState.services.length > 0" class="space-y-2">
            <input v-model="serviceKeyword" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="搜索商品代码或名称" />
            <select v-model="form.service" @change="onServiceChange" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800">
              <option v-for="service in filteredServices" :key="service.id" :value="service.id">
                [{{ service.id }}] {{ service.name }}
              </option>
            </select>
            <div class="text-[10px] text-slate-400">共 {{ testState.services.length }} 项，当前显示 {{ filteredServices.length }} 项</div>
          </div>
          <input v-else v-model="form.service" @input="sync" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="默认 dr，测试 Key 后切换为下拉" />
        </div>
      </div>

      <div>
        <label class="block text-slate-500 mb-1 font-semibold">接码 API 密钥</label>
        <input v-model="form.api_key" @input="sync" type="password" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 placeholder-slate-400 text-slate-800" placeholder="请输入接码平台 API 密钥" />
      </div>

      <div>
        <div class="flex justify-between items-center mb-1">
          <label class="text-slate-500 font-semibold">国家选择 (Country)</label>
          <span class="text-[9px] text-slate-400">(印尼为6)</span>
        </div>
        <div v-if="testState.goods.length > 0" class="space-y-2">
          <input v-model="countryKeyword" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="搜索国家编号或名称" />
          <select v-model="form.country" @change="onCountryChange" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800">
            <option v-for="g in filteredGoods" :key="g.id" :value="g.id">
              [{{ g.id }}] {{ g.name }} (最低: {{ g.min_price }} // 可用: {{ g.total_count }})
            </option>
          </select>
          <div class="text-[10px] text-slate-400">共 {{ testState.goods.length }} 项，当前显示 {{ filteredGoods.length }} 项</div>
        </div>
        <input v-else v-model="form.country" @input="sync" type="text" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="如 6 (印尼) 或测试 Key 后同步下拉选择" />
      </div>

      <div class="grid grid-cols-4 gap-3">
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">最低价格</label>
          <input v-model.number="form.min_price" @input="sync" type="number" step="0.1" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="不限" />
        </div>
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">最高价格</label>
          <input v-model.number="form.max_price" @input="sync" type="number" step="0.1" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="不限" />
        </div>
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">接码超时秒数</label>
          <input v-model.number="form.otp_timeout_seconds" @input="sync" type="number" min="30" step="1" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="180" />
        </div>
        <div>
          <label class="block text-slate-500 mb-1 font-semibold">超时换码次数</label>
          <input v-model.number="form.otp_retry_attempts" @input="sync" type="number" min="1" step="1" class="w-full bg-slate-50 border border-slate-200 rounded px-3 py-2 text-slate-800" placeholder="3" />
        </div>
      </div>
    </div>
  </div>
</template>
