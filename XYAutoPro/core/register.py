"""
ChatGPT 手机号注册 - 纯协议版 (XYAutoRs)
=========================================
基于 phone_register_auto.py 改写:
  - 移除 Playwright 依赖
  - Sentinel token 改为纯协议方式 (sdk.js + Node 子进程)
  - 自动探测/更新 sdk.js 版本

用法: python XYAutoRs.py
"""

import json, os, sys, time, uuid, secrets, random, re, asyncio, subprocess, tempfile, traceback
from urllib.parse import quote, urlencode
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

_PROJECT_ROOT = Path(__file__).parent.parent
if str(_PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT))
if str(_PROJECT_ROOT / "core") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "core"))
if str(_PROJECT_ROOT / "sms") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "sms"))
if str(_PROJECT_ROOT / "sentinel") not in sys.path:
    sys.path.insert(0, str(_PROJECT_ROOT / "sentinel"))

BITFUN_DIR = str(_PROJECT_ROOT / "bitfun")
if BITFUN_DIR not in sys.path:
    sys.path.insert(0, BITFUN_DIR)

from curl_cffi import requests as curl_requests
from run_context import RunContext
from phone_db import PhoneDB

WORKDIR = Path(__file__).parent
PID = os.getpid()
RESULT_FILE = WORKDIR / f"phone_register_result_{PID}.json"

AUTH_BASE = "https://auth.openai.com"
CHAT_BASE = "https://chatgpt.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/148.0.0.0 Safari/537.36"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"

PROXY_API_BASE = (
    "https://YOUR_PROXY_API_HOST/api/ProxyLogic/Generate"
    "?Num=1&Country=JP&Server=as&Format=0"
    "&Crc=YOUR_PROXY_CRC&Pool=1"
    "&KeyName=YOUR_PROXY_KEY_NAME&GenType=http"
    "&AppSecret=96d2b10ca34fc0fa5d71a43c25c97ca4"
)
PROXY_API_SESSION = random.randint(100000000, 999999999)
PROXY_API_URL = f"{PROXY_API_BASE}&session={PROXY_API_SESSION}"
PROXY_TARGET_COUNTRY = "JP"
SMS_FLOW_CONFIG = _PROJECT_ROOT / "sms" / "sms_config.json"
PROJECT_NAME = "chatgpt_register"
phone_db = PhoneDB()

SENTINEL_FRAME_URL = "https://sentinel.openai.com/backend-api/sentinel/frame.html"
SENTINEL_SDK_BASE = "https://sentinel.openai.com/sentinel"
SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
SENTINEL_REFERER = "https://sentinel.openai.com/backend-api/sentinel/frame.html"
SENTINEL_SEC_CH_UA = '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"'

SENTINEL_CACHE_DIR = _PROJECT_ROOT / "sentinel"
SENTINEL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
SENTINEL_LOCAL_VERSION_FILE = SENTINEL_CACHE_DIR / "version.txt"
SENTINEL_LOCAL_SDK_FILE = SENTINEL_CACHE_DIR / "sdk.js"
SENTINEL_QUICKJS_SCRIPT = SENTINEL_CACHE_DIR / "openai_sentinel_quickjs.js"

NODE_WRAPPER_JS = """
const fs = require('fs');
const timeoutMs = Number(process.env.OPENAI_SENTINEL_VM_TIMEOUT_MS || '15000');
const sdkFile = process.env.OPENAI_SENTINEL_SDK_FILE;
const scriptFile = process.env.OPENAI_SENTINEL_QUICKJS_SCRIPT;

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', async () => {
  try {
    const payload = JSON.parse(input || '{}');
    globalThis.__payload_json = JSON.stringify(payload);
    globalThis.__sdk_source = fs.readFileSync(sdkFile, 'utf8');
    globalThis.__vm_done = false;
    globalThis.__vm_output_json = '';
    globalThis.__vm_error = '';
    const script = fs.readFileSync(scriptFile, 'utf8');
    eval(script);

    const started = Date.now();
    while (!globalThis.__vm_done) {
      if ((Date.now() - started) > timeoutMs) {
        throw new Error('QuickJS script timeout');
      }
      await new Promise((resolve) => setTimeout(resolve, 1));
    }

    if (String(globalThis.__vm_error || '').trim()) {
      throw new Error(String(globalThis.__vm_error));
    }

    process.stdout.write(String(globalThis.__vm_output_json || ''));
  } catch (err) {
    const msg = err && err.stack ? String(err.stack) : String(err);
    process.stderr.write(msg);
    process.exit(1);
  }
});
""".strip()


_run_ctx = None

def log(msg):
    ts = time.strftime("%H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line, flush=True)
    if _run_ctx and hasattr(_run_ctx, 'log_to_file'):
        _run_ctx.log_to_file(msg)


def _fetch_proxy_from_api() -> str:
    import requests as _req
    r = _req.get(PROXY_API_URL, timeout=15)
    text = r.text.strip()
    parts = text.split(":")
    if len(parts) != 4:
        raise RuntimeError(f"代理API返回格式异常: {text}")
    host, port, user, pwd = parts
    proxy_url = f"http://{user}:{pwd}@{host}:{port}"
    return proxy_url


def _verify_proxy_country(proxy_url: str) -> tuple[bool, str, str]:
    s = curl_requests.Session(impersonate="chrome")
    try:
        r = s.get("https://api.ipify.org?format=json", proxy=proxy_url, timeout=15)
        proxy_ip = r.json().get("ip", "")
    except Exception:
        try:
            r = s.get("https://ifconfig.me/ip", proxy=proxy_url, timeout=15)
            proxy_ip = r.text.strip()
        except Exception:
            return False, "", ""
    if not proxy_ip:
        return False, "", ""
    try:
        r2 = s.get(f"http://ip-api.com/json/{proxy_ip}?lang=en", timeout=10)
        info = r2.json()
    except Exception:
        return True, "??", "验证跳过(ip-api不可达)"
    country = info.get("countryCode", "")
    isp = info.get("isp", "")
    city = info.get("city", "")
    is_residential = not any(dc in isp.lower() for dc in [
        "hosting", "cloud", "datacenter", "vpn", "vps", "server",
        "digitalocean", "aws", "azure", "gcp", "ovh", "hetzner",
        "vultr", "linode", "scaleway", "upcloud",
    ])
    return country == PROXY_TARGET_COUNTRY, country, f"{city}|{isp}|{'住宅' if is_residential else 'DC'}"


def _verify_proxy_target_reachable(proxy_url: str) -> tuple[bool, str]:
    targets = [
        ("chatgpt.com", "https://chatgpt.com/"),
        ("auth.openai.com", "https://auth.openai.com/"),
    ]
    for name, url in targets:
        try:
            s = curl_requests.Session(impersonate="chrome")
            s.get(url, proxy=proxy_url, timeout=10)
        except Exception as e:
            return False, f"{name}不可达: {type(e).__name__}"
    return True, "chatgpt.com+auth.openai.com均可达"


def acquire_proxy(max_retries=8) -> str:
    for i in range(1, max_retries + 1):
        try:
            log(f"[1/10] 获取JP住宅代理 ({i}/{max_retries}) ...")
            proxy_url = _fetch_proxy_from_api()
            log(f"  动作:请求代理API | 状态:返回 {proxy_url[:50]}...")
            is_ok, country, detail = _verify_proxy_country(proxy_url)
            if is_ok:
                log(f"  动作:验证代理国家 | 状态:OK {country} {detail}")
                reach_ok, reach_detail = _verify_proxy_target_reachable(proxy_url)
                if reach_ok:
                    log(f"  动作:验证目标可达 | 状态:OK {reach_detail}")
                    return proxy_url
                else:
                    log(f"  动作:验证目标可达 | 状态:FAIL {reach_detail}")
            else:
                log(f"  动作:验证代理国家 | 状态:FAIL {country}≠{PROXY_TARGET_COUNTRY}, {detail}")
        except Exception as e:
            log(f"  动作:获取代理 | 状态:FAIL {e}")
            time.sleep(2)
    raise RuntimeError(f"连续{max_retries}次获取代理失败或国家不符")


LOCAL_SMS_CONFIG = _PROJECT_ROOT / "sms" / "sms_config.json"


def load_register_sms_config():
    config_path = LOCAL_SMS_CONFIG if LOCAL_SMS_CONFIG.exists() else SMS_FLOW_CONFIG
    with open(config_path, "r", encoding="utf-8") as f:
        config_data = json.load(f)
    if config_path == LOCAL_SMS_CONFIG:
        cfg = dict(config_data)
    else:
        cfg = dict(config_data.get("sms_config", {}).get("gpt_sms", {}))
    whitelist = cfg.get("preferred_countries", [])
    max_price = cfg.get("max_price", "未设置")
    min_price = cfg.get("min_price", "未设置")
    whitelist_names = [f"{cid}({COUNTRY_MAP.get(str(cid), '?')})" for cid in whitelist]
    default_country = cfg.get("country", "")
    default_name = f"{default_country}({COUNTRY_MAP.get(str(default_country), '?')})"
    log(f"[SMS] 接码配置: 白名单={whitelist_names}, 默认={default_name}, 价格=[{min_price}, {max_price}]")
    return cfg


def _run_async(coro):
    return asyncio.run(coro)


def acquire_phone_bundle(sms_provider):
    activation_id, phone = _run_async(sms_provider.get_number())
    normalized_phone = phone if str(phone).startswith("+") else f"+{phone}"
    return {"activation_id": activation_id, "phone": normalized_phone}


def wait_for_sms_code(sms_provider, activation_id, timeout=60):
    return _run_async(sms_provider.get_otp(activation_id, timeout=timeout))


RETRYABLE_ERRORS = [
    "account_creation_failed",
    "invalid_request_error",
    "register_failed",
    "authorize/continue",
    "just a moment",
    "cloudflare",
    "challenge",
    "http 403",
    "403 forbidden",
    "rate_limit",
    "too many requests",
]


def _is_retryable_error(error) -> bool:
    msg = str(error or "").lower()
    return any(marker in msg for marker in RETRYABLE_ERRORS)


class RetryableRegistrationError(Exception):
    pass


def _check_phone_db(phone: str) -> tuple[bool, str]:
    if phone_db.is_phone_used(phone):
        return True, "used"
    is_bl, reason = phone_db.is_blacklisted(phone)
    if is_bl:
        return True, f"blacklisted({reason})"
    return False, ""


def _get_sms_provider_name(sms_provider) -> str:
    return getattr(sms_provider, "provider_name", "") or getattr(sms_provider, "__class__", object).__name__


def _get_sms_cost(sms_provider) -> float:
    return float(getattr(sms_provider, "last_cost", 0) or 0)


COUNTRY_MAP = {
    "117": "Portugal", "1001": "Japan", "22": "India", "6": "Indonesia",
    "4": "Philippines", "1": "Ukraine", "2": "Kazakhstan", "33": "Colombia",
    "36": "Colombia", "44": "UK", "10": "Russia", "0": "Russia",
    "32": "Canada", "203": "Czech", "16": "Germany", "23": "UK",
    "34": "Spain", "15": "France", "7": "Malaysia", "57": "Colombia",
    "45": "Australia", "37": "NewZealand",
}

PHONE_PREFIX_MAP = {
    "+351": "PT", "+81": "JP", "+91": "IN", "+62": "ID",
    "+63": "PH", "+380": "UA", "+7": "KZ/RU", "+86": "CN",
    "+1": "US/CA", "+44": "UK", "+40": "RO", "+57": "CO",
    "+55": "BR", "+34": "ES", "+49": "DE", "+33": "FR",
    "+61": "AU", "+60": "MY", "+420": "CZ",
}


def _get_phone_region(phone: str) -> str:
    for prefix, country in sorted(PHONE_PREFIX_MAP.items(), key=lambda x: -len(x[0])):
        if phone.startswith(prefix):
            return country
    return "unknown"


def establish_auth_session(phone: str, proxy: str = ""):
    log("[7/10] 建立 Auth session ...")
    session = curl_requests.Session()
    base_headers = {"User-Agent": UA, "Accept": "application/json"}
    did = str(uuid.uuid4())

    log("  动作:Prime (/create-account) ...")
    _request(session, "GET", f"{AUTH_BASE}/create-account",
        headers={**base_headers, "Accept": "text/html,application/xhtml+xml"},
        max_retries=2, proxy=proxy)

    # 1. 获取真实 CSRF Token
    log("  动作:获取CSRF Token ...")
    r_csrf = _request(session, "GET", f"{CHAT_BASE}/api/auth/csrf",
        headers={**base_headers, "Referer": f"{CHAT_BASE}/"},
        max_retries=2, proxy=proxy)
    
    try:
        csrf_token = r_csrf.json().get("csrfToken", "")
    except Exception:
        csrf_token = ""
        
    if not csrf_token:
        raise RuntimeError("无法获取 CSRF token，登录会话建立失败")
    log("  动作:获取CSRF Token | 状态:OK")

    session_logging_id = str(uuid.uuid4()).replace("-", "")
    signin_url = (
        f"{CHAT_BASE}/api/auth/signin/openai"
        f"?prompt=login&ext-oai-did={did}"
        f"&auth_session_logging_id={session_logging_id}"
        f"&screen_hint=login_or_signup"
        f"&login_hint={quote(phone, safe='')}"
    )
    
    log("  动作:Signin (/api/auth/signin) ...")
    # 使用真实的 csrfToken 以建立官方标准的 __Secure-next-auth.state 授权 Cookie
    r_signin = _request(session, "POST", signin_url,
        data=urlencode({"csrfToken": csrf_token, "callbackUrl": "https://chatgpt.com/"}),
        headers={**base_headers, "Content-Type": "application/x-www-form-urlencoded",
                 "Origin": CHAT_BASE, "Referer": f"{CHAT_BASE}/"},
        max_retries=2, proxy=proxy)

    try:
        signin_resp = r_signin.json()
        target_url = signin_resp.get("url", "") or signin_resp.get("continue_url", "")
    except Exception:
        target_url = ""

    if not target_url:
        if r_signin.status_code == 403:
            raise RuntimeError("Signin 接口返回 403 (当前住宅代理 IP 疑似触发了 Cloudflare Turnstile 验证盾)")
        raise RuntimeError(f"Signin 未返回跳转链接 (HTTP {r_signin.status_code})")

    scope = "openid email profile offline_access model.request model.read organization.read organization.write"
    auth_session_url = target_url

    log("  动作:Authorize (/api/accounts/authorize) ...")
    r = _request(session, "GET", auth_session_url,
        headers={**base_headers, "Accept": "text/html,application/xhtml+xml",
                 "Origin": AUTH_BASE, "Referer": f"{CHAT_BASE}/"}, proxy=proxy)

    redirect_path = r.url.split("auth.openai.com")[-1]
    log(f"  动作:检查重定向 | 状态:OK path={redirect_path}")

    return {
        "session": session,
        "did": did,
        "redirect_path": redirect_path,
        "base_headers": base_headers,
        "scope": scope,
    }


def retry_registration_with_new_gpt_phone(register_func, sms_provider, max_attempts=10, otp_timeout=60, proxy: str = ""):
    last_error = None
    db_skip_count = 0
    effective_max = max_attempts + 100

    log("[5/10] 提取 Sentinel token (纯协议) ...")
    sentinel_data = extract_sentinel(proxy=proxy)
    log(f"  动作:Sentinel token | 状态:OK")

    for attempt in range(1, effective_max + 1):
        if (_PROJECT_ROOT / "stop.flag").exists():
            log("🛑 准备获取号码前检测到优雅停止信号 (stop.flag)，取消获取新号并退出重试。")
            raise KeyboardInterrupt("优雅停止信号已下发")

        bundle = acquire_phone_bundle(sms_provider)
        phone = bundle["phone"]
        activation_id = bundle["activation_id"]

        sms_provider_name = _get_sms_provider_name(sms_provider)
        sms_cost = _get_sms_cost(sms_provider)
        phone_region = _get_phone_region(phone)

        is_skip, skip_reason = _check_phone_db(phone)
        if is_skip:
            db_skip_count += 1
            log(f"[6/10] 号码 {phone} 跳过: {skip_reason}")
            phone_db.add_record(
                phone=phone, sms_provider=sms_provider_name,
                sms_cost=sms_cost, phone_region=phone_region,
                project=PROJECT_NAME, status=skip_reason,
            )
            try:
                _run_async(sms_provider.cancel_activation(activation_id))
            except Exception:
                pass
            if db_skip_count >= 50:
                log("[DB] 连续跳过 50 个号码，停止重试")
                raise RuntimeError("连续跳过号码过多，可能号码池耗尽或黑名单过宽")
            continue

        db_skip_count = 0
        real_attempt = attempt - db_skip_count
        log(f"[6/10] 获取号码({attempt}次拿号): {phone} | 地区={phone_region}")

        auth_ctx = establish_auth_session(phone, proxy=proxy)

        record_id = phone_db.add_record(
            phone=phone, sms_provider=sms_provider_name,
            sms_cost=sms_cost, phone_region=phone_region,
            project=PROJECT_NAME, status="pending",
        )

        try:
            result = register_func(
                phone,
                sms_provider=sms_provider,
                activation_id=activation_id,
                otp_timeout=otp_timeout,
                proxy=proxy,
                sentinel_data=sentinel_data,
                auth_ctx=auth_ctx,
            )
            if result and result.get("success"):
                phone_db.update_status(record_id, status="success")
                return result
            if result is None:
                phone_db.update_status(record_id, status="failed")
                raise RetryableRegistrationError("注册返回 None (可能被风控或号码无效)")
            if result.get("error") == "already_registered":
                phone_db.update_status(record_id, status="already_registered")
                log(f"[AUTO] 号码已注册，换号重试")
            else:
                err_detail = result.get("error", "") + ": " + str(result.get("detail", ""))
                phone_db.update_status(record_id, status="failed")
                raise RetryableRegistrationError(f"注册失败: {err_detail}")
        except TimeoutError as e:
            last_error = e
            phone_db.update_status(record_id, status="timeout")
            log(f"[9/10] 第{attempt}次验证码超时，换号重试")
            try:
                _run_async(sms_provider.cancel_activation(activation_id))
            except Exception as cancel_error:
                log(f"[AUTO] 取消接码任务失败: {cancel_error}")
            if (_PROJECT_ROOT / "stop.flag").exists():
                log("🛑 验证码超时且检测到优雅停止信号 (stop.flag)，已成功释放号码，停止继续重试并退出。")
                raise KeyboardInterrupt("优雅停止信号已下发")
            if attempt >= max_attempts:
                raise TimeoutError(f"等待验证码超时，已换号重试 {max_attempts} 次仍失败") from e
        except RetryableRegistrationError as e:
            last_error = e
            phone_db.update_status(record_id, status="failed")
            log(f"[8/10] 第{attempt}次注册失败，换号重试: {e}")
            try:
                _run_async(sms_provider.cancel_activation(activation_id))
            except Exception:
                pass
            if (_PROJECT_ROOT / "stop.flag").exists():
                log("🛑 注册失败且检测到优雅停止信号 (stop.flag)，已成功释放号码，停止继续重试并退出。")
                raise KeyboardInterrupt("优雅停止信号已下发")
            if attempt >= max_attempts:
                raise RuntimeError(f"注册连续失败，已换号重试 {max_attempts} 次仍失败: {e}") from e
        except Exception as e:
            last_error = e
            if _is_retryable_error(e):
                phone_db.update_status(record_id, status="failed")
                log(f"[AUTO] 第 {attempt} 次遇到可重试错误，换号重试: {e}")
                try:
                    _run_async(sms_provider.cancel_activation(activation_id))
                except Exception:
                    pass
                if (_PROJECT_ROOT / "stop.flag").exists():
                    log("🛑 遇到可重试错误且检测到优雅停止信号 (stop.flag)，已成功释放号码，停止继续重试并退出。")
                    raise KeyboardInterrupt("优雅停止信号已下发")
                if attempt >= max_attempts:
                    raise RuntimeError(f"连续触发可重试错误，已换号 {max_attempts} 次: {e}") from e
            else:
                phone_db.update_status(record_id, status="error")
                raise

        if attempt >= max_attempts:
            break

    if last_error:
        raise last_error
    raise RuntimeError("自动注册未执行")


def _get_proxy_kwargs(proxy: str = "") -> dict:
    if not proxy:
        return {}
    return {"proxies": {"http": proxy, "https": proxy}}


def _request(session, method, url, ctx=None, step_name=None, max_retries=3, proxy: str = "", **kwargs):
    kwargs.setdefault("impersonate", "chrome")
    kwargs.setdefault("timeout", 45)
    if proxy and "proxies" not in kwargs and "proxy" not in kwargs:
        kwargs.update(_get_proxy_kwargs(proxy))

    start_ts = time.time()
    for attempt in range(max_retries):
        try:
            r = session.request(method, url, **kwargs)
            break
        except Exception as e:
            elapsed_ms = (time.time() - start_ts) * 1000
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 5
                log(f"  请求失败，{wait}s 后重试 ({attempt+1}/{max_retries}): {e}")
                if ctx and hasattr(ctx, 'log_http_trace'):
                    ctx.log_http_trace(method, url, 0, elapsed_ms, step_name=step_name or "", error=str(e)[:200])
                time.sleep(wait)
            else:
                log(f"  ❌ 请求最终失败: {e}")
                if ctx and hasattr(ctx, 'log_http_trace'):
                    ctx.log_http_trace(method, url, 0, elapsed_ms, step_name=step_name or "", error=str(e)[:200])
                raise

    elapsed_ms = (time.time() - start_ts) * 1000

    if ctx and step_name:
        req_info = {"method": method, "url": url}
        if "headers" in kwargs:
            req_info["headers"] = dict(kwargs["headers"])
        if "json" in kwargs:
            req_info["body_json"] = kwargs["json"]
        if "data" in kwargs:
            req_info["body_form"] = kwargs["data"] if isinstance(kwargs["data"], dict) else str(kwargs["data"])
        if "allow_redirects" in kwargs:
            req_info["allow_redirects"] = kwargs["allow_redirects"]

        resp_info = {"status_code": r.status_code, "final_url": str(r.url)}
        try:
            resp_info["body_json"] = r.json()
        except Exception:
            resp_info["body_text"] = r.text[:5000] if r.text else ""

        ctx.log_step(step_name, req_info, resp_info, r.status_code)
        ctx.save_cookies(step_name, session)

    if ctx and hasattr(ctx, 'log_http_trace'):
        ctx.log_http_trace(method, url, r.status_code, elapsed_ms, step_name=step_name or "")

    return r


# ==========================================
# Sentinel token 过期检测 + 自动重获取
# ==========================================

def _is_sentinel_expired(r) -> bool:
    """检测响应是否为 sentinel token 过期"""
    if r.status_code not in (403, 409):
        return False
    try:
        data = r.json()
        err = ""
        if isinstance(data, dict):
            err = (data.get("error") or "")
            if isinstance(err, dict):
                err = err.get("message", "")
            err = str(err).lower()
        elif isinstance(data, str):
            err = data.lower()
    except Exception:
        err = (r.text or "").lower()[:500]
    expired_keywords = ["invalid_token", "expired_token", "token_expired", "sentinel", "challenge", "captcha"]
    return any(kw in err for kw in expired_keywords)


def _request_with_sentinel_retry(session, method, url, sentinel_data: dict, proxy: str = "",
                                  ctx=None, step_name=None, max_retries=3, **kwargs):
    """带 sentinel token 过期自动重获取的请求封装。
    
    当响应为 403/409 且包含 token 过期关键词时，重新获取 sentinel token 并重试。
    sentinel_data 是可变 dict，重获取后会原地更新。
    """
    for attempt in range(2):
        r = _request(session, method, url, ctx=ctx, step_name=step_name,
                     max_retries=max_retries, proxy=proxy, **kwargs)
        if not _is_sentinel_expired(r):
            return r
        
        log(f"  ⚠️ Sentinel token 过期 (HTTP {r.status_code})，重新获取... (重试{attempt+1}/2)")
        try:
            new_sentinel = extract_sentinel(ctx=ctx, proxy=proxy)
            sentinel_data["sentinel_token"] = new_sentinel["sentinel_token"]
            sentinel_data["sentinel_so_token"] = new_sentinel.get("sentinel_so_token", "")
            sentinel_data["oai_did"] = new_sentinel.get("oai_did", "")
            log(f"  ✅ Sentinel token 重新获取成功，重试请求...")
        except Exception as e:
            log(f"  ❌ Sentinel token 重新获取失败: {e}")
            return r
        
        headers = kwargs.get("headers", {})
        if "openai-sentinel-token" in headers:
            headers["openai-sentinel-token"] = sentinel_data["sentinel_token"]
        if "openai-sentinel-so-token" in headers:
            headers["openai-sentinel-so-token"] = sentinel_data.get("sentinel_so_token", "")
        kwargs["headers"] = headers
    
    return r

# ==========================================
# Sentinel token - 纯协议方式 (无需 Playwright)
# ==========================================

def _ensure_quickjs_script():
    if SENTINEL_QUICKJS_SCRIPT.exists() and SENTINEL_QUICKJS_SCRIPT.stat().st_size > 0:
        return
    ctf_reg_dir = _PROJECT_ROOT / "Gpt-Agreement-Payment-main" / "CTF-reg"
    src = ctf_reg_dir / "sentinel" / "openai_sentinel_quickjs.js"
    if not src.exists():
        src = ctf_reg_dir / "openai_sentinel_quickjs.js"
    if src.exists():
        import shutil
        shutil.copy2(str(src), str(SENTINEL_QUICKJS_SCRIPT))
        log(f"  复制 quickjs 适配脚本: {src.name}")
    else:
        raise RuntimeError(f"找不到 openai_sentinel_quickjs.js, 请手动复制到 {SENTINEL_QUICKJS_SCRIPT}")


def _update_sentinel_sdk(session, proxy: str = ""):
    log("  探测 Sentinel SDK 版本...")
    kwargs = {"timeout": 30, "impersonate": "chrome"}
    kwargs.update(_get_proxy_kwargs(proxy))
    resp = session.get(SENTINEL_FRAME_URL, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"获取 frame.html 失败: HTTP {resp.status_code}")

    frame_html = resp.text
    version = None
    for pattern in [r'/sentinel/([a-zA-Z0-9]+)/sdk\.js', r'sentinel/([a-zA-Z0-9]+)/sdk\.js']:
        matches = re.findall(pattern, frame_html)
        if matches:
            version = matches[0]
            break
    if not version:
        raise RuntimeError("无法从 frame.html 提取 Sentinel 版本号")

    local_version = ""
    if SENTINEL_LOCAL_VERSION_FILE.exists():
        local_version = SENTINEL_LOCAL_VERSION_FILE.read_text(encoding="utf-8").strip()

    if version == local_version and SENTINEL_LOCAL_SDK_FILE.exists() and SENTINEL_LOCAL_SDK_FILE.stat().st_size > 0:
        log(f"  Sentinel SDK 版本一致: {version}, 跳过下载")
        return version

    sdk_url = f"{SENTINEL_SDK_BASE}/{version}/sdk.js"
    log(f"  下载 sdk.js: {sdk_url}")
    kwargs2 = {
        "timeout": 60, "impersonate": "chrome",
        "headers": {"Accept": "*/*", "Referer": SENTINEL_FRAME_URL},
    }
    kwargs2.update(_get_proxy_kwargs(proxy))
    resp = session.get(sdk_url, **kwargs2)
    if resp.status_code != 200:
        raise RuntimeError(f"下载 sdk.js 失败: HTTP {resp.status_code}")
    sdk_content = resp.content
    if not sdk_content:
        raise RuntimeError("下载 sdk.js 失败: 响应为空")

    if local_version and SENTINEL_LOCAL_SDK_FILE.exists():
        bak = SENTINEL_CACHE_DIR / f"sdk_{local_version}.js.bak"
        try:
            SENTINEL_LOCAL_SDK_FILE.rename(bak)
        except Exception:
            pass

    SENTINEL_LOCAL_SDK_FILE.write_bytes(sdk_content)
    SENTINEL_LOCAL_VERSION_FILE.write_text(version, encoding="utf-8")
    log(f"  Sentinel SDK 已更新: {version} ({len(sdk_content)} bytes)")
    return version


def _run_node_action(action, payload, timeout_ms=30000):
    body = dict(payload)
    body["action"] = action
    node_path = os.getenv("OPENAI_SENTINEL_NODE_PATH", "node").strip() or "node"
    proc = subprocess.run(
        [node_path, "-e", NODE_WRAPPER_JS],
        input=json.dumps(body, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=max(15, int(timeout_ms / 1000) + 10),
        env={
            **os.environ,
            "OPENAI_SENTINEL_SDK_FILE": str(SENTINEL_LOCAL_SDK_FILE),
            "OPENAI_SENTINEL_QUICKJS_SCRIPT": str(SENTINEL_QUICKJS_SCRIPT),
            "OPENAI_SENTINEL_VM_TIMEOUT_MS": str(min(timeout_ms, 30000)),
        },
    )
    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "unknown").strip()[:500]
        raise RuntimeError(f"Node 执行失败 ({action}): {err_msg}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"Node 返回空 ({action})")
    return json.loads(out)


def _fetch_challenge(session, device_id, flow, request_p, proxy: str = ""):
    body = {"p": request_p, "id": device_id, "flow": flow}
    kwargs = {
        "data": json.dumps(body, separators=(",", ":")),
        "headers": {
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": SENTINEL_REFERER,
            "Origin": "https://sentinel.openai.com",
            "User-Agent": UA,
            "sec-ch-ua": SENTINEL_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        },
        "timeout": 20,
        "impersonate": "chrome",
    }
    kwargs.update(_get_proxy_kwargs(proxy))
    resp = session.post(SENTINEL_REQ_URL, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"/sentinel/req 失败: HTTP {resp.status_code}")
    return resp.json()


def _build_token_for_flow(session, device_id, flow, proxy: str = ""):
    requirements = _run_node_action("requirements", {"device_id": device_id})
    request_p = str(requirements.get("request_p") or "").strip()
    if not request_p:
        raise RuntimeError(f"requirements 未返回 request_p")

    challenge = _fetch_challenge(session, device_id, flow, request_p, proxy)
    c_value = str(challenge.get("token") or "").strip()
    if not c_value:
        raise RuntimeError("challenge 缺少 token 字段")

    solved = _run_node_action("solve", {
        "device_id": device_id,
        "request_p": request_p,
        "challenge": challenge,
    })
    final_p = str(solved.get("final_p") or solved.get("p") or "").strip()
    t_value = str(solved.get("t") or "").strip() if solved.get("t") is not None else ""
    if not final_p:
        raise RuntimeError("solve 未返回 final_p")

    token_payload = {"p": final_p, "t": t_value, "c": c_value, "id": device_id, "flow": flow}
    return json.dumps(token_payload, separators=(",", ":"), ensure_ascii=False)


def extract_sentinel(ctx=None, proxy: str = ""):
    log("[5/10] 提取 Sentinel token (纯协议) ...")
    _ensure_quickjs_script()
    log("  动作:准备quickjs适配脚本 | 状态:OK")

    sentinel_session = curl_requests.Session()
    sentinel_session.headers.update({"User-Agent": UA, "Accept": "*/*"})

    version = _update_sentinel_sdk(sentinel_session, proxy)
    log(f"  动作:更新sdk.js | 状态:OK version={version}")

    device_id = str(uuid.uuid4())

    log("  动作:生成token(flow=username_password_create) ...")
    sentinel_token = _build_token_for_flow(sentinel_session, device_id, "username_password_create", proxy)
    log(f"  状态:OK len={len(sentinel_token)}")

    log("  动作:生成token(flow=oauth_create_account) ...")
    sentinel_so_token = _build_token_for_flow(sentinel_session, device_id, "oauth_create_account", proxy)
    log(f"  状态:OK len={len(sentinel_so_token)}")

    if ctx:
        ctx.log_step("01_sentinel", {
            "method": "protocol",
            "version": version,
            "device_id": device_id,
        }, {
            "sentinel_token_len": len(sentinel_token),
            "sentinel_so_token_len": len(sentinel_so_token),
            "oai_did": device_id,
        })
        ctx.save_token("sentinel_token", sentinel_token)
        ctx.save_token("sentinel_so_token", sentinel_so_token)
        ctx.save_token("oai_did", device_id)

    return {
        "sentinel_token": sentinel_token,
        "sentinel_so_token": sentinel_so_token,
        "cookie_str": "",
        "oai_did": device_id,
    }


# ==========================================
# Core Registration Flow
# ==========================================
def run_registration(phone, sms_provider=None, activation_id=None, otp_timeout=60, proxy: str = "", sentinel_data: dict = None, auth_ctx: dict = None):
    if not phone.startswith("+"):
        phone = "+" + phone

    sms_provider_name = _get_sms_provider_name(sms_provider) if sms_provider else ""
    sms_cost_val = _get_sms_cost(sms_provider) if sms_provider else 0
    phone_region = _get_phone_region(phone)

    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    password = "".join(random.choices(chars, k=9)) + "!A1"
    first_names = ["James", "John", "Robert", "Michael", "David", "William", "Mary", "Linda", "Jennifer", "Barbara"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Anderson"]
    full_name = f"{random.choice(first_names)} {random.choice(last_names)}"
    birthdate = f"{random.randint(1985, 2004)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"

    log(f"手机号: {phone} | 地区: {phone_region} | 接码: {sms_provider_name}")
    log(f"密码:   {password}")
    log(f"姓名:   {full_name}  生日: {birthdate}")

    global _run_ctx
    ctx = RunContext(flow_type="register", identifier=phone)
    _run_ctx = ctx
    ctx.save_config(identity={"phone": phone, "password": password, "name": full_name, "birthdate": birthdate})
    ctx.add_attempt(phone)

    if not auth_ctx:
        raise RuntimeError("auth_ctx 未提供，请先调用 establish_auth_session()")

    _reg_success = False
    try:
        session = auth_ctx["session"]
        base_headers = auth_ctx["base_headers"]
        did = auth_ctx["did"]
        scope = auth_ctx["scope"]
        redirect_path = auth_ctx["redirect_path"]

        if not sentinel_data:
            sentinel_data = extract_sentinel(ctx=ctx, proxy=proxy)

        # ============================================================
        # Step 2: Check redirect from Auth session
        # ============================================================
        log("[7/10] 检查 Auth session 状态 ...")
        log(f"  动作:检查重定向 | 状态:OK path={redirect_path}")

        if "log-in/password" in redirect_path:
            log(f"  动作:检查号码 | 状态:FAIL 已注册")
            ctx.save_result({"success": False, "phone": phone, "error": "already_registered"})
            ctx.save_account({"status": "already_registered"})
            return {"success": False, "phone": phone, "error": "already_registered"}

        # ============================================================
        # Step 3: Register
        # ============================================================
        log("[8/10] 注册 (提交手机号+密码) ...")
        r = _request_with_sentinel_retry(session, "POST", f"{AUTH_BASE}/api/accounts/user/register",
            sentinel_data=sentinel_data, proxy=proxy,
            ctx=ctx, step_name="03_register",
            json={"password": password, "username": phone},
            headers={**base_headers, "Origin": AUTH_BASE,
                     "Referer": f"{AUTH_BASE}/create-account/password",
                     "openai-sentinel-token": sentinel_data["sentinel_token"]})

        log(f"  动作:注册 | 状态:{'OK' if r.status_code == 200 else 'FAIL'} HTTP {r.status_code}")
        try:
            reg_data = r.json()
            log(f"  Response: {json.dumps(reg_data, ensure_ascii=False)[:300]}")
        except Exception:
            reg_data = {}

        if r.status_code != 200:
            err = reg_data.get("error", {}).get("message", str(reg_data))
            log(f"  动作:注册 | 状态:FAIL {err}")
            
            # 记录服务商风控阻断事件 (HTTP 400 / account_creation_failed)
            if r.status_code == 400 and sms_provider:
                failed_country = getattr(sms_provider, "last_country", None)
                failed_operator = getattr(sms_provider, "last_operator", None)
                if failed_country and failed_operator and failed_operator != "default":
                    try:
                        err_code = reg_data.get("error", {}).get("code", "unknown")
                        phone_db.add_failed_operator(
                            service=getattr(sms_provider, "service", "ot"),
                            country_id=failed_country,
                            operator_id=failed_operator,
                            error_code=err_code
                        )
                        log(f"[DB] ⚠️ 已录入供应商注册失败事件: 国家={failed_country}, 供应商={failed_operator}, 错误码={err_code}")
                    except Exception as db_err:
                        log(f"[DB] 录入供应商失败记录异常: {db_err}")

            ctx.save_result({"success": False, "phone": phone, "error": "register_failed", "detail": err})
            return None

        # ============================================================
        # Step 4: 触发短信发送
        # ============================================================
        log("[8/10] 触发短信发送 ...")
        continue_url = reg_data.get("continue_url", "")
        if continue_url:
            log("  动作:GET continue_url ...")
            r = _request(session, "GET", continue_url,
                ctx=ctx, step_name="04_sms_trigger",
                headers={**base_headers, "Origin": AUTH_BASE,
                         "Referer": f"{AUTH_BASE}/create-account/password"}, proxy=proxy)
            log(f"  动作:触发短信 | 状态:{'OK' if r.status_code == 200 else 'FAIL'} HTTP {r.status_code}")

        # ============================================================
        # Step 5: 等待验证码
        # ============================================================
        log("[9/10] 等待验证码 ...")
        if sms_provider and activation_id:
            log(f"  动作:轮询接码平台 | activation_id={activation_id}")
            try:
                otp_code = wait_for_sms_code(sms_provider, activation_id, timeout=otp_timeout)
                log(f"  动作:获取验证码 | 状态:OK code={otp_code}")
            except TimeoutError as e:
                log(f"  动作:获取验证码 | 状态:FAIL 超时")
                ctx.save_result({
                    "success": False, "phone": phone,
                    "activation_id": activation_id,
                    "error": "otp_fetch_failed", "detail": str(e),
                })
                raise
            except Exception as e:
                log(f"  动作:获取验证码 | 状态:FAIL {e}")
                ctx.save_result({
                    "success": False, "phone": phone,
                    "activation_id": activation_id,
                    "error": "otp_fetch_failed", "detail": str(e),
                })
                return None
        else:
            otp_code = input(f"\n📱 验证码已发送到 {phone}\n请输入6位验证码: ").strip()
            if not otp_code or not re.match(r'^\d{4,6}$', otp_code):
                log("验证码格式错误")
                ctx.save_result({"success": False, "phone": phone, "error": "invalid_otp_format"})
                return None

        # ============================================================
        # Step 6: 验证 OTP
        # ============================================================
        log("[9/10] 验证 OTP ...")
        r = _request(session, "POST", f"{AUTH_BASE}/api/accounts/phone-otp/validate",
            ctx=ctx, step_name="06_otp_validate",
            json={"code": otp_code},
            headers={**base_headers, "Origin": AUTH_BASE,
                     "Referer": f"{AUTH_BASE}/contact-verification"}, proxy=proxy)

        log(f"  动作:OTP验证 | 状态:{'OK' if r.status_code == 200 else 'FAIL'} HTTP {r.status_code}")
        try:
            otp_data = r.json()
        except Exception:
            otp_data = {}

        if r.status_code != 200:
            err = otp_data.get("error", {}).get("message", str(otp_data))
            log(f"  动作:OTP验证 | 状态:FAIL {err}")
            ctx.save_result({"success": False, "phone": phone, "error": "otp_failed", "detail": err})
            return None

        log("  动作:OTP验证 | 状态:OK 通过")

        # ============================================================
        # Step 7: 创建账户
        # ============================================================
        log("[10/10] 创建账户+获取Session ...")
        r = _request_with_sentinel_retry(session, "POST", f"{AUTH_BASE}/api/accounts/create_account",
            sentinel_data=sentinel_data, proxy=proxy,
            ctx=ctx, step_name="07_create_account",
            json={"name": full_name, "birthdate": birthdate},
            headers={**base_headers, "Origin": AUTH_BASE,
                     "Referer": f"{AUTH_BASE}/about-you",
                     "openai-sentinel-token": sentinel_data["sentinel_token"],
                     "openai-sentinel-so-token": sentinel_data.get("sentinel_so_token", "")})

        log(f"  动作:创建账户 | 状态:{'OK' if r.status_code == 200 else 'FAIL'} HTTP {r.status_code}")
        try:
            create_data = r.json()
        except Exception:
            create_data = {}

        if r.status_code != 200:
            err = create_data.get("error", {}).get("message", str(create_data))
            log(f"  动作:创建账户 | 状态:FAIL {err}")
            ctx.save_result({"success": False, "phone": phone, "error": "create_account_failed", "detail": err})
            return None

        log("  动作:创建账户 | 状态:OK")

        # ============================================================
        # Step 8: 获取 Session
        # ============================================================
        log("  动作:获取Session ...")
        session_data = None
        access_token = ""

        callback_url = create_data.get("continue_url", "")
        if callback_url:
            log("  动作:策略A-跟随callbackURL ...")
            try:
                r = _request(session, "GET", callback_url,
                    ctx=ctx, step_name="08a_callback",
                    headers={"User-Agent": UA,
                             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Referer": f"{AUTH_BASE}/"},
                    allow_redirects=True, max_retries=3, proxy=proxy)
                log(f"  Callback: {r.status_code} -> {r.url[:80]}")

                if "error=OAuthCallback" not in r.url and "auth/error" not in r.url:
                    r = _request(session, "GET", f"{CHAT_BASE}/api/auth/session",
                        ctx=ctx, step_name="08a_session",
                        headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
                        max_retries=2, proxy=proxy)
                    if r.status_code == 200:
                        session_data = r.json()
                        access_token = session_data.get("accessToken", "")
                        if access_token:
                            log("  动作:策略A-获取Session | 状态:OK")
            except Exception as e:
                log(f"  动作:策略A-获取Session | 状态:FAIL {e}")

        if not access_token:
            log("  动作:策略B-NextAuth登录流程 ...")
            try:
                login_session = curl_requests.Session()

                r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/csrf",
                    ctx=ctx, step_name="08b1_csrf",
                    headers={"User-Agent": UA, "Accept": "application/json"},
                    max_retries=2, proxy=proxy)
                csrf_token = r.json().get("csrfToken", "")
                log(f"  CSRF: {csrf_token[:20]}...")

                signin_url = (
                    f"{CHAT_BASE}/api/auth/signin/openai"
                    f"?prompt=login&ext-oai-did={did}"
                    f"&screen_hint=login"
                    f"&login_hint={quote(phone, safe='')}"
                )
                r = _request(login_session, "POST", signin_url,
                    ctx=ctx, step_name="08b2_signin",
                    data=urlencode({"csrfToken": csrf_token, "callbackUrl": "https://chatgpt.com/"}),
                    headers={"User-Agent": UA,
                             "Content-Type": "application/x-www-form-urlencoded",
                             "Origin": CHAT_BASE, "Referer": f"{CHAT_BASE}/"},
                    max_retries=2, proxy=proxy)
                log(f"  Signin: {r.status_code}")

                try:
                    signin_resp = r.json()
                    target_url = signin_resp.get("url", "") or signin_resp.get("continue_url", "")
                except Exception:
                    target_url = ""

                if target_url:
                    r = _request(login_session, "GET", target_url,
                        ctx=ctx, step_name="08b3_follow_url",
                        headers={"User-Agent": UA,
                                 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                 "Referer": f"{CHAT_BASE}/"},
                        allow_redirects=True, max_retries=2, proxy=proxy)
                    log(f"  Follow URL: {r.status_code} -> {r.url[:60]}")

                r = _request_with_sentinel_retry(login_session, "POST", f"{AUTH_BASE}/api/accounts/password/verify",
                    sentinel_data=sentinel_data, proxy=proxy,
                    ctx=ctx, step_name="08b4_password_verify",
                    json={"password": password},
                    headers={"User-Agent": UA, "Accept": "application/json",
                             "Origin": AUTH_BASE, "Referer": f"{AUTH_BASE}/log-in/password",
                             "openai-sentinel-token": sentinel_data["sentinel_token"]},
                    max_retries=2)
                log(f"  Password: {r.status_code}")

                if r.status_code == 200:
                    try:
                        pwd_data = r.json()
                        login_continue_url = pwd_data.get("continue_url", "")
                    except Exception:
                        login_continue_url = ""
                    if login_continue_url:
                        log("  跟随 login callback...")
                        r = _request(login_session, "GET", login_continue_url,
                            ctx=ctx, step_name="08b5_callback",
                            headers={"User-Agent": UA,
                                     "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                     "Referer": f"{AUTH_BASE}/"},
                            allow_redirects=True, max_retries=3, proxy=proxy)
                        log(f"  最终: {r.status_code} -> {r.url[:80]}")

                r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/session",
                    ctx=ctx, step_name="08b6_session",
                    headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
                    max_retries=3, proxy=proxy)
                if r.status_code == 200:
                    session_data = r.json()
                    access_token = session_data.get("accessToken", "")
                    if access_token:
                        log("  动作:策略B-获取Session | 状态:OK")
                        session.cookies.update(login_session.cookies)

            except Exception as e:
                log(f"  策略B 异常: {e}")

        ab_has_token = bool(access_token)

        z_session_raw = ""
        log("  动作:策略Z-reauthorize+session ...")
        try:
            r = _request(session, "GET",
                f"{AUTH_BASE}/api/accounts/authorize"
                f"?client_id={CLIENT_ID}"
                f"&scope={quote(scope)}&response_type=code"
                f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
                f"&audience={quote('https://api.openai.com/v1')}"
                f"&device_id={did}&prompt=none"
                f"&state={secrets.token_urlsafe(16)}",
                ctx=ctx, step_name="08z_reauthorize",
                headers={"User-Agent": UA,
                         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                         "Origin": AUTH_BASE, "Referer": f"{CHAT_BASE}/"},
                allow_redirects=True, max_retries=2, proxy=proxy)
            log(f"  策略Z-reauthorize: {r.status_code} -> {r.url[:80]}")

            r = _request(session, "GET", f"{CHAT_BASE}/api/auth/session",
                ctx=ctx, step_name="08z_session",
                headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
                max_retries=3, proxy=proxy)
            z_session_raw = r.text
            if r.status_code == 200:
                z_session_data = r.json()
                z_token = z_session_data.get("accessToken", "")
                if z_token:
                    if not ab_has_token:
                        session_data = z_session_data
                        access_token = z_token
                        log("  动作:策略Z-获取Session | 状态:OK(采用Z)")
                    else:
                        log("  动作:策略Z-获取Session | 状态:OK(确认,A/B已有)")
                else:
                    log("  动作:策略Z-获取Session | 状态:FAIL 无token")
            else:
                log(f"  动作:策略Z-获取Session | 状态:FAIL HTTP {r.status_code}")
        except Exception as e:
            log(f"  策略Z 异常: {e}")

        # ============================================================
        # 保存结果
        # ============================================================
        result = {
            "success": True,
            "phone": phone,
            "activation_id": activation_id or "",
            "password": password,
            "name": full_name,
            "birthdate": birthdate,
        }

        if access_token and session_data:
            result["session"] = session_data
            result["access_token"] = access_token
            log(f"  Token: {access_token[:50]}...")
            log(f"  过期: {session_data.get('expires', 'N/A')}")

            session_token = session_data.get("sessionToken", "")
            ctx.save_tokens_to_auth(
                access_token=access_token,
                session_token=session_token,
                device_id=did,
            )
            ctx.save_cookies_to_auth(session)

            ctx.save_account({
                "type": "register",
                "phone": phone,
                "password": password,
                "name": full_name,
                "birthdate": birthdate,
                "user_id": session_data.get("user", {}).get("id", ""),
                "email": session_data.get("user", {}).get("email"),
                "plan_type": session_data.get("account", {}).get("planType", ""),
                "expires": session_data.get("expires", ""),
                "registered_at": time.strftime("%Y-%m-%d %H:%M:%S"),
            })

            account_id = phone_db.add_account(
                phone=phone, password=password, name=full_name, birthdate=birthdate,
                email=session_data.get("user", {}).get("email", ""),
                user_id=session_data.get("user", {}).get("id", ""),
                plan_type=session_data.get("account", {}).get("planType", ""),
                access_token=access_token, session_token=session_token,
                token_status="success", oauth_status="success",
                payment_status="pending", account_status="active",
                proxy_ip=proxy, sms_provider=sms_provider_name,
                sms_cost=sms_cost_val, phone_region=phone_region,
                run_id=ctx.run_id,
            )
            log(f"  动作:保存账号 | 状态:OK account_id={account_id}")

            # 记录成功注册的优质运营商事件
            if sms_provider:
                success_country = getattr(sms_provider, "last_country", None)
                success_operator = getattr(sms_provider, "last_operator", None)
                if success_country and success_operator and success_operator != "default":
                    try:
                        phone_db.add_successful_operator(
                            service=getattr(sms_provider, "service", "dr"),
                            country_id=success_country,
                            operator_id=success_operator
                        )
                        log(f"[DB] 🏅 成功记入优质供应商成功事件: 国家={success_country}, 供应商={success_operator}")
                    except Exception as db_err:
                        log(f"[DB] 记入优质供应商成功记录异常: {db_err}")
        else:
            log("  动作:获取Session | 状态:FAIL 无token,可用手机号+密码重新登录")
            ctx.save_account({"status": "registered_but_no_session"})

            account_id = phone_db.add_account(
                phone=phone, password=password, name=full_name, birthdate=birthdate,
                token_status="failed", oauth_status="unknown",
                payment_status="pending", account_status="active",
                proxy_ip=proxy, sms_provider=sms_provider_name,
                sms_cost=sms_cost_val, phone_region=phone_region,
                run_id=ctx.run_id,
            )
            log(f"  动作:保存账号 | 状态:OK(无token) account_id={account_id}")

        result["task_dir"] = str(ctx.task_dir)

        if z_session_raw:
            try:
                z_path = _PROJECT_ROOT / "data" / f"z_session_{phone.lstrip('+')}.json"
                z_path.parent.mkdir(parents=True, exist_ok=True)
                z_path.write_text(z_session_raw, encoding="utf-8")
                log(f"  策略Z-session已保存: {z_path}")
            except Exception as e:
                log(f"  策略Z-session保存失败: {e}")

        ctx.save_result(result)

        with open(RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        log("=" * 60)
        log("🎉 注册成功!")
        log(f"  手机号: {phone}")
        log(f"  密码:   {password}")
        log(f"  姓名:   {full_name}")
        log(f"  生日:   {birthdate}")
        if access_token:
            log(f"  Token:  {access_token[:50]}...")
        log("=" * 60)
        log(f"💾 结果已保存: {ctx.task_dir}")

        _reg_success = True
        return result
    finally:
        try:
            ctx.update_meta_status("success" if _reg_success else "failed", final_phone=phone)
        except Exception:
            pass


def main():
    global _run_ctx
    log("=" * 60)
    log("  ChatGPT 手机号注册 - XYAuto 纯协议版")
    log("=" * 60)

    from sms_manager import DynamicSMSProvider

    stats = phone_db.stats()
    log(f"[DB] 号码库: 总计={stats['total']}, 成功={stats['success']}, "
        f"失败={stats['failed']}, 待定={stats['pending']}, 黑名单={stats['blacklist_count']}, "
        f"账号={stats['accounts']}, tokenOK={stats['token_ok']}, 支付OK={stats['payment_ok']}")

    round_num = 0
    while True:
        # 检查优雅停止信号
        if (_PROJECT_ROOT / "stop.flag").exists():
            log("🛑 检测到全局优雅停止信号 (stop.flag)，放弃开始新一轮注册，进程安全退出。")
            break

        round_num += 1
        log(f"{'='*30} 第 {round_num} 轮 {'='*30}")

        ctx = RunContext(flow_type="register", identifier=f"proc{PID}")
        _run_ctx = ctx
        run_id = ctx.run_id
        phone_db.add_run(run_id, flow_type="register")

        try:
            log("[1/10] 获取 JP 住宅代理...")
            proxy_url = acquire_proxy()
            log(f"  当前代理: {proxy_url[:60]}...")

            log(f"[2/10] 加载接码配置 ...")
            sms_cfg = load_register_sms_config()
            log(f"[3/10] 初始化接码Provider ...")
            sms_provider = DynamicSMSProvider(sms_cfg)

            otp_timeout = sms_cfg.get("otp_timeout_seconds", 120)
            max_attempts = sms_cfg.get("otp_retry_attempts", 10)

            result = retry_registration_with_new_gpt_phone(
                run_registration,
                sms_provider,
                max_attempts=max_attempts,
                otp_timeout=otp_timeout,
                proxy=proxy_url,
            )

            if result and result.get("success"):
                phone_db.update_run(run_id, status="success", phone_used=result.get("phone", ""), data_path=str(ctx.task_dir))
                log(f"第 {round_num} 轮注册成功! 继续下一轮...")
            else:
                phone_db.update_run(run_id, status="failed", error_message="registration_unsuccessful")
                log(f"第 {round_num} 轮注册未成功, 继续下一轮...")
        except KeyboardInterrupt:
            log("用户中断, 退出循环")
            try:
                phone_db.update_run(run_id, status="failed", error_message="user_interrupted")
                ctx.update_meta_status("failed")
            except Exception:
                pass
            break
        except Exception as e:
            err_name = type(e).__name__
            if err_name in ("InsufficientBalanceError", "InvalidApiKeyError", "NoAvailableNumbersError", "SMSProviderError"):
                log("=" * 70)
                log(f"🛑 [接码平台熔断拦截] {e}")
                log("=" * 70)
            else:
                log(f"第 {round_num} 轮异常: {e}")
                traceback.print_exc()
            phone_db.update_run(run_id, status="failed", error_message=str(e)[:200])
            ctx.update_meta_status("failed")
            ctx.save_error(str(e), traceback_str=traceback.format_exc())
            log("等待30秒后继续下一轮...")
            time.sleep(30)

        stats = phone_db.stats()
        log(f"[DB] 累计: 账号={stats['accounts']}, tokenOK={stats['token_ok']}, 支付OK={stats['payment_ok']}")

    stats = phone_db.stats()
    log(f"[DB] 最终: 账号={stats['accounts']}, tokenOK={stats['token_ok']}, 支付OK={stats['payment_ok']}")


if __name__ == "__main__":
    main()
