"""
ChatGPT 手机号登录 - 获取 Session
====================================
用已注册的手机号+密码登录，获取 accessToken
每次运行创建独立任务目录，详细记录每步入出参、Cookie、Token

用法: python phone_login.py
"""

import json, os, sys, time, uuid, secrets, re
from urllib.parse import quote, urlencode, urlparse, parse_qs
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from curl_cffi import requests as curl_requests
from task_context import TaskContext

WORKDIR = Path(__file__).parent
RESULT_FILE = WORKDIR / "login_result.json"

AUTH_BASE = "https://auth.openai.com"
CHAT_BASE = "https://chatgpt.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/148.0.0.0 Safari/537.36"
CLIENT_ID = "app_X8zY6vW2pQ9tR3dE7nK1jL5gH"

PROXY = "http://127.0.0.1:7897"


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def _request(session, method, url, ctx=None, step_name=None, max_retries=3, **kwargs):
    """带重试的请求，可选自动记录到 TaskContext"""
    kwargs.setdefault("impersonate", "chrome")
    kwargs.setdefault("timeout", 60)
    if PROXY and "proxies" not in kwargs:
        kwargs["proxies"] = {"http": PROXY, "https": PROXY}

    for attempt in range(max_retries):
        try:
            r = session.request(method, url, **kwargs)
            break
        except Exception as e:
            if attempt < max_retries - 1:
                wait = (attempt + 1) * 3
                log(f"  请求失败，{wait}s 后重试 ({attempt+1}/{max_retries}): {e}")
                time.sleep(wait)
            else:
                log(f"  ❌ 请求最终失败: {e}")
                raise

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

    return r


# ==========================================
# Sentinel token (Playwright)
# ==========================================
def extract_sentinel(flow="username_password_login", ctx=None):
    """用 Playwright 提取 sentinel token"""
    from playwright.sync_api import sync_playwright

    log(f"提取 Sentinel token (flow={flow})...")

    device_id = str(uuid.uuid4())
    state_val = secrets.token_urlsafe(32)
    scope = "openid email profile offline_access model.request model.read organization.read organization.write"
    screen_hint = "login" if "login" in flow else "signup"
    auth_url = (
        f"{AUTH_BASE}/api/accounts/authorize"
        f"?client_id={CLIENT_ID}"
        f"&scope={quote(scope)}&response_type=code"
        f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
        f"&audience={quote('https://api.openai.com/v1')}"
        f"&device_id={device_id}&prompt=login"
        f"&screen_hint={screen_hint}&state={state_val}"
    )

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=True,
            proxy={"server": PROXY} if PROXY else None,
            args=["--disable-blink-features=AutomationControlled"]
        )
        pw_ctx = browser.new_context(
            user_agent=UA,
            viewport={"width": 1280, "height": 800},
            locale="en-US",
            timezone_id="America/New_York",
        )
        page = pw_ctx.new_page()

        try:
            page.goto(auth_url, wait_until="domcontentloaded", timeout=120000)
        except Exception:
            page.goto(auth_url, wait_until="commit", timeout=120000)

        try:
            page.wait_for_load_state("networkidle", timeout=30000)
        except Exception:
            pass

        sdk_load_time = None
        for i in range(45):
            time.sleep(2)
            if page.evaluate("() => typeof window.SentinelSDK !== 'undefined'"):
                sdk_load_time = i * 2
                log(f"  SentinelSDK loaded ({sdk_load_time}s)")
                break
        else:
            log(f"  URL: {page.url[:80]}")
            browser.close()
            raise RuntimeError("SentinelSDK not loaded")

        page.evaluate("() => SentinelSDK.init()")
        time.sleep(2)

        did = page.evaluate("() => document.cookie.match(/oai-did=([^;]+)/)?.[1] || ''")
        sentinel_token = page.evaluate("""([did, flow]) => {
            return SentinelSDK.token().then(raw => {
                const parsed = JSON.parse(raw);
                parsed.id = did;
                parsed.flow = flow;
                return JSON.stringify(parsed);
            });
        }""", [did, flow])

        # 提取浏览器 cookies
        browser_cookies = pw_ctx.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in browser_cookies)
        browser.close()

    log(f"  ✅ Sentinel token 获取成功 (did: {did[:12]}...)")

    if ctx:
        ctx.log_step("04_sentinel", {
            "flow": flow,
            "auth_url": auth_url,
            "device_id": device_id,
        }, {
            "sentinel_token": sentinel_token,
            "oai_did": did,
            "sdk_load_time_sec": sdk_load_time,
        })
        ctx.save_token("sentinel_token", sentinel_token)
        ctx.save_token("oai_did", did)
        ctx.save_token_json("browser_cookies", browser_cookies)

    return {
        "sentinel_token": sentinel_token,
        "cookie_str": cookie_str,
        "oai_did": did,
    }


# ==========================================
# 核心: 登录流程
# ==========================================
def run_login(phone, password):
    """用手机号+密码登录，正确处理 NextAuth callback"""
    if not phone.startswith("+"):
        phone = "+" + phone

    ctx = TaskContext("login", phone)

    # 保存账号信息
    ctx.save_account({
        "type": "login",
        "phone": phone,
        "password": password,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    session = curl_requests.Session()
    proxy_dict = {"http": PROXY, "https": PROXY} if PROXY else None

    # ============================================================
    # Step 1: CSRF
    # ============================================================
    log("[1/8] 获取 CSRF token...")
    r = _request(session, "GET", f"{CHAT_BASE}/api/auth/csrf",
        ctx=ctx, step_name="01_csrf",
        headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
        proxies=proxy_dict)
    if r.status_code != 200:
        log(f"  ❌ CSRF 失败: {r.status_code}")
        ctx.save_result({"success": False, "error": "csrf_failed", "status_code": r.status_code})
        return None
    csrf_token = r.json().get("csrfToken", "")
    log(f"  ✅ CSRF: {csrf_token[:20]}...")

    # ============================================================
    # Step 2: Signin
    # ============================================================
    log("[2/8] 发起 OAuth signin...")
    did = str(uuid.uuid4())
    signin_url = (
        f"{CHAT_BASE}/api/auth/signin/openai"
        f"?prompt=login&ext-oai-did={did}"
        f"&screen_hint=login"
        f"&login_hint={quote(phone, safe='')}"
    )
    r = _request(session, "POST", signin_url,
        ctx=ctx, step_name="02_signin",
        data=urlencode({"csrfToken": csrf_token, "callbackUrl": "https://chatgpt.com/"}),
        headers={
            "User-Agent": UA,
            "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/json",
            "Origin": CHAT_BASE,
            "Referer": f"{CHAT_BASE}/auth/login",
        },
        proxies=proxy_dict)

    log(f"  Signin: {r.status_code}")
    try:
        signin_data = r.json()
        authorize_url = signin_data.get("url", "")
        continue_url_from_signin = signin_data.get("continue_url", "")
        if authorize_url:
            log(f"  Authorize URL: ...{authorize_url[-60:]}")
        if continue_url_from_signin:
            log(f"  Continue URL: {continue_url_from_signin}")
    except Exception:
        authorize_url = ""
        continue_url_from_signin = ""
        log(f"  Body: {r.text[:300]}")

    if not authorize_url and not continue_url_from_signin:
        log("❌ 未获取到 authorize URL 或 continue_url")
        ctx.save_result({"success": False, "error": "no_authorize_url", "phone": phone})
        return None

    # ============================================================
    # Step 3: 跟随 URL
    # ============================================================
    target_url = authorize_url or continue_url_from_signin
    log("[3/8] 跟随 URL...")
    r = _request(session, "GET", target_url,
        ctx=ctx, step_name="03_follow_url",
        headers={
            "User-Agent": UA,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            "Referer": f"{CHAT_BASE}/",
        },
        proxies=proxy_dict, allow_redirects=True)

    redirect_path = r.url.split("auth.openai.com")[-1] if "auth.openai.com" in r.url else r.url[-60:]
    log(f"  最终页面: {redirect_path}")

    # ============================================================
    # Step 4: Sentinel
    # ============================================================
    log("[4/8] 提取 Sentinel token...")
    sentinel_data = extract_sentinel("username_password_login", ctx=ctx)
    sentinel_token = sentinel_data["sentinel_token"]

    # ============================================================
    # Step 5: 密码验证
    # ============================================================
    log("[5/8] 密码验证...")
    r = _request(session, "POST", f"{AUTH_BASE}/api/accounts/password/verify",
        ctx=ctx, step_name="05_password_verify",
        json={"password": password},
        headers={
            "User-Agent": UA,
            "Content-Type": "application/json",
            "Accept": "application/json",
            "Origin": AUTH_BASE,
            "Referer": f"{AUTH_BASE}/log-in/password",
            "openai-sentinel-token": sentinel_token,
        },
        proxies=proxy_dict)

    log(f"  Status: {r.status_code}")
    try:
        pwd_data = r.json()
        log(f"  Response: {json.dumps(pwd_data, ensure_ascii=False)[:300]}")
    except Exception:
        pwd_data = {}

    if r.status_code != 200:
        err = pwd_data.get("error", {}).get("message", str(pwd_data))
        log(f"❌ 密码验证失败: {err}")
        ctx.save_result({"success": False, "error": "password_failed", "detail": err, "phone": phone})
        return None

    page_type = pwd_data.get("page", {}).get("type", "")
    continue_url = pwd_data.get("continue_url", "")
    log(f"  ✅ 密码验证成功! page_type: {page_type}")

    # ============================================================
    # Step 6: OTP 验证 (如果需要)
    # ============================================================
    if page_type in ("phone_otp_verification", "contact_verification") or \
       "/contact-verification" in continue_url or "/phone-verification" in continue_url:

        log("[6/8] 需要 OTP，触发短信...")
        try:
            r = _request(session, "GET", f"{AUTH_BASE}/api/accounts/phone-otp/send",
                ctx=ctx, step_name="06_otp_send",
                headers={
                    "User-Agent": UA,
                    "Accept": "application/json",
                    "Origin": AUTH_BASE,
                    "Referer": f"{AUTH_BASE}/log-in/password",
                },
                proxies=proxy_dict)
            log(f"  phone-otp/send: {r.status_code}")
        except Exception as e:
            log(f"  phone-otp/send 失败: {e}")

        otp_code = input(f"\n📱 验证码已发送到 {phone}\n请输入验证码: ").strip()
        if not otp_code or not re.match(r'^\d{4,6}$', otp_code):
            log("验证码格式错误")
            ctx.save_result({"success": False, "error": "invalid_otp", "phone": phone})
            return None

        log(f"  验证 OTP: {otp_code}...")
        r = _request(session, "POST", f"{AUTH_BASE}/api/accounts/phone-otp/validate",
            ctx=ctx, step_name="06_otp_validate",
            json={"code": otp_code},
            headers={
                "User-Agent": UA,
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Origin": AUTH_BASE,
                "Referer": f"{AUTH_BASE}/contact-verification",
            },
            proxies=proxy_dict, max_retries=5)

        log(f"  Status: {r.status_code}")
        try:
            otp_data = r.json()
            log(f"  Response: {json.dumps(otp_data, ensure_ascii=False)[:300]}")
        except Exception:
            otp_data = {}

        if r.status_code != 200:
            err = otp_data.get("error", {}).get("message", str(otp_data))
            log(f"❌ OTP 验证失败: {err}")
            ctx.save_result({"success": False, "error": "otp_failed", "detail": err, "phone": phone})
            return None

        page_type = otp_data.get("page", {}).get("type", "")
        continue_url = otp_data.get("continue_url", "")
        log(f"  ✅ OTP 验证成功! page_type: {page_type}")
    else:
        log(f"[6/8] 无需 OTP (page_type: {page_type})")

    # ============================================================
    # Step 7: 跟随 callback 重定向
    # ============================================================
    log("[7/8] 跟随 callback 重定向...")
    scope = "openid email profile offline_access model.request model.read organization.read organization.write"

    if continue_url:
        r = _request(session, "GET", continue_url,
            ctx=ctx, step_name="07_callback",
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Referer": f"{AUTH_BASE}/",
            },
            proxies=proxy_dict, allow_redirects=True, max_retries=3)
        log(f"  最终: {r.status_code} -> {r.url[:80]}")

        if "error=OAuthCallback" in r.url or "auth/error" in r.url:
            log("  ⚠️ OAuth callback 错误，尝试重新 authorize...")
            r = _request(session, "GET",
                f"{AUTH_BASE}/api/accounts/authorize"
                f"?client_id={CLIENT_ID}"
                f"&scope={quote(scope)}&response_type=code"
                f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
                f"&audience={quote('https://api.openai.com/v1')}"
                f"&device_id={did}&prompt=none"
                f"&state={secrets.token_urlsafe(16)}",
                ctx=ctx, step_name="07_reauthorize",
                headers={
                    "User-Agent": UA,
                    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                    "Origin": AUTH_BASE,
                    "Referer": f"{CHAT_BASE}/",
                },
                proxies=proxy_dict, allow_redirects=True)
            log(f"  Reauthorize: {r.status_code} -> {r.url[:80]}")
    else:
        log("  ⚠️ 无 continue_url，尝试重新 authorize...")
        r = _request(session, "GET",
            f"{AUTH_BASE}/api/accounts/authorize"
            f"?client_id={CLIENT_ID}"
            f"&scope={quote(scope)}&response_type=code"
            f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
            f"&audience={quote('https://api.openai.com/v1')}"
            f"&device_id={did}&prompt=none"
            f"&state={secrets.token_urlsafe(16)}",
            ctx=ctx, step_name="07_reauthorize",
            headers={
                "User-Agent": UA,
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                "Origin": AUTH_BASE,
                "Referer": f"{CHAT_BASE}/",
            },
            proxies=proxy_dict, allow_redirects=True)
        log(f"  Reauthorize: {r.status_code} -> {r.url[:80]}")

    # ============================================================
    # Step 8: 获取 session
    # ============================================================
    log("[8/8] 获取 session...")
    r = _request(session, "GET", f"{CHAT_BASE}/api/auth/session",
        ctx=ctx, step_name="08_session",
        headers={
            "User-Agent": UA,
            "Accept": "application/json",
            "Referer": f"{CHAT_BASE}/",
        },
        proxies=proxy_dict, max_retries=3)

    if r.status_code != 200:
        log(f"❌ Session 请求失败: {r.status_code}")
        ctx.save_result({"success": False, "error": "session_failed", "status_code": r.status_code, "phone": phone})
        return None

    session_data = r.json()
    access_token = session_data.get("accessToken", "")

    if access_token:
        log("=" * 60)
        log("🎉 登录成功!")
        log(f"  手机号: {phone}")
        log(f"  密码:   {password}")
        log(f"  Token:  {access_token[:50]}...")
        log(f"  过期:   {session_data.get('expires', 'N/A')}")
        log(f"  User:   {session_data.get('user', {}).get('email', 'N/A')}")
        log("=" * 60)

        # 保存 token
        ctx.save_token("access_token", access_token)
        session_token = session_data.get("sessionToken", "")
        if session_token:
            ctx.save_token("session_token", session_token)
        ctx.save_token_json("full_session", session_data)

        # 更新账号信息
        ctx.save_account({
            "type": "login",
            "phone": phone,
            "password": password,
            "user_id": session_data.get("user", {}).get("id", ""),
            "name": session_data.get("user", {}).get("name", ""),
            "email": session_data.get("user", {}).get("email"),
            "plan_type": session_data.get("account", {}).get("planType", ""),
            "expires": session_data.get("expires", ""),
            "login_at": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

        # 保存最终结果
        result = {
            "success": True,
            "type": "login",
            "phone": phone,
            "password": password,
            "session": session_data,
            "task_dir": str(ctx.task_dir),
        }
        ctx.save_result(result)

        # 同时保存到工作区根目录（向后兼容）
        with open(RESULT_FILE, 'w', encoding='utf-8') as f:
            json.dump(result, f, ensure_ascii=False, indent=2, default=str)

        log(f"💾 结果已保存: {ctx.task_dir}")
        return result
    else:
        log("⚠️ 未获取到 accessToken")
        log(f"  Session: {json.dumps(session_data, ensure_ascii=False)[:300]}")
        ctx.save_result({"success": False, "error": "no_access_token", "phone": phone, "session_raw": session_data})
        return None


def main():
    log("=" * 60)
    log("  ChatGPT 手机号登录 - 获取 Session")
    log("=" * 60)

    phone = input("\n📱 请输入手机号 (如 +573135882082): ").strip()
    if not phone:
        log("手机号为空，退出")
        return

    password = input("🔑 请输入密码: ").strip()
    if not password:
        log("密码为空，退出")
        return

    try:
        run_login(phone, password)
    except Exception as e:
        log(f"❌ 登录异常: {e}")
        import traceback
        traceback.print_exc()

    input("\n按回车退出...")


if __name__ == "__main__":
    main()
