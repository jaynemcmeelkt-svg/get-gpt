const BASE_HEADERS: Record<string, string> = { 'Content-Type': 'application/json' }

async function request<T = any>(url: string, options: RequestInit = {}): Promise<T> {
  const response = await fetch(url, { headers: BASE_HEADERS, ...options })
  if (!response.ok) {
    const errBody = await response.json().catch(() => ({}))
    throw new Error(errBody.detail || response.statusText || '请求错误')
  }
  return response.json()
}

export async function getConfig() {
  return request<Record<string, any>>('/api/config')
}

export async function saveConfig(config: Record<string, any>) {
  return request('/api/config', { method: 'POST', body: JSON.stringify(config) })
}

export async function testSmsKey(payload: { provider: string; api_key: string; service?: string }) {
  return request<{ ok: boolean; balance: string }>('/api/sms/test', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function syncSmsGoods(payload: { provider: string; api_key: string; service?: string }) {
  return request<{ ok: boolean; selected_service?: string; services?: Array<{ id: string; name: string }>; goods: any[] }>('/api/sms/sync', {
    method: 'POST',
    body: JSON.stringify(payload)
  })
}

export async function getRuns(limit = 50) {
  return request<any[]>(`/api/runs?limit=${limit}`)
}

export async function getRunStats() {
  return request<{ total: number; success: number; failed: number; success_rate: number; errors: Record<string, number> }>('/api/runs/stats')
}

export async function getRunLog(runId: string) {
  return request<any>(`/api/runs/${runId}/log`)
}

export async function getAccounts() {
  return request<any[]>('/api/accounts')
}

export async function runFlow(config: Record<string, any>) {
  return request<any>('/api/flow/run', { method: 'POST', body: JSON.stringify(config) })
}

export async function runOAuth(payload: { req: Record<string, any>; login_result: any }) {
  return request<any>('/api/flow/oauth', { method: 'POST', body: JSON.stringify(payload) })
}
