# Get-GPT 项目文件索引

> 干净版项目基线 · 2026-06-14

---

## 一、根目录

| 文件 | 说明 |
|------|------|
| [README.md](./README.md) | 项目主说明文档 |
| [INDEX.md](./INDEX.md) | 本文件索引 |
| [SUMMARY.md](./SUMMARY.md) | 技术架构与 API 规范总结 |
| [start.bat](./start.bat) | 生产模式一键启动（构建前端 + 启动后端） |
| [start-dev.bat](./start-dev.bat) | 开发模式一键启动（前后端热重载） |

---

## 二、XYAutoPro/ — 纯协议注册主体

### 入口

| 文件 | 说明 |
|------|------|
| [gui_launcher.py](./XYAutoPro/gui_launcher.py) | Slate-Light GUI 多进程控制台大屏 |
| [launcher.py](./XYAutoPro/launcher.py) | CLI 命令行多进程启动器 |

### core/ — 注册核心

| 文件 | 说明 |
|------|------|
| [register.py](./XYAutoPro/core/register.py) | 注册主协议（Sentinel → 取号 → Auth → 注册 → OTP → 创建 → Session） |
| [phone_db.py](./XYAutoPro/core/phone_db.py) | SQLite 数据库（accounts / operators / card / paypal_phone） |
| [run_context.py](./XYAutoPro/core/run_context.py) | RunContext：RunID + meta.json + http_trace + run.log 隔离 |
| [task_context.py](./XYAutoPro/core/task_context.py) | 旧 TaskContext（保留兼容） |

### sentinel/ — Sentinel Token 生成

| 文件 | 说明 |
|------|------|
| [sentinel_updater.py](./XYAutoPro/sentinel/sentinel_updater.py) | SDK 版本探测 + 动态下载 |
| [sentinel_token_gen.py](./XYAutoPro/sentinel/sentinel_token_gen.py) | Token 生成（QuickJS 适配） |
| [openai_sentinel_quickjs.js](./XYAutoPro/sentinel/openai_sentinel_quickjs.js) | QuickJS 执行环境适配脚本 |
| [sdk.js](./XYAutoPro/sentinel/sdk.js) | 本地缓存的 Sentinel SDK |
| [version.txt](./XYAutoPro/sentinel/version.txt) | SDK 缓存版本号 |

### sms/ — 接码平台

| 文件 | 说明 |
|------|------|
| [sms_manager.py](./XYAutoPro/sms/sms_manager.py) | DynamicSMSProvider（动态黑/白名单 + 价格重排 + 指定供应商） |
| [sms_config.json](./XYAutoPro/sms/sms_config.json) | 接码配置（**已脱敏：`YOUR_SMS_BOWER_API_KEY`**） |
| [paypal_config.json](./XYAutoPro/sms/paypal_config.json) | PayPal US 接码号码库（**已脱敏：`YOUR_PAYPAL_SMS_PROVIDER_URL`**） |

### tools/ — 运维工具

| 文件 | 说明 |
|------|------|
| [refresh_oauth.py](./XYAutoPro/tools/refresh_oauth.py) | 批量补取 / 刷新历史账号 OAuth |
| [gen_stripe_url.py](./XYAutoPro/tools/gen_stripe_url.py) | 为已注册账号生成 Stripe Hosted Plus 0 元长链接 |
| [fetch_cards.py](./XYAutoPro/tools/fetch_cards.py) | 随机信用卡采集工具（Visa + JCB） |
| [import_paypal_phones.py](./XYAutoPro/tools/import_paypal_phones.py) | US 接码号导入工具 |
| [pay_stripe_card.py](./XYAutoPro/tools/pay_stripe_card.py) | 纯协议 Stripe 支付（US 代理 + Card + PayPal OTP） |
| [do_paypal_pay.py](./XYAutoPro/tools/do_paypal_pay.py) | Playwright RPA 浏览器支付 |
| [pay_registered_paypal.py](./XYAutoPro/tools/pay_registered_paypal.py) | 已注册 PayPal 账号支付 |
| [real_card_bins.md](./XYAutoPro/tools/real_card_bins.md) | 真实卡 BIN 段速查 |

### 运行时目录（首次启动自动生成）

| 目录 | 说明 |
|------|------|
| `data/` | SQLite 数据库（`phone_records.db`）和 z_session 缓存 |
| `runs/` | 注册任务运行记录（每次注册一个 `run_YYYYMMDD_HHMMSS_xxxxxx/`） |
| `tmp/` | 临时测试脚本目录 |

---

## 三、sms_flow_project/ — FastAPI 后端

| 文件 | 说明 |
|------|------|
| [main.py](./sms_flow_project/main.py) | FastAPI 应用入口（配置 / 测试 / 同步 / 任务 API） |
| [register_flow.py](./sms_flow_project/register_flow.py) | 纯协议注册流（实时更新 DB） |
| [login_flow.py](./sms_flow_project/login_flow.py) | 纯协议登录流 |
| [oauth_flow.py](./sms_flow_project/oauth_flow.py) | Codex OAuth 授权（AsyncCamoufox 轻量浏览器） |
| [sms_manager.py](./sms_flow_project/sms_manager.py) | 接码 SDK（兼容传统 + 一浩临时 + 一浩固定号码） |
| [db_manager.py](./sms_flow_project/db_manager.py) | SQLite 管理器（含内存数据库测试优化） |
| [phone_register.py](./sms_flow_project/phone_register.py) | 注册命令行入口 |
| [phone_login.py](./sms_flow_project/phone_login.py) | 登录命令行入口 |
| [address_generator.py](./sms_flow_project/address_generator.py) | 美国姓名 / 地址 / 身份生成 |
| [test_project.py](./sms_flow_project/test_project.py) | 项目单元测试 |

### data/ — 静态数据

| 文件 | 说明 |
|------|------|
| [config.json](./sms_flow_project/data/config.json) | 主配置（**已脱敏：`YOUR_SESSION_ID` / `YOUR_APP_SECRET` / `YOUR_API_KEY`**） |
| [namesData.json](./sms_flow_project/data/namesData.json) | 英文姓名库 |
| [usData.json](./sms_flow_project/data/usData.json) | 美国州 / 城市 / 邮编 |
| [usRealAddresses.json](./sms_flow_project/data/usRealAddresses.json) | 美国真实地址样本 |

### static/ — 内嵌静态页

| 文件 | 说明 |
|------|------|
| [index.html](./sms_flow_project/static/index.html) | 后端内嵌的简易控制页 |
| [js/vue.global.prod.js](./sms_flow_project/static/js/vue.global.prod.js) | Vue 3 离线运行时 |

---

## 四、web/ — Vue3 前端管理后台

### 入口与配置

| 文件 | 说明 |
|------|------|
| [index.html](./web/index.html) | Vite SPA 入口 |
| [package.json](./web/package.json) | 依赖清单 |
| [vite.config.ts](./web/vite.config.ts) | Vite 构建配置 |
| [tsconfig.json](./web/tsconfig.json) | TypeScript 配置 |

### src/

| 文件 | 说明 |
|------|------|
| [App.vue](./web/src/App.vue) | 应用根组件 |
| [main.ts](./web/src/main.ts) | 应用入口 |
| [style.css](./web/src/style.css) | 全局样式 |

### src/pages/ — 业务页面

| 文件 | 说明 |
|------|------|
| [ConfigPage.vue](./web/src/pages/ConfigPage.vue) | 接码 / 代理 / 账号配置页 |
| [DataPage.vue](./web/src/pages/DataPage.vue) | 已注册账号数据看板 |
| [MonitorPage.vue](./web/src/pages/MonitorPage.vue) | 实时任务监控页 |

### src/components/

| 文件 | 说明 |
|------|------|
| [GptSmsConfig.vue](./web/src/components/GptSmsConfig.vue) | ChatGPT 接码配置组件 |
| [YihaoSmsConfig.vue](./web/src/components/YihaoSmsConfig.vue) | 一浩接码（固定号码/临时号码）配置组件 |
| [ToastNotify.vue](./web/src/components/ToastNotify.vue) | 消息提示组件 |

### src/utils/

| 文件 | 说明 |
|------|------|
| [search.ts](./web/src/utils/search.ts) | 下拉搜索过滤工具（GPT 接码商品 / 国家） |

### src/api/

| 文件 | 说明 |
|------|------|
| [client.ts](./web/src/api/client.ts) | 后端 API 调用封装 |

---

## 五、占位符索引

| 占位符 | 出现位置 | 含义 |
|--------|---------|------|
| `YOUR_SMS_BOWER_API_KEY` | XYAutoPro/sms/sms_config.json | SmsBower 接码平台 API Key |
| `YOUR_PAYPAL_SMS_PROVIDER_URL` | XYAutoPro/sms/paypal_config.json | PayPal US 接码服务商域名 |
| `YOUR_API_KEY` | 多处配置 | 通用 API Key 占位符 |
| `YOUR_SESSION_ID` | sms_flow_project/data/config.json | 代理 API Session ID |
| `YOUR_APP_SECRET` | sms_flow_project/data/config.json | 代理 API AppSecret |
| `YOUR_PROXY_API_HOST` | sms_flow_project/data/config.json / XYAutoPro/* | 代理服务商 API 域名 |
| `YOUR_PROXY_CRC` | XYAutoPro/* | 代理 API CRC 签名 |
| `YOUR_PROXY_KEY_NAME` | XYAutoPro/* | 代理 API KeyName |
| `YOUR_PROXY_GATEWAY_HOST` | sms_flow_project/test_project.py | 代理网关域名 |
| `YOUR_SMS_API_HOST` | sms_flow_project/main.py / sms_manager.py | 接码平台 API 域名（一浩） |
| `YOUR_PAYPAL_SMS_PROVIDER_URL` | XYAutoPro/sms/paypal_config.json | PayPal US 接码服务商域名 |
| `YOUR_SMS_PROVIDER_DOMAIN` | 多处 | 接码平台主域名 |
