"""
tools/pay_stripe_card.py
========================
纯协议 Stripe hosted checkout 支付流程：
  1. 从 DB 取未支付账号 (gen_stripe_url 已生成长链接)
  2. 申请 US 住宅代理
  3. 从 card 表随机取一张 Visa/JCB
  4. 从 paypal_phone 表取一个接码号码 (最少使用优先+随机)
  5. 纯协议访问 Stripe checkout 长链接，填卡+PayPal 验证码
  6. 轮询接码 API 获取 OTP，完成支付

前置条件：
  - gen_stripe_url.py 已生成长链接并写入 DB
  - fetch_cards.py 已采集 Visa/JCB 卡到 card 表
  - sms/paypal_config.json 已配置 PayPal 接码号码
  - US 住宅代理可达

用法:
    python tools/pay_stripe_card.py                        # 取 top1 未支付账号
    python tools/pay_stripe_card.py --phone +573113106370  # 指定账号
    python tools/pay_stripe_card.py --dry-run              # 仅展示资源，不执行支付
    python tools/pay_stripe_card.py --sync-phones          # 从 paypal_config.json 同步号码到 DB
"""

import sys
import json
import time
import random
import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

from curl_cffi import requests as curl_requests

sys.path.insert(0, str(ROOT / "core"))
from phone_db import PhoneDB

DB_PATH = ROOT / "data" / "phone_records.db"
PAYPAL_CONFIG_PATH = ROOT / "sms" / "paypal_config.json"

PROXY_API_BASE = (
    "https://YOUR_PROXY_API_HOST/api/ProxyLogic/Generate"
    "?Num=1&Country={country}&Server=as&Format=0"
    "&Crc=YOUR_PROXY_CRC&Pool=1"
    "&KeyName=YOUR_PROXY_KEY_NAME&GenType=http"
    "&AppSecret=96d2b10ca34fc0fa5d71a43c25c97ca4"
)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)


def log(tag: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{tag}] {msg}", flush=True)


# ── Step 1: DB 查询 ───────────────────────────────────────────────────────────

def pick_account(db: PhoneDB, phone: str = "") -> dict:
    if phone:
        row = db.conn.execute(
            "SELECT id, phone, name, access_token, session_token, payment_status, proxy_ip "
            "FROM accounts WHERE phone = ? LIMIT 1", (phone,)
        ).fetchone()
    else:
        row = db.conn.execute(
            "SELECT id, phone, name, access_token, session_token, payment_status, proxy_ip "
            "FROM accounts "
            "WHERE token_status = 'success' "
            "  AND (payment_status IS NULL OR payment_status != 'success') "
            "  AND access_token != '' "
            "ORDER BY id DESC LIMIT 1"
        ).fetchone()
    return dict(row) if row else {}


def pick_card(db: PhoneDB, prefer_type: str = "") -> dict:
    if prefer_type:
        row = db.conn.execute(
            "SELECT id, card_type, card_number, cvv, expires FROM card "
            "WHERE card_type = ? ORDER BY RANDOM() LIMIT 1", (prefer_type,)
        ).fetchone()
    if not prefer_type or not row:
        row = db.conn.execute(
            "SELECT id, card_type, card_number, cvv, expires FROM card "
            "ORDER BY RANDOM() LIMIT 1"
        ).fetchone()
    return dict(row) if row else {}


# ── Step 2: US 代理 ──────────────────────────────────────────────────────────

def fetch_proxy(target_country: str) -> str:
    session = random.randint(100_000_000, 999_999_999)
    url = PROXY_API_BASE.format(country=target_country) + f"&session={session}"
    import requests as _req
    r = _req.get(url, timeout=15)
    text = r.text.strip()
    parts = text.split(":")
    if len(parts) != 4:
        raise RuntimeError(f"代理 API 格式异常: {text!r}")
    host, port, user, pwd = parts
    return f"http://{user}:{pwd}@{host}:{port}"


def verify_proxy(proxy_url: str, expected_country: str) -> tuple[bool, str, dict]:
    s = curl_requests.Session(impersonate="chrome")
    try:
        r = s.get("https://api.ipify.org?format=json", proxy=proxy_url, timeout=15)
        ip = r.json().get("ip", "")
    except Exception:
        return False, "", {}
    if not ip:
        return False, "", {}
    try:
        r2 = s.get(f"http://ip-api.com/json/{ip}?lang=en", timeout=10)
        info = r2.json()
    except Exception:
        return False, ip, {}
    country = info.get("countryCode", "")
    return (
        country == expected_country,
        country,
        {"ip": ip, "city": info.get("city", ""), "isp": info.get("isp", ""),
         "country": country},
    )


def acquire_us_proxy(max_retries: int = 6) -> tuple[str, dict]:
    for i in range(1, max_retries + 1):
        log("PROXY", f"[{i}/{max_retries}] 申请 US 代理 ...")
        try:
            proxy_url = fetch_proxy("US")
        except Exception as e:
            log("PROXY", f"  申请失败: {e}")
            time.sleep(2)
            continue
        is_ok, country, info = verify_proxy(proxy_url, "US")
        if not is_ok:
            log("PROXY", f"  国家校验失败 country={country or '?'} ip={info.get('ip', '?')}")
            continue
        log("PROXY", f"  ✓ US ip={info['ip']} city={info['city']} isp={info['isp']}")
        return proxy_url, info
    raise RuntimeError(f"连续 {max_retries} 次未拿到合格 US 代理")


# ── Step 3: PayPal 接码 ──────────────────────────────────────────────────────

def sync_paypal_phones(db: PhoneDB):
    if not PAYPAL_CONFIG_PATH.exists():
        log("SYNC", f"paypal_config.json 不存在: {PAYPAL_CONFIG_PATH}")
        return 0
    with open(PAYPAL_CONFIG_PATH, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    phones = cfg.get("phones", [])
    added = 0
    for line in phones:
        line = line.strip()
        if not line or "|" not in line:
            continue
        phone, sms_url = line.split("|", 1)
        phone = phone.strip()
        sms_url = sms_url.strip()
        if phone and sms_url:
            db.add_paypal_phone(phone, sms_url)
            added += 1
    log("SYNC", f"同步 {added} 个 PayPal 接码号码到 DB")
    return added


def fetch_paypal_otp(sms_url: str, timeout: int = 180, interval: int = 3) -> str:
    import requests as _req
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            r = _req.get(sms_url, timeout=10)
            text = r.text.strip()
            if text and len(text) >= 4:
                import re
                match = re.search(r'\d{4,8}', text)
                if match:
                    return match.group()
                return text
        except Exception as e:
            log("OTP", f"  轮询异常: {e}")
        remaining = int(deadline - time.time())
        log("OTP", f"  等待验证码... (剩余 {remaining}s)")
        time.sleep(interval)
    return ""


# ── Step 4: 浏览器自动化 Stripe → PayPal 支付 ───────────────────────────────

def stripe_paypal_pay(
    checkout_url: str,
    paypal_phone: dict,
    proxy_url: str,
    otp_timeout: int = 180,
    headless: bool = True,
) -> dict:
    """
    Camoufox 浏览器自动化：
      1. 打开 Stripe checkout 长链接
      2. 选 PayPal 支付方式
      3. 跳转 PayPal 登录 → 输入手机号 → 接码 → 验证
      4. 确认支付 → 回到 ChatGPT
    """
    result = {"ok": False}

    from camoufox.sync_api import Camoufox
    from camoufox import DefaultAddons

    pp_phone = paypal_phone["phone"]
    pp_sms_url = paypal_phone["sms_url"]

    proxy_parts = proxy_url.replace("http://", "").replace("https://", "").split("@")
    if len(proxy_parts) == 2:
        auth, hostport = proxy_parts
        user, pwd = auth.split(":", 1)
        px_host, px_port = hostport.split(":", 1)
        proxy_dict = {"server": f"http://{hostport}", "username": user, "password": pwd}
    else:
        proxy_dict = {"server": proxy_url}

    log("BROWSER", f"启动 Camoufox (headless={headless}) ...")

    try:
        with Camoufox(headless=headless, proxy=proxy_dict, geoip=True, exclude_addons=[DefaultAddons.UBO]) as browser:
            page = browser.new_page()

            log("BROWSER", f"打开 checkout URL ...")
            try:
                page.goto(checkout_url, timeout=120000, wait_until="domcontentloaded")
                log("BROWSER", f"  页面已加载: {page.title()[:60]}")
            except Exception as e:
                log("BROWSER", f"  页面加载异常: {e}, 尝试继续 ...")
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=15000)
                except Exception:
                    pass

            try:
                page.screenshot(path=str(ROOT / "tmp" / f"checkout_{int(time.time())}.png"))
            except Exception:
                pass

            log("BROWSER", "选择 PayPal 支付方式 ...")
            try:
                paypal_radio = page.locator('input[value="paypal"], [data-testid="paypal"]').first
                paypal_radio.wait_for(state="visible", timeout=15000)
                paypal_radio.click()
                log("BROWSER", "  ✓ PayPal 已选中")
            except Exception:
                log("BROWSER", "  未找到 PayPal radio，尝试点击 PayPal 文字/按钮 ...")
                try:
                    page.locator('text=PayPal').first.click(timeout=10000)
                    log("BROWSER", "  ✓ PayPal 文字已点击")
                except Exception:
                    try:
                        page.locator('text=Pay with PayPal').first.click(timeout=5000)
                    except Exception:
                        log("BROWSER", "  ⚠ 未找到 PayPal 选项，截图保留 ...")
                        try:
                            page.screenshot(path=str(ROOT / "tmp" / f"no_paypal_{int(time.time())}.png"))
                        except Exception:
                            pass

            time.sleep(1)

            log("BROWSER", "点击确认/提交按钮 ...")
            try:
                submit_btn = page.locator('button[type="submit"], button:has-text("Subscribe"), button:has-text("Pay"), button:has-text("Confirm")').first
                submit_btn.wait_for(state="visible", timeout=10000)
                submit_btn.click()
                log("BROWSER", "  ✓ 已点击提交")
            except Exception as e:
                log("BROWSER", f"  提交按钮未找到: {str(e)[:80]}")

            log("BROWSER", "等待 PayPal 页面跳转 ...")
            try:
                page.wait_for_url("**/paypal**", timeout=30000)
                log("BROWSER", f"  ✓ 已跳转 PayPal: {page.url[:80]}")
            except Exception:
                log("BROWSER", f"  当前 URL: {page.url[:80]}")
                try:
                    page.screenshot(path=str(ROOT / "tmp" / f"before_paypal_{int(time.time())}.png"))
                except Exception:
                    pass

            page.wait_for_load_state("networkidle", timeout=15000)
            time.sleep(2)

            log("BROWSER", f"输入 PayPal 手机号: {pp_phone} ...")
            try:
                phone_input = page.locator('input[type="tel"], input[name="phone"], input[placeholder*="phone"], input[id*="phone"]').first
                phone_input.wait_for(state="visible", timeout=15000)
                phone_input.fill(pp_phone)
                log("BROWSER", "  ✓ 手机号已填入")
                time.sleep(0.5)
                page.locator('button[type="submit"], button:has-text("Next"), button:has-text("Continue"), button:has-text("Send")').first.click()
            except Exception as e:
                log("BROWSER", f"  手机号输入: {e}, 尝试备选 ...")
                try:
                    page.locator('input').first.fill(pp_phone)
                    page.keyboard.press("Enter")
                except Exception:
                    pass

            log("BROWSER", f"轮询接码获取 OTP (timeout={otp_timeout}s) ...")
            otp = fetch_paypal_otp(pp_sms_url, timeout=otp_timeout)
            if not otp:
                result["error"] = "接码超时，未获取到 OTP"
                return result
            log("BROWSER", f"  ✓ OTP: {otp}")

            log("BROWSER", "输入验证码 ...")
            try:
                otp_input = page.locator('input[type="text"], input[name="code"], input[name="otp"], input[placeholder*="code"], input[placeholder*="verification"]').first
                otp_input.wait_for(state="visible", timeout=10000)
                otp_input.fill(otp)
                log("BROWSER", "  ✓ 验证码已填入")
                time.sleep(0.5)
                page.locator('button[type="submit"], button:has-text("Verify"), button:has-text("Confirm"), button:has-text("Continue")').first.click()
            except Exception as e:
                log("BROWSER", f"  验证码输入: {e}, 尝试直接输入 ...")
                page.keyboard.type(otp)
                page.keyboard.press("Enter")

            log("BROWSER", "等待支付确认 ...")
            try:
                page.wait_for_url("**/chatgpt.com**", timeout=60000)
                log("BROWSER", "  ✓ 已跳回 ChatGPT，支付成功")
                result["ok"] = True
                result["status"] = "complete"
            except Exception:
                page.wait_for_load_state("networkidle", timeout=30000)
                current_url = page.url
                log("BROWSER", f"  当前 URL: {current_url[:100]}")

                if "success" in current_url.lower() or "chatgpt.com" in current_url.lower():
                    result["ok"] = True
                    result["status"] = "complete"
                else:
                    try:
                        page.locator('button:has-text("Agree & Pay"), button:has-text("Complete"), button:has-text("Confirm")').first.click(timeout=10000)
                        page.wait_for_url("**/chatgpt.com**", timeout=30000)
                        result["ok"] = True
                        result["status"] = "complete"
                    except Exception:
                        result["error"] = f"支付流程未完成, URL: {current_url[:100]}"
                        result["final_url"] = current_url

    except Exception as e:
        result["error"] = f"浏览器异常: {e}"

    return result


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="纯协议 Stripe hosted checkout 支付 (US 代理 + card + PayPal 接码)"
    )
    parser.add_argument("--phone", default="", help="指定账号手机号；不填则取 top1 未支付账号")
    parser.add_argument("--prefer-card", default="", choices=("Visa", "JCB"), help="优先使用的卡类型")
    parser.add_argument("--dry-run", action="store_true", help="仅展示资源分配，不执行支付")
    parser.add_argument("--sync-phones", action="store_true", help="从 paypal_config.json 同步号码到 DB 后退出")
    parser.add_argument("--otp-timeout", type=int, default=180, help="PayPal OTP 等待超时秒数（默认 180）")
    parser.add_argument("--visible", action="store_true", help="显示浏览器窗口（默认 headless）")
    args = parser.parse_args()

    db = PhoneDB()

    if args.sync_phones:
        sync_paypal_phones(db)
        db.close()
        return

    sync_paypal_phones(db)

    log("DB", "查询账号 ...")
    acct = pick_account(db, args.phone)
    if not acct:
        log("DB", "❌ 未找到未支付账号 (token_status=success)")
        db.close()
        sys.exit(1)
    phone = acct["phone"]
    tag = phone[-8:]
    log("DB", f"✓ id={acct['id']} phone={phone} payment_status={acct.get('payment_status', '?')}")
    at = acct.get("access_token", "")
    st = acct.get("session_token", "")
    log("DB", f"  access_token:  {at}")
    log("DB", f"  session_token: {st}")

    z_session_files = list((ROOT / "data").glob("z_session_*.json"))
    if z_session_files:
        log("DB", f"  z_session 文件: {z_session_files[0]}")
    else:
        log("DB", "  z_session 文件: 无")

    card = pick_card(db, args.prefer_card)
    if not card:
        log("DB", "❌ card 表无可用卡，请先运行 fetch_cards.py 采集")
        db.close()
        sys.exit(1)
    log("DB", f"✓ 卡: {card['card_type']} ****{card['card_number'][-4:]} expires={card['expires']}")

    pp = db.pick_paypal_phone()
    if not pp:
        log("DB", "❌ 无可用 PayPal 接码号码，请配置 sms/paypal_config.json 后运行 --sync-phones")
        db.close()
        sys.exit(1)
    log("DB", f"✓ PayPal 接码: {pp['phone']} (已用 {pp['use_count']} 次)")

    if args.dry_run:
        print()
        print("=" * 60)
        print("  DRY RUN — 资源分配预览")
        print("=" * 60)
        print(f"  账号: {phone}")
        print(f"  卡号: {card['card_type']} ****{card['card_number'][-4:]} ({card['expires']})")
        print(f"  PayPal 接码: {pp['phone']}")
        print(f"  接码 URL: {pp['sms_url'][:60]}...")
        print("=" * 60)
        db.close()
        return

    log("PROXY", "申请 US 代理 ...")
    proxy_url, ip_info = acquire_us_proxy()

    access_token = acct.get("access_token", "")
    session_token = acct.get("session_token", "")

    log(tag, "生成 Stripe checkout 长链接 ...")

    auth_s = curl_requests.Session(impersonate="chrome136")
    auth_s.headers.update({
        "Authorization": f"Bearer {access_token}",
        "Accept": "*/*",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": UA,
    })
    if session_token:
        auth_s.headers["Cookie"] = f"__Secure-next-auth.session-token={session_token}"

    warmup_urls = [
        "https://chatgpt.com/",
        "https://chatgpt.com/api/auth/session",
        "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27?timezone_offset_min=-420",
    ]
    for wu in warmup_urls:
        try:
            auth_s.get(wu, proxy=proxy_url, timeout=15)
        except Exception:
            pass

    checkout_body = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": "US", "currency": "USD"},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "hosted",
        "promo_campaign": {
            "promo_campaign_id": "plus-1-month-free",
            "is_coupon_from_query_param": False,
        },
    }
    try:
        cr = auth_s.post(
            "https://chatgpt.com/backend-api/payments/checkout",
            json=checkout_body,
            proxy=proxy_url,
            timeout=30,
        )
        if cr.status_code != 200:
            log(tag, f"❌ checkout 请求失败: HTTP {cr.status_code}: {cr.text[:300]}")
            db.close()
            sys.exit(2)
        checkout_data = cr.json()
        checkout_url = (checkout_data.get("checkout_url") or checkout_data.get("url") or "").strip()
        publishable_key = checkout_data.get("publishable_key", "")
        cs_id = checkout_data.get("checkout_session_id", "")
        if not checkout_url and not cs_id:
            log(tag, f"❌ 响应缺 checkout_url 和 checkout_session_id: {checkout_data}")
            db.close()
            sys.exit(2)
        log(tag, f"✓ checkout 已获取: url={len(checkout_url)}chars pk={'✓' if publishable_key else '✗'} cs={'✓' if cs_id else '✗'}")
        print()
        print("=" * 70)
        print("  账号信息")
        print("=" * 70)
        print(f"  phone:          {phone}")
        print(f"  access_token:   {access_token[:50]}...")
        print(f"  session_token:  {session_token[:50]}...")
        print(f"  proxy:          {ip_info['ip']} ({ip_info['city']}, {ip_info['isp']})")
        print("-" * 70)
        print(f"  publishable_key: {publishable_key[:40]}...")
        print(f"  checkout_session_id: {cs_id}")
        print(f"  checkout_url:")
        print(f"  {checkout_url}")
        print("=" * 70)
        print()
    except Exception as e:
        log(tag, f"❌ checkout 异常: {e}")
        db.close()
        sys.exit(2)

    log(tag, "执行 Stripe → PayPal 浏览器支付 ...")
    pay_result = stripe_paypal_pay(
        checkout_url=checkout_url,
        paypal_phone=pp,
        proxy_url=proxy_url,
        otp_timeout=args.otp_timeout,
        headless=not args.visible,
    )

    db.update_paypal_phone_usage(
        pp["id"],
        otp=pay_result.get("otp", ""),
        otp_status="sent" if pay_result.get("ok") else "failed",
    )

    if pay_result.get("ok"):
        log(tag, "🎉 支付成功！")
        db.conn.execute(
            "UPDATE accounts SET payment_status = 'success', "
            "payment_updated_at = datetime('now'), updated_at = datetime('now') "
            "WHERE id = ?",
            (acct["id"],)
        )
        db.conn.commit()
        log(tag, "✓ DB 已更新 payment_status = success")
    else:
        err = pay_result.get('error', 'unknown')
        log(tag, f"❌ 支付失败: {err}")
        if pay_result.get("checkout_url") or checkout_url:
            url = pay_result.get("checkout_url") or checkout_url
            log(tag, f"  浏览器手动支付: {url}")
        db.conn.execute(
            "UPDATE accounts SET payment_status = 'failed', "
            "payment_updated_at = datetime('now'), updated_at = datetime('now') "
            "WHERE id = ?",
            (acct["id"],)
        )
        db.conn.commit()

    db.close()

    if not pay_result.get("ok"):
        sys.exit(1)


if __name__ == "__main__":
    main()
