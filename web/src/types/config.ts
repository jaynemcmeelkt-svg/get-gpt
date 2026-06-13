export interface GptSmsConfig {
  provider: string
  api_key: string
  service: string
  country: string
  preferred_countries: string[]
  min_price: number | null
  max_price: number | null
  activation_id: string | null
  otp_timeout_seconds: number
  otp_retry_attempts: number
}

export interface FixedConfig {
  phone: string
  sms_url: string
  sms_urls: string[]
  strategy: string
}

export interface PaypalSmsConfig {
  provider: string
  mode: string
  api_key: string
  service: string
  otp_timeout_seconds: number
  fixed_config: FixedConfig
}

export interface ProxyConfig {
  mode: string
  api_url: string
  api_urls: string[]
  strategy: string
  static_proxy: string
}

export interface GuiConfig {
  login_headless: boolean
  oauth_headless: boolean
  auto_detect_geo: boolean
  locale: string
  timezone: string
}

export interface AccountConfig {
  email: string
  password: string
  phone: string
}

export interface SmsConfig {
  gpt_sms: GptSmsConfig
  paypal_sms: PaypalSmsConfig
}

export interface AppConfig {
  flow_type: string
  gui_config: GuiConfig
  proxy_config: ProxyConfig
  sms_config: SmsConfig
  account_config: AccountConfig
}

export interface SmsTestState {
  ok: boolean
  balance: string
  goods: any[]
  services?: Array<{ id: string; name: string }>
  selected_service?: string
}

export interface RunStats {
  total: number
  success: number
  failed: number
  success_rate: number
  errors: Record<string, number>
}
