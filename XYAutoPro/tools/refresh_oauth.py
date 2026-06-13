"""
tools/refresh_oauth.py
======================
正式工具：批量补取 / 刷新历史账号的 OAuth (access_token + session_token)

用法:
    # 补取所有 token_status != 'success' 且有密码的账号（含 inactive）
    python tools/refresh_oauth.py

    # 补取指定手机号（不管 account_status）
    python tools/refresh_oauth.py --phone +351967531121

    # 强制刷新所有有密码的账号
    python tools/refresh_oauth.py --all

    # 并发数（默认 1，建议不超过 3，避免被风控）
    python tools/refresh_oauth.py --workers 2

    # 跳过代理（调试用）
    python tools/refresh_oauth.py --no-proxy

原理:
    与主脚本 core/register.py 策略B 完全一致：
      现取 JP 住宅代理（acquire_proxy）+ curl_cffi impersonate=chrome
      + Sentinel token + 密码登录
    → GET chatgpt.com/api/auth/session
    → 取回 accessToken + __Secure-next-auth.session-token
    → 写入 accounts 表 (access_token, session_token, token_status, token_updated_at)
"""

import sys, os, json, time, uuid, argparse, sqlite3
from concurrent.futures import ThreadPoolExecutor, as_completed
from urllib.parse import quote, urlencode
from pathlib import Path

ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "core"))
sys.path.insert(0, str(ROOT / "sentinel"))

sys.stdout.reconfigure(encoding='utf-8', errors='replace')

from curl_cffi import requests as curl_requests
from core.phone_db import PhoneDB
from core.register import (
    extract_sentinel,
    _request,
    _request_with_sentinel_retry,
    acquire_proxy,          # 主流程：获取JP住宅代理
    _verify_proxy_country,  # 主流程：验证代理国家
)

AUTH_BASE = "https://auth.openai.com"
CHAT_BASE = "https://chatgpt.com"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Chrome/148.0.0.0 Safari/537.36"

_db = PhoneDB()


def log(tag: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{tag}] {msg}", flush=True)


# ── 核心：重新登录取 session ─────────────────────────────────────────────────

def relogin(phone: str, password: str, proxy: str = "", sentinel_data: dict = None) -> dict:
    """
    用 phone + password 重新登录，取回 access_token / session_token。

    Args:
        phone:         手机号 (e.g. "+351967531121")
        password:      账号密码
        proxy:         代理 URL (可为空)
        sentinel_data: 可复用的 sentinel_data dict（批量时共享节省时间）

    Returns:
        {"access_token": str, "session_token": str} 或 {} 表示失败
    """
    tag = phone[-8:]  # 日志 tag 用末 8 位
    base_headers = {"User-Agent": UA, "Accept": "application/json"}
    did = str(uuid.uuid4())

    # Step 1: Sentinel token（外部传入时复用，否则现取）
    if not sentinel_data:
        log(tag, "获取 Sentinel token ...")
        sentinel_data = extract_sentinel(proxy=proxy)
        log(tag, f"Sentinel OK len={len(sentinel_data['sentinel_token'])}")

    # Step 2: CSRF
    login_session = curl_requests.Session()
    r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/csrf",
                 headers={**base_headers, "Referer": f"{CHAT_BASE}/"},
                 max_retries=2, proxy=proxy)
    csrf_token = r.json().get("csrfToken", "")
    if not csrf_token:
        log(tag, "FAIL: 无法获取 CSRF token")
        return {}
    log(tag, f"CSRF OK")

    # Step 3: POST signin/openai
    signin_url = (
        f"{CHAT_BASE}/api/auth/signin/openai"
        f"?prompt=login&ext-oai-did={did}"
        f"&screen_hint=login"
        f"&login_hint={quote(phone, safe='')}"
    )
    r = _request(login_session, "POST", signin_url,
                 data=urlencode({"csrfToken": csrf_token, "callbackUrl": "https://chatgpt.com/"}),
                 headers={**base_headers,
                          "Content-Type": "application/x-www-form-urlencoded",
                          "Origin": CHAT_BASE, "Referer": f"{CHAT_BASE}/"},
                 max_retries=2, proxy=proxy)

    try:
        signin_resp = r.json()
        target_url = signin_resp.get("url", "") or signin_resp.get("continue_url", "")
    except Exception:
        target_url = ""

    if not target_url:
        if r.status_code == 403:
            log(tag, "FAIL: Signin 403 (当前代理 IP 触发了 Cloudflare Turnstile 验证盾)")
        else:
            log(tag, f"FAIL: Signin 未返回跳转 URL (HTTP {r.status_code})")
        return {}

    # Step 3.5: 跟随 redirect
    r = _request(login_session, "GET", target_url,
                 headers={**base_headers,
                           "Accept": "text/html,application/xhtml+xml",
                           "Referer": f"{CHAT_BASE}/"},
                 allow_redirects=True, max_retries=2, proxy=proxy)

    # Step 4: POST password/verify（带 Sentinel token）
    r = _request_with_sentinel_retry(
        login_session, "POST", f"{AUTH_BASE}/api/accounts/password/verify",
        sentinel_data=sentinel_data, proxy=proxy,
        json={"password": password},
        headers={**base_headers,
                 "Origin": AUTH_BASE,
                 "Referer": f"{AUTH_BASE}/log-in/password",
                 "openai-sentinel-token": sentinel_data["sentinel_token"]},
        max_retries=2)

    log(tag, f"password/verify → {r.status_code}")
    if r.status_code != 200:
        log(tag, f"FAIL: {r.text[:200]}")
        return {}

    try:
        login_continue_url = r.json().get("continue_url", "")
    except Exception:
        login_continue_url = ""

    if login_continue_url:
        r = _request(login_session, "GET", login_continue_url,
                     headers={**base_headers,
                               "Accept": "text/html,application/xhtml+xml",
                               "Referer": f"{AUTH_BASE}/"},
                     allow_redirects=True, max_retries=3, proxy=proxy)

    # Step 5: GET /api/auth/session
    r = _request(login_session, "GET", f"{CHAT_BASE}/api/auth/session",
                 headers={**base_headers, "Referer": f"{CHAT_BASE}/"},
                 max_retries=3, proxy=proxy)

    if r.status_code != 200:
        log(tag, f"FAIL: session {r.status_code}")
        return {}

    try:
        session_data = r.json()
    except Exception:
        log(tag, "FAIL: session JSON parse error")
        return {}

    access_token = session_data.get("accessToken", "")
    if not access_token:
        log(tag, "FAIL: accessToken 为空")
        return {}

    # 取 session-token cookie
    raw_session_token = ""
    try:
        raw_session_token = login_session.cookies.get("__Secure-next-auth.session-token", "")
        if not raw_session_token:
            for key in login_session.cookies:
                if "session-token" in str(key):
                    raw_session_token = login_session.cookies[key]
                    break
    except Exception:
        pass

    log(tag, f"✅ OK accessToken len={len(access_token)} session_token len={len(raw_session_token)}")
    return {"access_token": access_token, "session_token": raw_session_token}


# ── DB helpers ───────────────────────────────────────────────────────────────

def _get_target_accounts(phone_filter: str = None, force_all: bool = False) -> list[dict]:
    conn = sqlite3.connect(_db.db_path)
    conn.row_factory = sqlite3.Row
    if phone_filter:
        # 指定手机号：不管任何状态，只要有密码就尝试
        rows = conn.execute(
            "SELECT * FROM accounts WHERE phone=? AND password!='' AND password IS NOT NULL",
            (phone_filter,)
        ).fetchall()
    elif force_all:
        # 强制全量：所有有密码的账号
        rows = conn.execute(
            "SELECT * FROM accounts WHERE password!='' AND password IS NOT NULL"
        ).fetchall()
    else:
        # 默认：token 未成功 且 有密码（不限 account_status，inactive 的也试）
        rows = conn.execute(
            "SELECT * FROM accounts WHERE "
            "password!='' AND password IS NOT NULL AND "
            "(token_status != 'success' OR access_token='' OR access_token IS NULL)"
        ).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def _save_token(phone: str, access_token: str, session_token: str,
                status: str = "success", proxy_ip: str = ""):
    conn = sqlite3.connect(_db.db_path)
    # 如果有新代理也一并更新 proxy_ip
    if proxy_ip:
        conn.execute(
            """UPDATE accounts SET
                 access_token=?, session_token=?,
                 token_status=?, token_updated_at=datetime('now'),
                 proxy_ip=?, account_status='active',
                 updated_at=datetime('now')
               WHERE phone=?""",
            (access_token, session_token, status, proxy_ip, phone)
        )
    else:
        conn.execute(
            """UPDATE accounts SET
                 access_token=?, session_token=?,
                 token_status=?, token_updated_at=datetime('now'),
                 account_status='active',
                 updated_at=datetime('now')
               WHERE phone=?""",
            (access_token, session_token, status, phone)
        )
    conn.commit()
    conn.close()


def _save_fail(phone: str):
    conn = sqlite3.connect(_db.db_path)
    conn.execute(
        """UPDATE accounts SET
             token_status='refresh_failed',
             updated_at=datetime('now')
           WHERE phone=?""",
        (phone,)
    )
    conn.commit()
    conn.close()


# ── 代理获取（同主流程）───────────────────────────────────────────────────────

def _acquire_fresh_proxy(tag: str, use_proxy: bool) -> str:
    """
    与主流程 acquire_proxy() 完全一致：现取JP住宅代理，验证可达性。
    use_proxy=False 时跳过，返回空字符串（调试用）。
    """
    if not use_proxy:
        return ""
    log(tag, "获取 JP 住宅代理 ...")
    try:
        proxy = acquire_proxy(max_retries=6)
        log(tag, f"代理 OK: {proxy[:50]}...")
        return proxy
    except Exception as e:
        log(tag, f"代理获取失败: {e}，尝试无代理继续")
        return ""


# ── 单账号任务 ───────────────────────────────────────────────────────────────

def _process_one(acct: dict, sentinel_data: dict, use_proxy: bool = True) -> tuple[str, bool]:
    phone    = acct["phone"]
    password = acct["password"]
    tag      = phone[-8:]

    if not password:
        log(tag, "SKIP: 无密码记录")
        return phone, False

    log(tag, f"开始 phone={phone}")

    # 每个账号现取新代理（旧代理 proxy_ip 是注册时的，可能已过期）
    proxy = _acquire_fresh_proxy(tag, use_proxy)

    try:
        result = relogin(phone, password, proxy=proxy, sentinel_data=sentinel_data)
    except Exception as e:
        log(tag, f"EXCEPTION: {e}")
        result = {}

    if result.get("access_token"):
        _save_token(phone, result["access_token"], result.get("session_token", ""), proxy_ip=proxy)
        return phone, True
    else:
        _save_fail(phone)
        return phone, False


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="批量补取/刷新历史账号 OAuth token")
    parser.add_argument("--phone",    default="", help="仅补取指定手机号")
    parser.add_argument("--all",      action="store_true", help="强制刷新所有有密码的账号")
    parser.add_argument("--workers",  type=int, default=1, help="并发数（默认1，建议≤3）")
    parser.add_argument("--no-proxy", action="store_true", help="跳过代理（调试用）")
    args = parser.parse_args()

    use_proxy = not args.no_proxy

    accounts = _get_target_accounts(
        phone_filter=args.phone or None,
        force_all=args.all
    )

    if not accounts:
        print("没有需要补取的账号（token_status 已为 success 或无密码记录）。")
        return

    print(f"\n{'='*60}")
    print(f"待补取账号: {len(accounts)} 条  并发数: {args.workers}  使用代理: {use_proxy}")
    for a in accounts:
        print(f"  {a['phone']}  token={a.get('token_status','?')}  status={a.get('account_status','?')}")
    print(f"{'='*60}\n")

    # 预取一次 Sentinel token（所有串行任务共享，节省时间）
    # 注意：Sentinel 不依赖代理，可以直连
    print("[INIT] 预取 Sentinel token ...")
    try:
        sentinel_data = extract_sentinel()
        print(f"[INIT] Sentinel OK\n")
    except Exception as e:
        print(f"[INIT] Sentinel 获取失败: {e}，将每个账号单独获取")
        sentinel_data = None

    ok_list, fail_list = [], []

    if args.workers <= 1:
        for acct in accounts:
            phone, ok = _process_one(acct, sentinel_data, use_proxy=use_proxy)
            (ok_list if ok else fail_list).append(phone)
            if ok:
                time.sleep(3)   # 成功后稍微间隔，避免触发速率限制
    else:
        with ThreadPoolExecutor(max_workers=args.workers) as pool:
            futures = {
                pool.submit(_process_one, acct, sentinel_data, use_proxy): acct["phone"]
                for acct in accounts
            }
            for fut in as_completed(futures):
                phone, ok = fut.result()
                (ok_list if ok else fail_list).append(phone)

    print(f"\n{'='*60}")
    print(f"完成！成功: {len(ok_list)}  失败: {len(fail_list)}")
    if ok_list:
        print(f"✅ 成功: {ok_list}")
    if fail_list:
        print(f"❌ 失败: {fail_list}")
    print(f"{'='*60}\n")


if __name__ == "__main__":
    main()
