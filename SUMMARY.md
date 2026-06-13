# Get-GPT 技术总结文档

> 干净版项目基线 · 2026-06-14

---

## 目录

1. [项目定位](#1-项目定位)
2. [系统架构](#2-系统架构)
3. [核心技术原理](#3-核心技术原理)
4. [API 规范](#4-api-规范)
5. [代理方案](#5-代理方案)
6. [数据库设计](#6-数据库设计)
7. [配置说明](#7-配置说明)
8. [使用示例](#8-使用示例)
9. [常见问题](#9-常见问题)

---

## 1. 项目定位

通过 **OpenAI Auth API** 与 **ChatGPT 后台支付接口** 实现 ChatGPT 账号的全自动注册、登录、Codex OAuth 授权、Stripe Plus 0 元长链接生成以及支付闭环。

**核心三段流水线：**

```
[手机号注册] → [Codex OAuth] → [Stripe 0元长链接 + 支付]
```

---

## 2. 系统架构

```
┌──────────────────────────────────────────────────────────────────────┐
│                    Get-GPT 三段式架构                                 │
│                                                                       │
│   ┌─────────────┐    ┌──────────────────┐    ┌──────────────────┐   │
│   │  Web 前端   │───▶│  FastAPI 后端    │───▶│  XYAutoPro       │   │
│   │  Vue 3 SPA  │◀───│  (REST API)      │◀───│  (核心引擎)      │   │
│   └─────────────┘    └──────────────────┘    └──────────────────┘   │
│                                                       │              │
│                                                       ▼              │
│                                          ┌────────────────────────┐  │
│                                          │  SQLite (WAL多进程)    │  │
│                                          │  accounts / operators  │  │
│                                          │  card / paypal_phone   │  │
│                                          └────────────────────────┘  │
└──────────────────────────────────────────────────────────────────────┘
```

### 2.1 技术栈

| 层级 | 技术 |
|------|------|
| HTTP 客户端 | `curl_cffi`（TLS/JA3 指纹模拟 Chrome） |
| 异步运行时 | `httpx` + `asyncio`（接码 / OAuth） |
| Web 后端 | `FastAPI` + `Uvicorn` + `Pydantic` |
| 前端 | `Vue 3` + `Vite` + `TypeScript` |
| 数据库 | `SQLite`（WAL 模式 / 多进程安全） |
| 浏览器（仅 OAuth） | `AsyncCamoufox` / `Playwright`（轻量级） |
| Sentinel 执行 | `QuickJS`（Node.js 嵌入） |

---

## 3. 核心技术原理

### 3.1 注册流程（8 步）

```
Step 1: 提取 Sentinel Token (QuickJS)
Step 2: 建立 Auth Session (CSRF + signin + authorize)
Step 3: 注册 — POST /api/accounts/user/register
Step 4: 触发短信 — GET continue_url
Step 5: 等待验证码 (接码平台轮询)
Step 6: 验证 OTP — POST /api/accounts/phone-otp/validate
Step 7: 创建账户 — POST /api/accounts/create_account
Step 8: 获取 Session — 策略 A（callback 直出）/ B（完整 NextAuth 登录）
```

### 3.2 NextAuth CSRF/State 优化（关键升级）

旧方案的 `csrfToken: "true"` 导致 100% OAuthCallback 错误，必须依赖策略 B 重新登录，极易触发 Cloudflare 403 盾。

**当前方案：**

1. 前置 `GET /api/auth/csrf` 拉取真实 CSRF Token。
2. 用真实 CSRF 调用 `POST /api/auth/signin/openai`，NextAuth 正常写入 `__Secure-next-auth.state` Cookie。
3. 从响应中提取带真实 `state` 的 `target_url`。

**收益：**
- 策略 A 100% 成功 → 注册完成直接拿 Token，省 3~7 秒。
- 高风险 IP 在第一步即可触发熔断，**避免扣费损失**。

### 3.3 Sentinel Token 纯协议生成

```
SDK 版本探测 → 下载 sdk.js → QuickJS 执行 → SentinelSDK.token() → 加密 Token
```

| flow 参数 | 使用场景 |
|-----------|---------|
| `username_password_login` | 登录时密码验证 |
| `username_password_create` | 注册时用户注册 |
| `oauth_create_account` | 注册时账户创建 |

### 3.4 动态卡商风控调度

| 机制 | 触发条件 | 行为 |
|------|---------|------|
| 黑名单熔断 | 同卡商累计 ≥ 10 次 HTTP 400 风控阻断 | 比价时自动过滤该卡池 |
| 优质卡商优先 | 同卡商累计 ≥ 20 次成功 | 全局插队 + 同国优先选用 |

---

## 4. API 规范

### 4.1 注册核心 API

```http
POST https://auth.openai.com/api/accounts/user/register
Headers:
  openai-sentinel-token: {sentinel_token}
Body:
  {"password": "{password}", "username": "{phone}"}
```

### 4.2 OTP 验证

```http
GET  https://auth.openai.com/api/accounts/phone-otp/send
POST https://auth.openai.com/api/accounts/phone-otp/validate
Body: {"code": "{otp_code}"}
```

### 4.3 创建账户

```http
POST https://auth.openai.com/api/accounts/create_account
Headers:
  openai-sentinel-token: {sentinel_token}
  openai-sentinel-so-token: {sentinel_so_token}
Body:
  {"name": "{full_name}", "birthdate": "{YYYY-MM-DD}"}
```

### 4.4 Stripe Plus 0 元长链接

```http
POST https://chatgpt.com/backend-api/payments/checkout
Headers:
  Authorization: Bearer {accessToken}
  Content-Type: application/json
Body:
{
  "plan_name": "chatgptplusplan",
  "billing_details": {"country": "US", "currency": "USD"},
  "cancel_url": "https://chatgpt.com/#pricing",
  "promo_campaign": {
    "promo_campaign_id": "plus-1-month-free",
    "is_coupon_from_query_param": false
  },
  "checkout_ui_mode": "hosted"
}
```

**响应：**

```json
{
  "tag": "hosted_checkout_session",
  "checkout_session_id": "cs_live_a1...",
  "url": "https://pay.openai.com/c/pay/cs_live_a1...#fid..."
}
```

### 4.5 Stripe 二次校验

```http
GET https://api.stripe.com/v1/payment_pages/{cs_id}
→ amount_due 字段确认 0 元
```

---

## 5. 代理方案

### 5.1 优先级

| 优先级 | 代理来源 | 类型 | 出口国 |
|--------|---------|------|--------|
| 1 | 第三方代理 API | SOCKS5 机房 IP | JP / US |
| 2 | 第三方家宽代理 | HTTP 家宽 IP | JP（千叶） |
| 3 | 本地代理 | 自定义 | `127.0.0.1:7897` |

### 5.2 代理 API 模板（已脱敏）

```
GET https://YOUR_PROXY_API_HOST/api/ProxyLogic/Generate
    ?Num=1
    &Country=JP
    &session=YOUR_SESSION_ID
    &Server=as
    &Format=0
    &Crc=YOUR_PROXY_CRC
    &Pool=2
    &KeyName=YOUR_PROXY_KEY_NAME
    &GenType=socks5
    &AppSecret=YOUR_APP_SECRET
```

**Python 使用格式：**

```python
proxy = f"socks5h://{user}:{pass}@{host}:{port}"
```

### 5.3 双代理隔离（支付场景）

| 阶段 | 代理 | 原因 |
|------|------|------|
| 长链接生成 | JP 代理 | warm-up 对 chatgpt.com 友好 |
| 实际支付 | US 代理 | 账单 US 区，降低 Stripe/PayPal 风控 |

---

## 6. 数据库设计

### 6.1 accounts（账号资产表 / 成功才写入）

| 字段 | 类型 | 说明 |
|------|------|------|
| phone | TEXT | 带国家码 |
| password / name / birthdate | TEXT | 注册信息 |
| access_token / session_token / refresh_token | TEXT | 凭证 |
| token_status | TEXT | success / failed / expired / pending |
| oauth_status | TEXT | success / unknown |
| payment_status | TEXT | pending / active / failed |
| codex_status / codex_token | TEXT | Codex 平台凭证 |
| run_id | TEXT | 关联 runs 表 |

### 6.2 failed_operators（卡商风控审计）

| 字段 | 类型 | 说明 |
|------|------|------|
| service / country_id / operator_id | TEXT | 三元组定位 |
| error_code | TEXT | 注册报错代码 |
| created_at | TEXT | 写入时间 |

### 6.3 successful_operators（优质卡商）

字段同上（去掉 `error_code`）。

### 6.4 card（信用卡库）

| 字段 | 类型 | 说明 |
|------|------|------|
| card_type | TEXT | Visa / JCB |
| card_number | TEXT | UNIQUE |
| cvv / expires | TEXT | 安全码 / 有效期 |
| batch_id | INTEGER | 采集批次 |

### 6.5 paypal_phone（PayPal 接码号码池）

| 字段 | 类型 | 说明 |
|------|------|------|
| phone / sms_url | TEXT | 号码 + 接码 URL |
| status | TEXT | active / disabled |
| use_count | INTEGER | 累计使用次数 |
| last_otp / last_otp_status | TEXT | 最后验证码与状态 |

---

## 7. 配置说明

### 7.1 sms_flow_project/data/config.json 字段

| 字段 | 占位符 / 默认 | 说明 |
|------|--------------|------|
| `proxy_config.api_url` | `YOUR_SESSION_ID` / `YOUR_APP_SECRET` / `YOUR_PROXY_API_HOST` | 代理 API |
| `sms_config.gpt_sms.api_key` | `YOUR_API_KEY` | SmsBower / SMS-Activate Key |
| `sms_config.paypal_sms.fixed_config.sms_urls[]` | `YOUR_API_KEY` | PayPal 固定号码 URL |
| `gui_config.locale` | `en-US` | 浏览器区域 |
| `gui_config.timezone` | `America/New_York` | 浏览器时区 |

### 7.2 XYAutoPro/sms/sms_config.json 字段

| 字段 | 占位符 / 默认 | 说明 |
|------|--------------|------|
| `provider` | `smsbower` | 接码平台标识 |
| `api_key` | `YOUR_SMS_BOWER_API_KEY` | API Key |
| `service` | `dr` | OpenAI 服务码 |
| `min_price` / `max_price` | `0.001` / `0.05` | 价格区间过滤 |
| `phone_exception` | `35191,35196` | 黑名单号段前缀 |

### 7.3 XYAutoPro/sms/paypal_config.json 字段

```json
{
  "phones": [
    "+10000000001|http://YOUR_PAYPAL_SMS_PROVIDER_URL/api/get_sms?key=YOUR_API_KEY"
  ],
  "otp_timeout_seconds": 180,
  "otp_poll_interval_seconds": 3
}
```

---

## 8. 使用示例

### 8.1 启动 XYAutoPro GUI

```powershell
cd XYAutoPro
python gui_launcher.py
```

### 8.2 CLI 多进程注册

```powershell
python launcher.py --count 5 --interval 20
```

### 8.3 生成 Stripe 长链接

```powershell
python tools/gen_stripe_url.py                       # 默认 JP / US-USD / top1 账号
python tools/gen_stripe_url.py --phone +573113106370 # 指定账号
python tools/gen_stripe_url.py --strict-zero         # 非 0 元时 exit(4)
```

### 8.4 启动 FastAPI 后端 + 前端

```powershell
.\start-dev.bat
# → 后端: http://localhost:8000
# → 前端: http://localhost:5173
```

### 8.5 浏览器控制台快捷生成（不依赖代理）

打开 `https://chatgpt.com/` → F12 → Console：

```javascript
(async () => {
  const s = await (await fetch("/api/auth/session")).json();
  const r = await fetch("https://chatgpt.com/backend-api/payments/checkout", {
    method: "POST",
    headers: {
      "Authorization": `Bearer ${s.accessToken}`,
      "Content-Type": "application/json"
    },
    body: JSON.stringify({
      plan_name: "chatgptplusplan",
      billing_details: {country: "US", currency: "USD"},
      cancel_url: "https://chatgpt.com/#pricing",
      promo_campaign: {
        promo_campaign_id: "plus-1-month-free",
        is_coupon_from_query_param: false
      },
      checkout_ui_mode: "hosted"
    })
  });
  console.log((await r.json()).url);
})();
```

---

## 9. 常见问题

### 9.1 OAuthCallback 错误

**原因：** NextAuth state 不匹配（旧版用 `csrfToken: "true"`）。
**解决：** 使用 `GET /api/auth/csrf` 获取真实 token（当前版本已修复）。

### 9.2 SentinelSDK not loaded

**解决：** SDK 版本探测失败时重试 3 次；强制重新下载 `sdk.js`。

### 9.3 rate_limit_exceeded

**原因：** 同一手机号短时间内频繁请求。
**解决：** 切换号段 + 增加请求间隔。

### 9.4 Stripe 长链接非 0 元

**原因：** 账号已用过 `plus-1-month-free` 优惠。
**解决：** 仅对新注册 / 未试用账号使用此优惠码。

### 9.5 代理出口不匹配

**Stripe 风控规则：**
- JP 代理 → JP/JPY ✅
- JP 代理 → US/USD ❌
- US 代理 → US/USD ✅

机房 IP 比家宽 IP 更易被风控识别。

### 9.6 优惠码参数格式

**正确（对象）：**
```json
"promo_campaign": {
  "promo_campaign_id": "plus-1-month-free",
  "is_coupon_from_query_param": false
}
```

**错误（字符串）：** `"promo_campaign": "plus-1-month-free"` → `Discount code is not eligible`

---

## 10. 三种 checkout_ui_mode 对比

| 模式 | 返回链接 | 说明 |
|------|---------|------|
| `hosted` | `pay.openai.com/c/pay/{id}#...` | Stripe 托管支付页（推荐） |
| `custom` | `chatgpt.com/checkout/{entity}/{id}` | ChatGPT 站内支付页 |
| `redirect` | 同上 | 实际等价 custom |

---

## 11. 地区/币种映射

| 国家码 | 币种 | 备注 |
|--------|------|------|
| US | USD | 美国 |
| JP | JPY | 日本 |
| ID | IDR | 印尼（GoPay） |
| DE / FR / IE | EUR | 欧元区 |
| GB | GBP | 英国 |
| SG | SGD | 新加坡 |

---

> **免责声明：** 本工具仅用于技术研究与学习。AccessToken 等同于账号凭据，请勿在不可信环境中使用。生成的支付链接为一次性 / 限时有效。
