"""
ChatGPT 手机号注册 - 交互式终端
====================================
基于 sms 项目逻辑重写：用 curl_cffi 直接调 API，Playwright 仅提取 Sentinel token。
每次运行创建独立任务目录，详细记录每步入出参、Cookie、Token

用法: python phone_register.py
"""

import json, os, sys, time, uuid, secrets, random, re
from urllib.parse import quote, urlencode
from pathlib import Path

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from curl_cffi import requests as curl_requests
from task_context import TaskContext

WORKDIR = Path(__file__).parent
RESULT_FILE = WORKDIR / "phone_register_result.json"

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
    kwargs.setdefault("timeout", 30)
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
def extract_sentinel(ctx=None):
    """用 Playwright 提取 sentinel token (注册流程，两种 token)"""
    from playwright.sync_api import sync_playwright

    log("提取 Sentinel token...")

    for attempt in range(3):
        try:
            return _extract_sentinel_inner(ctx=ctx)
        except RuntimeError as e:
            if attempt < 2:
                log(f"  ⚠️ Sentinel 提取失败 ({attempt+1}/3): {e}，重试...")
                time.sleep(3)
            else:
                raise


def _extract_sentinel_inner(ctx=None):
    from playwright.sync_api import sync_playwright

    device_id = str(uuid.uuid4())
    state_val = secrets.token_urlsafe(32)
    scope = "openid email profile offline_access model.request model.read organization.read organization.write"
    auth_url = (
        f"{AUTH_BASE}/api/accounts/authorize"
        f"?client_id={CLIENT_ID}"
        f"&scope={quote(scope)}&response_type=code"
        f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
        f"&audience={quote('https://api.openai.com/v1')}"
        f"&device_id={device_id}&prompt=login"
        f"&screen_hint=signup&state={state_val}"
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
            log(f"  页面 URL: {page.url[:80]}")
            log(f"  页面标题: {page.title()[:50]}")
            browser.close()
            raise RuntimeError("SentinelSDK not loaded")

        page.evaluate("() => SentinelSDK.init()")
        time.sleep(2)

        did = page.evaluate("() => document.cookie.match(/oai-did=([^;]+)/)?.[1] || ''")

        sentinel_token = page.evaluate("""(did) => {
            return SentinelSDK.token().then(raw => {
                const parsed = JSON.parse(raw);
                parsed.id = did;
                parsed.flow = 'username_password_create';
                return JSON.stringify(parsed);
            });
        }""", did)

        sentinel_so = page.evaluate("""(did) => {
            return SentinelSDK.token().then(raw => {
                const parsed = JSON.parse(raw);
                return JSON.stringify({
                    so: raw, c: parsed.c, id: did, flow: 'oauth_create_account'
                });
            });
        }""", did)

        browser_cookies = pw_ctx.cookies()
        cookie_str = "; ".join(f"{c['name']}={c['value']}" for c in browser_cookies)
        browser.close()

    log(f"  ✅ Sentinel token 获取成功 (did: {did[:12]}...)")

    if ctx:
        ctx.log_step("01_sentinel", {
            "auth_url": auth_url,
            "device_id": device_id,
        }, {
            "sentinel_token": sentinel_token,
            "sentinel_so_token": sentinel_so,
            "oai_did": did,
            "sdk_load_time_sec": sdk_load_time,
        })
        ctx.save_token("sentinel_token", sentinel_token)
        ctx.save_token("sentinel_so_token", sentinel_so)
        ctx.save_token("oai_did", did)
        ctx.save_token_json("browser_cookies", browser_cookies)

    return {
        "sentinel_token": sentinel_token,
        "sentinel_so_token": sentinel_so,
        "cookie_str": cookie_str,
        "oai_did": did,
    }


# ==========================================
# Core Registration Flow
# ==========================================
def run_registration(phone):
    """核心注册流程 - 基于 sms 项目逻辑"""
    if not phone.startswith("+"):
        phone = "+" + phone

    # 随机生成账号资料
    chars = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"
    password = "".join(random.choices(chars, k=9)) + "!A1"
    first_names = ["James", "John", "Robert", "Michael", "David", "William", "Mary", "Linda", "Jennifer", "Barbara"]
    last_names = ["Smith", "Johnson", "Williams", "Brown", "Jones", "Garcia", "Miller", "Davis", "Wilson", "Anderson"]
    full_name = f"{random.choice(first_names)} {random.choice(last_names)}"
    birthdate = f"{random.randint(1985, 2004)}-{random.randint(1,12):02d}-{random.randint(1,28):02d}"

    log(f"手机号: {phone}")
    log(f"密码:   {password}")
    log(f"姓名:   {full_name}  生日: {birthdate}")

    # 创建任务上下文
    ctx = TaskContext("register", phone)
    ctx.save_account({
        "type": "register",
        "phone": phone,
        "password": password,
        "name": full_name,
        "birthdate": birthdate,
        "created_at": time.strftime("%Y-%m-%d %H:%M:%S"),
    })

    session = curl_requests.Session()
    base_headers = {"User-Agent": UA, "Accept": "application/json"}

    # ============================================================
    # Step 1: 提取 Sentinel token
    # ============================================================
    log("[1/8] 提取 Sentinel token...")
    sentinel_data = extract_sentinel(ctx=ctx)
    sentinel_token = sentinel_data["sentinel_token"]
    sentinel_so_token = sentinel_data.get("sentinel_so_token", "")
    did = sentinel_data.get("oai_did", str(uuid.uuid4()))

    # ============================================================
    # Step 2: Prime + Auth flow
    # ============================================================
    log("[2/8] 建立 Auth session...")

    # 2a: Prime
    _request(session, "GET", f"{AUTH_BASE}/create-account",
        ctx=ctx, step_name="02a_prime",
        headers={**base_headers, "Accept": "text/html,application/xhtml+xml"},
        max_retries=2)

    # 2b: Signin
    session_logging_id = str(uuid.uuid4()).replace("-", "")
    signin_url = (
        f"{CHAT_BASE}/api/auth/signin/openai"
        f"?prompt=login&ext-oai-did={did}"
        f"&auth_session_logging_id={session_logging_id}"
        f"&screen_hint=login_or_signup"
        f"&login_hint={quote(phone, safe='')}"
    )
    _request(session, "POST", signin_url,
        ctx=ctx, step_name="02b_signin",
        data=urlencode({"csrfToken": "true"}),
        headers={**base_headers, "Content-Type": "application/x-www-form-urlencoded",
                 "Origin": CHAT_BASE, "Referer": f"{CHAT_BASE}/"},
        max_retries=2)

    # 2c: Authorize
    scope = "openid email profile offline_access model.request model.read organization.read organization.write"
    auth_session_url = (
        f"{AUTH_BASE}/api/accounts/authorize"
        f"?client_id={CLIENT_ID}"
        f"&scope={quote(scope)}&response_type=code"
        f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
        f"&audience={quote('https://api.openai.com/v1')}"
        f"&device_id={did}&prompt=login&screen_hint=login_or_signup"
        f"&login_hint={quote(phone, safe='')}"
        f"&state={secrets.token_urlsafe(16)}"
    )
    r = _request(session, "GET", auth_session_url,
        ctx=ctx, step_name="02c_authorize",
        headers={**base_headers, "Accept": "text/html,application/xhtml+xml",
                 "Origin": AUTH_BASE, "Referer": f"{CHAT_BASE}/"})

    redirect_path = r.url.split("auth.openai.com")[-1]
    log(f"  重定向到: {redirect_path}")

    # 检测是否已注册
    if "log-in" in redirect_path or "login" in redirect_path:
        log("⚠️ 该手机号已注册!")
        ctx.save_result({"success": False, "phone": phone, "error": "already_registered"})
        ctx.save_account({"status": "already_registered"})
        return {"success": False, "phone": phone, "error": "already_registered"}

    # ============================================================
    # Step 3: Register
    # ============================================================
    log("[3/8] 注册 (提交手机号+密码)...")
    r = _request(session, "POST", f"{AUTH_BASE}/api/accounts/user/register",
        ctx=ctx, step_name="03_register",
        json={"password": password, "username": phone},
        headers={**base_headers, "Origin": AUTH_BASE,
                 "Referer": f"{AUTH_BASE}/create-account/password",
                 "openai-sentinel-token": sentinel_token})

    log(f"  Status: {r.status_code}")
    try:
        reg_data = r.json()
        log(f"  Response: {json.dumps(reg_data, ensure_ascii=False)[:300]}")
    except Exception:
        reg_data = {}
        log(f"  Body: {r.text[:300]}")

    if r.status_code != 200:
        err = reg_data.get("error", {}).get("message", str(reg_data))
        log(f"❌ 注册失败: {err}")
        ctx.save_result({"success": False, "phone": phone, "error": "register_failed", "detail": err})
        return None

    # ============================================================
    # Step 4: 触发短信发送
    # ============================================================
    log("[4/8] 触发短信发送...")
    continue_url = reg_data.get("continue_url", "")
    if continue_url:
        r = _request(session, "GET", continue_url,
            ctx=ctx, step_name="04_sms_trigger",
            headers={**base_headers, "Origin": AUTH_BASE,
                     "Referer": f"{AUTH_BASE}/create-account/password"})
        log(f"  SMS trigger: {r.status_code}")

    # ============================================================
    # Step 5: 等待验证码
    # ============================================================
    log("[5/8] 等待验证码...")
    otp_code = input(f"\n📱 验证码已发送到 {phone}\n请输入6位验证码: ").strip()
    if not otp_code or not re.match(r'^\d{4,6}$', otp_code):
        log("验证码格式错误")
        ctx.save_result({"success": False, "phone": phone, "error": "invalid_otp_format"})
        return None

    # ============================================================
    # Step 6: 验证 OTP
    # ============================================================
    log(f"[6/8] 验证 OTP: {otp_code}...")
    r = _request(session, "POST", f"{AUTH_BASE}/api/accounts/phone-otp/validate",
        ctx=ctx, step_name="06_otp_validate",
        json={"code": otp_code},
        headers={**base_headers, "Origin": AUTH_BASE,
                 "Referer": f"{AUTH_BASE}/contact-verification"})

    log(f"  Status: {r.status_code}")
    try:
        otp_data = r.json()
        log(f"  Response: {json.dumps(otp_data, ensure_ascii=False)[:300]}")
    except Exception:
        otp_data = {}

    if r.status_code != 200:
        err = otp_data.get("error", {}).get("message", str(otp_data))
        log(f"❌ OTP 验证失败: {err}")
        ctx.save_result({"success": False, "phone": phone, "error": "otp_failed", "detail": err})
        return None

    log("  ✅ OTP 验证成功!")

    # ============================================================
    # Step 7: 创建账户
    # ============================================================
    log("[7/8] 创建账户...")
    r = _request(session, "POST", f"{AUTH_BASE}/api/accounts/create_account",
        ctx=ctx, step_name="07_create_account",
        json={"name": full_name, "birthdate": birthdate},
        headers={**base_headers, "Origin": AUTH_BASE,
                 "Referer": f"{AUTH_BASE}/about-you",
                 "openai-sentinel-token": sentinel_token,
                 "openai-sentinel-so-token": sentinel_so_token})

    log(f"  Status: {r.status_code}")
    try:
        create_data = r.json()
        log(f"  Response: {json.dumps(create_data, ensure_ascii=False)[:300]}")
    except Exception:
        create_data = {}

    if r.status_code != 200:
        err = create_data.get("error", {}).get("message", str(create_data))
        log(f"❌ 创建账户失败: {err}")
        ctx.save_result({"success": False, "phone": phone, "error": "create_account_failed", "detail": err})
        return None

    log("  ✅ 账户创建成功!")

    # ============================================================
    # Step 8: 获取 Session
    # ============================================================
    log("[8/8] 获取 Session...")
    session_data = None
    access_token = ""

    # 策略 A: 跟随 callback URL
    callback_url = create_data.get("continue_url", "")
    if callback_url:
        log("  策略A: 跟随 callback URL...")
        try:
            r = _request(session, "GET", callback_url,
                ctx=ctx, step_name="08a_callback",
                headers={"User-Agent": UA,
                         "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                         "Referer": f"{AUTH_BASE}/"},
                allow_redirects=True, max_retries=3)
            log(f"  Callback: {r.status_code} -> {r.url[:80]}")

            if "error=OAuthCallback" not in r.url and "auth/error" not in r.url:
                r = _request(session, "GET", f"{CHAT_BASE}/api/auth/session",
                    ctx=ctx, step_name="08a_session",
                    headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
                    max_retries=2)
                if r.status_code == 200:
                    session_data = r.json()
                    access_token = session_data.get("accessToken", "")
                    if access_token:
                        log("  ✅ Session 获取成功 (策略A)!")
        except Exception as e:
            log(f"  策略A 失败: {e}")

    # 策略 B: 完整 NextAuth 登录流程
    if not access_token:
        log("  策略B: 完整 NextAuth 登录流程...")
        try:
            login_session = curl_requests.Session()

            # B1: CSRF
            r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/csrf",
                ctx=ctx, step_name="08b1_csrf",
                headers={"User-Agent": UA, "Accept": "application/json"},
                max_retries=2)
            csrf_token = r.json().get("csrfToken", "")
            log(f"  CSRF: {csrf_token[:20]}...")

            # B2: Signin
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
                max_retries=2)
            log(f"  Signin: {r.status_code}")

            # B3: 跟随 URL
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
                    allow_redirects=True, max_retries=2)
                log(f"  Follow URL: {r.status_code} -> {r.url[:60]}")

            # B4: 密码验证
            r = _request(login_session, "POST", f"{AUTH_BASE}/api/accounts/password/verify",
                ctx=ctx, step_name="08b4_password_verify",
                json={"password": password},
                headers={"User-Agent": UA, "Accept": "application/json",
                         "Origin": AUTH_BASE, "Referer": f"{AUTH_BASE}/log-in/password",
                         "openai-sentinel-token": sentinel_token},
                max_retries=2)
            log(f"  Password: {r.status_code}")

            if r.status_code == 200:
                pwd_data = r.json()
                login_continue_url = pwd_data.get("continue_url", "")
                if login_continue_url:
                    log("  跟随 login callback...")
                    r = _request(login_session, "GET", login_continue_url,
                        ctx=ctx, step_name="08b5_callback",
                        headers={"User-Agent": UA,
                                 "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                                 "Referer": f"{AUTH_BASE}/"},
                        allow_redirects=True, max_retries=3)
                    log(f"  最终: {r.status_code} -> {r.url[:80]}")

            # B6: 获取 session
            r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/session",
                ctx=ctx, step_name="08b6_session",
                headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
                max_retries=3)
            if r.status_code == 200:
                session_data = r.json()
                access_token = session_data.get("accessToken", "")
                if access_token:
                    log("  ✅ Session 获取成功 (策略B)!")
                    for ck in login_session.cookies.jar:
                        session.cookies.jar.add(ck)

            if not access_token:
                # B7: Reauthorize
                log("  尝试 reauthorize...")
                r = _request(login_session, "GET",
                    f"{AUTH_BASE}/api/accounts/authorize"
                    f"?client_id={CLIENT_ID}"
                    f"&scope={quote(scope)}&response_type=code"
                    f"&redirect_uri={quote('https://chatgpt.com/api/auth/callback/openai')}"
                    f"&audience={quote('https://api.openai.com/v1')}"
                    f"&device_id={did}&prompt=none"
                    f"&state={secrets.token_urlsafe(16)}",
                    ctx=ctx, step_name="08b7_reauthorize",
                    headers={"User-Agent": UA,
                             "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
                             "Origin": AUTH_BASE, "Referer": f"{CHAT_BASE}/"},
                    allow_redirects=True, max_retries=2)
                log(f"  Reauthorize: {r.status_code} -> {r.url[:80]}")

                r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/session",
                    ctx=ctx, step_name="08b7_session",
                    headers={"User-Agent": UA, "Accept": "application/json", "Referer": f"{CHAT_BASE}/"},
                    max_retries=3)
                if r.status_code == 200:
                    session_data = r.json()
                    access_token = session_data.get("accessToken", "")
                    if access_token:
                        log("  ✅ Session 获取成功 (reauthorize)!")
                        for ck in login_session.cookies.jar:
                            session.cookies.jar.add(ck)
        except Exception as e:
            log(f"  策略B 异常: {e}")

    # ============================================================
    # 保存结果
    # ============================================================
    result = {
        "success": True,
        "phone": phone,
        "password": password,
        "name": full_name,
        "birthdate": birthdate,
    }

    if access_token and session_data:
        result["session"] = session_data
        result["access_token"] = access_token
        log(f"  Token: {access_token[:50]}...")
        log(f"  过期: {session_data.get('expires', 'N/A')}")

        # 保存 token
        ctx.save_token("access_token", access_token)
        session_token = session_data.get("sessionToken", "")
        if session_token:
            ctx.save_token("session_token", session_token)
        ctx.save_token_json("full_session", session_data)

        # 更新账号信息
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
    else:
        log("  ⚠️ 未获取到 session token，可用手机号+密码重新登录")
        ctx.save_account({"status": "registered_but_no_session"})

    result["task_dir"] = str(ctx.task_dir)
    ctx.save_result(result)

    # 同时保存到工作区根目录（向后兼容）
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

    return result


def main():
    log("=" * 60)
    log("  ChatGPT 手机号注册 - 交互式终端 (v3)")
    log("=" * 60)

    phone = input("\n📱 请输入手机号 (如 +573018171707): ").strip()
    if not phone:
        log("手机号为空，退出")
        return

    try:
        run_registration(phone)
    except Exception as e:
        log(f"❌ 注册异常: {e}")
        import traceback
        traceback.print_exc()

    input("\n按回车退出...")


if __name__ == "__main__":
    main()
