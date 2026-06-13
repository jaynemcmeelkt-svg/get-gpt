# Get-GPT — ChatGPT 自动化注册与支付套件

> 干净版项目基线 · 已脱敏所有敏感凭据
> 最后更新：2026-06-14

---

## 项目简介

本项目是一套基于 **纯协议直连（HTTP）** 模式实现的 ChatGPT 自动化运营系统，包含三大子模块：

| 子项目 | 功能定位 | 技术栈 |
|--------|---------|--------|
| `XYAutoPro/` | 多进程并发的 ChatGPT 手机号注册主体（含 GUI + CLI + 运维工具） | Python 3.11 / curl_cffi / SQLite / Tkinter / Playwright |
| `sms_flow_project/` | FastAPI 后端服务（提供配置 / 接码 / 注册任务调度 / 状态查询 API） | FastAPI / Uvicorn / Pydantic / Httpx |
| `web/` | Vite + Vue3 + TypeScript 前端管理后台 | Vue 3 / Vite / TypeScript |

---

## 核心特性

1. **🚀 纯协议直连**：使用 `curl_cffi` 模拟 Chrome 浏览器 TLS/JA3 指纹，直接调用 OpenAI 认证接口，**零浏览器依赖**。
2. **🛡️ Sentinel 风控绕过**：内置 SDK 版本探测 + QuickJS 执行环境，纯协议生成 `openai-sentinel-token`。
3. **📡 NextAuth CSRF/State 流转**：前置真实 CSRF 获取 → 标准 State 写入 → 零 OAuthCallback 错误 → 注册完成直接拿 Token。
4. **🗄️ SQLite 多进程安全**：WAL 模式数据库，`accounts` / `failed_operators` / `successful_operators` / `card` / `paypal_phone` 五张核心表。
5. **🎨 高颜值 GUI 控制台**：Slate-Light 配色 + VS Code 风格日志分流 + 实时数据看板（RPH / CAC / 黑白名单）。
6. **💳 Stripe 长链接生成**：自动取号 → JP 代理 → warm-up → `/backend-api/payments/checkout` → 二次拉 Stripe `payment_pages` 校验 0 元。
7. **🔁 双通道支付**：`pay_stripe_card.py`（纯协议）+ `do_paypal_pay.py`（Playwright RPA），按场景择优。

---

## 快速开始

### 1. 环境依赖

```text
Python    >= 3.11
Node.js   >= 16 (Sentinel Token QuickJS 执行)
SQLite    >= 3.x
```

Python 主要依赖：

```bash
pip install curl_cffi httpx fastapi uvicorn pydantic python-dotenv playwright pytest
```

### 2. 配置脱敏占位符

启动前请将以下占位符替换为你的真实凭据：

| 配置文件 | 占位符字段 | 说明 |
|---------|-----------|------|
| [XYAutoPro/sms/sms_config.json](file:///z:/ChinCode/TINY/TraeCN/Get-gpt-clean/XYAutoPro/sms/sms_config.json) | `YOUR_SMS_BOWER_API_KEY` | SmsBower 接码平台 API Key |
| [XYAutoPro/sms/paypal_config.json](file:///z:/ChinCode/TINY/TraeCN/Get-gpt-clean/XYAutoPro/sms/paypal_config.json) | `YOUR_PAYPAL_SMS_PROVIDER_URL` / `YOUR_API_KEY` | PayPal US 接码 API URL 与 Key |
| [sms_flow_project/data/config.json](file:///z:/ChinCode/TINY/TraeCN/Get-gpt-clean/sms_flow_project/data/config.json) | `YOUR_PROXY_API_HOST` / `YOUR_SESSION_ID` / `YOUR_APP_SECRET` / `YOUR_PROXY_CRC` / `YOUR_PROXY_KEY_NAME` / `YOUR_API_KEY` / `YOUR_PAYPAL_SMS_PROVIDER_URL` | 代理 API + 接码 API Key |

### 3. 启动方式

#### 方案 A：XYAutoPro 单机多进程

```powershell
cd XYAutoPro
# 1. 启动 GUI 控制台进行自动化注册（推荐）
python gui_launcher.py

# 2. 或者使用 CLI 命令行进行多进程注册
python launcher.py --count 3 --interval 20

# 3. 注册成功后，运行 PayPal 自动支付流水线（自动生成长链并完成 PayPal 扣款）
python tools/do_paypal_pay.py

# 4. 或者针对已注册账号进行交互式 PayPal 支付
python tools/pay_registered_paypal.py
```

#### 方案 B：FastAPI + Web 前端

```powershell
# 一键启动（开发模式）
.\start-dev.bat
# 一键启动（生产模式）
.\start.bat
```

---

## 目录结构

```
Get-gpt-clean/
├── README.md                       # 本文档
├── INDEX.md                        # 文件索引
├── SUMMARY.md                      # 技术总结与 API 规范
├── start.bat / start-dev.bat       # 一键启动脚本（生产 / 开发）
│
├── XYAutoPro/                      # 子项目 1：纯协议注册主体
│   ├── gui_launcher.py             #   GUI 控制台
│   ├── launcher.py                 #   CLI 多进程启动器
│   ├── core/                       #   注册核心
│   ├── sentinel/                   #   Sentinel Token 纯协议生成
│   ├── sms/                        #   接码平台适配
│   ├── tools/                      #   运维工具（支付/卡 BIN/OAuth）
│   ├── data/                       #   SQLite 数据库目录（运行时生成）
│   ├── runs/                       #   注册任务运行记录（运行时生成）
│   └── tmp/                        #   临时测试脚本目录
│
├── sms_flow_project/               # 子项目 2：FastAPI 后端
│   ├── main.py                     #   FastAPI 应用入口
│   ├── register_flow.py            #   协议注册流
│   ├── login_flow.py               #   协议登录流
│   ├── oauth_flow.py               #   Codex OAuth 授权流
│   ├── sms_manager.py              #   接码 SDK
│   ├── db_manager.py               #   SQLite 管理
│   ├── address_generator.py        #   美国地址 / 身份生成
│   ├── data/config.json            #   主配置文件（已脱敏）
│   └── static/                     #   后端内嵌静态页
│
└── web/                            # 子项目 3：Vue3 前端管理后台
    ├── src/
    │   ├── pages/                  #   配置 / 数据 / 监控页
    │   ├── components/             #   接码配置组件
    │   ├── api/client.ts           #   后端 API 调用
    │   └── utils/search.ts         #   下拉搜索工具
    └── vite.config.ts
```

---

## 技术原理速览

```
┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐
│  代理调度    │───▶│ SentinelSDK Token │───▶│ /api/accounts/    │
│ (代理 JP)    │    │  (QuickJS 生成)   │    │ user/register     │
└──────────────┘    └──────────────────┘    └────────┬──────────┘
                                                      │
                                                      ▼
                                            ┌───────────────────┐
                                            │ phone-otp/validate│
                                            │  → create_account │
                                            │  → callback       │
                                            │  → /api/auth/     │
                                            │     session       │
                                            └────────┬──────────┘
                                                      ▼
                                            🎉 accessToken 入库
```

详细 API 规范、Payload 示例请参见 [SUMMARY.md](./SUMMARY.md)。

---

## 安全提示

⚠️ **请勿将真实 API Key、AccessToken 提交到任何公开仓库**：

- 所有 `*_config.json` 中的字段已被替换为 `YOUR_XXX` 占位符。
- 运行时生成的 `data/*.db`、`runs/run_*/` 含有敏感凭据，已通过 `.gitignore` 排除。
- 生成的 Stripe `pay.openai.com/c/pay/{id}` 长链接为一次性 / 限时有效。

---

## 许可与免责声明

本工具仅用于技术研究和学习。AccessToken 等同于账号凭据，请勿在不可信环境中使用。
