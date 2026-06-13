"""
tools/gen_stripe_url.py
=======================
为已注册账号生成 Stripe hosted 长链接（Plus 计划 / 0 元优惠），并通过
Stripe payment_pages 二次验证账单金额。

流程节点：
  1. 从数据库取 top1 符合规则账号（token_status=success 且未付款）
  2. 通过代理 API 申请代理（默认 JP 出口）
  3. 验证代理出口国家匹配
  4. 验证 chatgpt.com / auth.openai.com 可达（stripe/paypal 仅软性探测）
  5. warm-up：模拟用户在 chatgpt.com 浏览（6 个 GET），拉高反欺诈评分
  6. POST /backend-api/payments/checkout（Plus / hosted / plus-1-month-free）
  7. GET api.stripe.com/v1/payment_pages/{cs_id} 二次拉取账单金额
  8. 校验 amount_due == 0，打印 hosted Stripe 长链接

关键设计：
  - curl_cffi Session 代理必须用 proxy= 参数（单数字符串），
    不能用 session.proxies = {dict}，否则代理不生效
  - warm-up 与 checkout 共享同一 Session（cookie/反欺诈状态带过去）
  - Stripe payment_pages 查询先走代理，失败自动直连回退

用法:
    python tools/gen_stripe_url.py                       # JP 出口，US/USD 账单
    python tools/gen_stripe_url.py --phone +12345678900  # 指定账号
    python tools/gen_stripe_url.py --proxy-country US    # 改代理出口国
    python tools/gen_stripe_url.py --country JP --currency JPY
    python tools/gen_stripe_url.py --dump-raw            # 落盘原始响应到 tmp/
    python tools/gen_stripe_url.py --strict-zero         # 非 0 元时退出码 4
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

# ── 常量 ─────────────────────────────────────────────────────────────────────

DB_PATH = ROOT / "data" / "phone_records.db"

DEFAULT_PROXY_COUNTRY = "JP"
DEFAULT_BILLING_COUNTRY = "US"
DEFAULT_BILLING_CURRENCY = "USD"

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

# warm-up 请求序列（照搬 gopay/_monolith._chatgpt_warmup）
WARMUP_STEPS = [
    ("home",             "https://chatgpt.com/",                                            "text/html"),
    ("auth_session",     "https://chatgpt.com/api/auth/session",                            "application/json"),
    ("accounts_check",   "https://chatgpt.com/backend-api/accounts/check/v4-2023-04-27?timezone_offset_min=-420", "application/json"),
    ("domain_density",   "https://chatgpt.com/backend-api/accounts/domain-density-eligibility",                   "application/json"),
    ("pricing_countries","https://chatgpt.com/backend-api/checkout_pricing_config/countries",                      "application/json"),
    ("pricing_config",   "https://chatgpt.com/backend-api/checkout_pricing_config/configs/{country}",             "application/json"),
]

# 硬性校验目标（全部通过才继续）
REQUIRED_TARGETS = [
    ("chatgpt.com",     "https://chatgpt.com/"),
    ("auth.openai.com", "https://auth.openai.com/"),
    ("paypal.com",    "https://www.paypal.com/"),
]
# 软性探测（失败仅打印警告，不换代理）
SOFT_TARGETS = [
    ("js.stripe.com", "https://js.stripe.com/v3/"),
]


# ── 日志 ─────────────────────────────────────────────────────────────────────

def log(tag: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{tag}] {msg}", flush=True)


# ── 节点 1: 数据库取账号 ──────────────────────────────────────────────────────

def pick_account(phone: str = "") -> dict:
    """按 phone 精准查，或按 token_status=success & 未付款 倒序取 top1。"""
    if not DB_PATH.exists():
        raise RuntimeError(f"数据库不存在: {DB_PATH}")
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        if phone:
            row = conn.execute(
                "SELECT id, phone, name, access_token, session_token, "
                "payment_status, token_status, proxy_ip "
                "FROM accounts WHERE phone = ? LIMIT 1",
                (phone,),
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, phone, name, access_token, session_token, "
                "payment_status, token_status, proxy_ip "
                "FROM accounts "
                "WHERE token_status = 'success' "
                "  AND (payment_status IS NULL OR payment_status != 'success') "
                "  AND access_token != '' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


# ── 节点 2-3: 代理申请与国家校验 ─────────────────────────────────────────────

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


def verify_proxy_country(proxy_url: str, expected: str) -> tuple[bool, str, dict]:
    s = curl_requests.Session(impersonate="chrome")
    try:
        r = s.get("https://api.ipify.org?format=json", proxy=proxy_url, timeout=15)
        ip = r.json().get("ip", "")
    except Exception:
        try:
            r = s.get("https://ifconfig.me/ip", proxy=proxy_url, timeout=15)
            ip = r.text.strip()
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
        country == expected,
        country,
        {"ip": ip, "city": info.get("city", ""), "region": info.get("regionName", ""),
         "isp": info.get("isp", ""), "country": country},
    )


# ── 节点 4: 目标可达性探测 ────────────────────────────────────────────────────

def _probe(proxy_url: str, url: str) -> tuple[bool, str]:
    try:
        s = curl_requests.Session(impersonate="chrome")
        resp = s.get(url, proxy=proxy_url, timeout=12)
        return True, f"HTTP {resp.status_code}"
    except Exception as e:
        return False, f"FAIL({type(e).__name__})"


def verify_targets_reachable(proxy_url: str) -> tuple[bool, list[str]]:
    """硬性目标全部通过才返回 True；软性目标失败仅标注 (soft)。"""
    results = []
    hard_ok = True
    for name, url in REQUIRED_TARGETS:
        ok, detail = _probe(proxy_url, url)
        results.append(f"{name}={detail}")
        if not ok:
            hard_ok = False
    for name, url in SOFT_TARGETS:
        ok, detail = _probe(proxy_url, url)
        results.append(f"{name}={detail}" + ("" if ok else "(soft, 忽略)"))
    return hard_ok, results


def get_scamalytics_score(ip: str) -> int:
    """获取 IP 的 Scamalytics 欺诈评分，失败返回 -1。"""
    try:
        # 使用 chrome136 伪装直连 Scamalytics (避免 403)
        s = curl_requests.Session(impersonate="chrome136")
        r = s.get(f"https://scamalytics.com/ip/{ip}", timeout=10)
        if r.status_code == 200:
            import re
            m = re.search(r"Fraud Score:\s*(\d+)", r.text, re.I)
            if m:
                return int(m.group(1))
    except Exception as e:
        log("PROXY-SCORE", f"⚠️ 抓取 Scamalytics 评分异常: {e}")
    return -1


def acquire_valid_proxy(target_country: str, max_retries: int = 6) -> tuple[str, dict]:
    for i in range(1, max_retries + 1):
        log("PROXY", f"[{i}/{max_retries}] 申请 {target_country} 代理 ...")
        try:
            proxy_url = fetch_proxy(target_country)
        except Exception as e:
            log("PROXY", f"  申请失败: {e}")
            time.sleep(2)
            continue
        is_ok, country, info = verify_proxy_country(proxy_url, target_country)
        if not is_ok:
            log("PROXY", f"  国家校验失败 country={country or '?'} ip={info.get('ip', '?')}，重试")
            continue

        # ── 新增：信用评分检测 ───────────────────────────────────────
        ip = info.get("ip", "")
        score = get_scamalytics_score(ip)
        if score != -1:
            log("PROXY", f"  ✓ 信用分检测: ip={ip} Scamalytics={score}/100")
            if score >= 50:
                log("PROXY", f"  ❌ 信用评分过高（{score} >= 50），视为不合格代理，立即轮换重试")
                continue
            else:
                log("PROXY", f"  ✅ 信用评分合格（{score} < 50）")
        else:
            log("PROXY", f"  ⚠️ 信用评分获取失败，将依赖原有 residential 校验规则继续")
        # ────────────────────────────────────────────────────────────

        log("PROXY", f"  ✓ {target_country} ip={info['ip']} city={info['city']} "
                     f"region={info['region']} isp={info['isp']}")
        ok, results = verify_targets_reachable(proxy_url)
        log("PROXY", "  目标可达性: " + " | ".join(results))
        if ok:
            return proxy_url, info
        log("PROXY", "  目标不可达，换代理")
    raise RuntimeError(f"连续 {max_retries} 次未拿到合格 {target_country} 代理")


# ── Session 封装（代理注入） ──────────────────────────────────────────────────

def build_authed_session(access_token: str, session_token: str, proxy_url: str):
    """构造带认证头的 curl_cffi Session。
    curl_cffi 不支持 session.proxies = {dict}，代理必须在每次请求时用 proxy= 传入。
    这里把 proxy_url 存到 _proxy_url 属性，通过 _sreq() 统一注入。"""
    s = curl_requests.Session(impersonate="chrome136")
    s.headers.update({
        "Authorization": f"Bearer {access_token}",
        "Accept": "*/*",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": UA,
        "sec-ch-ua": '"Not_A Brand";v="8", "Chromium";v="148", "Google Chrome";v="148"',
        "sec-ch-ua-mobile": "?0",
        "sec-ch-ua-platform": '"Windows"',
        "sec-fetch-dest": "empty",
        "sec-fetch-mode": "cors",
        "sec-fetch-site": "same-origin",
    })
    if session_token:
        s.headers["Cookie"] = f"__Secure-next-auth.session-token={session_token}"
    s._proxy_url = proxy_url  # type: ignore[attr-defined]
    return s


def _sreq(s, method: str, url: str, **kwargs):
    """统一请求封装：从 s._proxy_url 取代理，用 proxy= 单参数注入。"""
    proxy = getattr(s, "_proxy_url", "") or ""
    if proxy:
        kwargs.setdefault("proxy", proxy)
    return getattr(s, method)(url, **kwargs)


# ── 节点 5: warm-up ───────────────────────────────────────────────────────────

def warmup_chatgpt(s, billing_country: str) -> int:
    """模拟用户在 chatgpt.com 浏览（6 个 GET），让 OpenAI 反欺诈打 normal-user 分。
    缺这一步 → 反欺诈把 promo 阉割 → amount_due != 0。
    返回成功数，调用方据此判断是否继续。"""
    ok_count = 0
    for name, url, accept in WARMUP_STEPS:
        full_url = url.format(country=billing_country)
        try:
            r = _sreq(s, "get", full_url, headers={"Accept": accept}, timeout=20)
            log("WARM", f"  {name} → HTTP {r.status_code}")
            ok_count += 1
        except Exception as e:
            log("WARM", f"  {name} 失败: {type(e).__name__}: {str(e)[:100]}")
    return ok_count


# ── 节点 6: 生成 Stripe hosted 长链接 ────────────────────────────────────────

def fetch_hosted_checkout_url(
    s,
    billing_country: str,
    billing_currency: str,
    promo_campaign_id: str = "plus-1-month-free",
) -> dict:
    body = {
        "entry_point": "all_plans_pricing_modal",
        "plan_name": "chatgptplusplan",
        "billing_details": {"country": billing_country, "currency": billing_currency},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "hosted",
        "promo_campaign": {
            "promo_campaign_id": promo_campaign_id,
            "is_coupon_from_query_param": False,
        },
    }
    try:
        resp = _sreq(s, "post",
                     "https://chatgpt.com/backend-api/payments/checkout",
                     json=body, timeout=30)
    except Exception as e:
        return {"ok": False, "error": f"POST 异常: {type(e).__name__}: {e}"}

    # 401 → 用 session cookie 刷新 access_token 重试一次
    if resp.status_code == 401:
        log("HTTP", "  checkout 401，尝试 /api/auth/session 刷新 token")
        try:
            ar = _sreq(s, "get", "https://chatgpt.com/api/auth/session",
                       headers={"Accept": "application/json"}, timeout=20)
            if ar.status_code == 200:
                fresh = (ar.json() or {}).get("accessToken") or ""
                if fresh:
                    s.headers["Authorization"] = f"Bearer {fresh}"
                    log("HTTP", f"  token 刷新成功 (len={len(fresh)})，重试")
                    resp = _sreq(s, "post",
                                 "https://chatgpt.com/backend-api/payments/checkout",
                                 json=body, timeout=30)
        except Exception as e:
            log("HTTP", f"  刷新异常: {e}")

    if resp.status_code != 200:
        return {"ok": False, "error": f"HTTP {resp.status_code}: {(resp.text or '')[:400]}"}
    try:
        data = resp.json()
    except Exception:
        return {"ok": False, "error": f"非 JSON: {(resp.text or '')[:300]}"}

    checkout_url = (
        data.get("checkout_url") or data.get("url") or data.get("openai_checkout_url") or ""
    ).strip()
    if not checkout_url:
        return {"ok": False, "error": "响应缺 checkout_url", "raw": data}
    return {"ok": True, "checkout_url": checkout_url, "raw": data}


# ── 节点 7: Stripe payment_pages 二次验证金额 ─────────────────────────────────

def _extract_due(data: dict) -> tuple[int | None, str]:
    ts = data.get("total_summary") or {}
    inv = data.get("invoice") or {}
    currency = (data.get("currency") or inv.get("currency") or "").lower()
    candidate = ts.get("due") if ts.get("due") is not None else inv.get("amount_due")
    if candidate is None:
        return None, currency
    try:
        return int(candidate), currency
    except Exception:
        return None, currency


def fetch_stripe_amount(proxy_url: str, cs_id: str, publishable_key: str) -> dict:
    """GET api.stripe.com/v1/payment_pages/{cs_id} 取 total_summary.due。
    先走代理，CONNECT 被拦截时自动直连回退（JP 住宅代理常封 Stripe CONNECT 端口）。"""
    params = {
        "key": publishable_key,
        "_stripe_version": (
            "2025-03-31.basil; checkout_server_update_beta=v1; "
            "checkout_manual_approval_preview=v1"
        ),
        "elements_session_client[client_betas][0]": "custom_checkout_server_updates_1",
        "elements_session_client[client_betas][1]": "custom_checkout_manual_approval_1",
        "elements_session_client[elements_init_source]": "custom_checkout",
        "elements_session_client[referrer_host]": "chatgpt.com",
    }
    base_headers = {
        "Accept": "application/json",
        "Accept-Language": "en-US,en;q=0.9",
        "Origin": "https://pay.openai.com",
        "Referer": "https://pay.openai.com/",
        "User-Agent": UA,
    }
    attempts = ([("proxy", proxy_url)] if proxy_url else []) + [("direct", None)]
    last_err = ""
    for mode, p in attempts:
        s = curl_requests.Session(impersonate="chrome136")
        s.headers.update(base_headers)
        try:
            r = s.get(
                f"https://api.stripe.com/v1/payment_pages/{cs_id}",
                params=params,
                proxy=p,       # None = 直连
                timeout=30,
            )
        except Exception as e:
            last_err = f"[{mode}] {type(e).__name__}: {str(e)[:200]}"
            continue
        if r.status_code != 200:
            last_err = f"[{mode}] HTTP {r.status_code}: {(r.text or '')[:300]}"
            continue
        try:
            data = r.json() or {}
        except Exception:
            last_err = f"[{mode}] 非 JSON: {(r.text or '')[:300]}"
            continue
        due, currency = _extract_due(data)
        ts = data.get("total_summary") or {}
        return {
            "ok": True, "via": mode,
            "due": due, "currency": currency,
            "subtotal": ts.get("subtotal"), "total": ts.get("total"),
            "raw": data,
        }
    return {"ok": False, "error": last_err or "all attempts failed"}


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(
        description="生成 ChatGPT Plus Stripe hosted 长链接（JP 代理 / US-USD / 0 元优惠）"
    )
    parser.add_argument("--phone", default="", help="指定手机号；不填则取 top1 未支付账号")
    parser.add_argument("--proxy-country", default=DEFAULT_PROXY_COUNTRY,
                        help=f"代理出口国家代码（默认 {DEFAULT_PROXY_COUNTRY}）")
    parser.add_argument("--country", default=DEFAULT_BILLING_COUNTRY,
                        help=f"账单国家（默认 {DEFAULT_BILLING_COUNTRY}）")
    parser.add_argument("--currency", default=DEFAULT_BILLING_CURRENCY,
                        help=f"账单货币（默认 {DEFAULT_BILLING_CURRENCY}）")
    parser.add_argument("--promo", default="plus-1-month-free",
                        help="promo_campaign_id（默认 plus-1-month-free）")
    parser.add_argument("--max-proxy-retries", type=int, default=6,
                        help="代理申请最大重试次数（默认 6）")
    parser.add_argument("--strict-zero", action="store_true",
                        help="账单金额非 0 时以退出码 4 退出")
    parser.add_argument("--dump-raw", action="store_true",
                        help="把原始 JSON 响应落盘到 tmp/ 目录")
    args = parser.parse_args()

    print("=" * 70)
    print(f"  gen_stripe_url: 代理={args.proxy_country} / 账单={args.country}-{args.currency} / promo={args.promo}")
    print("=" * 70)

    # 节点 1
    log("DB", "查询账号 ...")
    acct = pick_account(args.phone)
    if not acct:
        log("DB", "❌ 未找到符合规则账号（token_status=success 且未付款）")
        sys.exit(1)
    phone = acct["phone"]
    tag = phone[-8:]
    log("DB", f"✓ id={acct['id']} phone={phone} name={acct.get('name') or '?'}")
    if not acct.get("access_token"):
        log("DB", "❌ 账号缺 access_token")
        sys.exit(1)

    # 节点 2-4
    log("PROXY", f"申请 {args.proxy_country} 代理 ...")
    proxy_url, ip_info = acquire_valid_proxy(args.proxy_country, max_retries=args.max_proxy_retries)

    # 构造共享 Session
    s = build_authed_session(
        access_token=acct["access_token"],
        session_token=acct.get("session_token") or "",
        proxy_url=proxy_url,
    )

    # 节点 5
    log("WARM", "warm-up（6 个 GET）...")
    warm_ok = warmup_chatgpt(s, args.country)
    if warm_ok == 0:
        log(tag, "❌ warm-up 全部失败，代理无法访问 chatgpt.com")
        sys.exit(5)
    if warm_ok < 3:
        log(tag, f"⚠ warm-up 仅 {warm_ok}/6 成功，反欺诈分可能不足")

    # 节点 6
    log(tag, f"POST /backend-api/payments/checkout ({args.country}/{args.currency}/{args.promo}) ...")
    result = fetch_hosted_checkout_url(
        s,
        billing_country=args.country,
        billing_currency=args.currency,
        promo_campaign_id=args.promo,
    )
    if not result.get("ok"):
        log(tag, f"❌ 生成失败: {result.get('error')}")
        sys.exit(2)

    checkout_url = result["checkout_url"]
    raw = result.get("raw") or {}

    if args.dump_raw:
        dump_dir = ROOT / "tmp"
        dump_dir.mkdir(parents=True, exist_ok=True)
        p = dump_dir / f"checkout_resp_{tag}_{int(time.time())}.json"
        p.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        log(tag, f"checkout 响应已落盘: {p}")

    # 节点 7: ChatGPT hosted 模式响应不带金额，去 Stripe 拉
    due, currency = _extract_due(raw)
    cs_id = (raw.get("checkout_session_id") or "").strip()
    pk = (raw.get("publishable_key") or "").strip()
    stripe_via = ""
    if due is None and cs_id and pk:
        log(tag, f"ChatGPT 响应无金额，拉 Stripe payment_pages cs={cs_id[:20]}...")
        pp = fetch_stripe_amount(proxy_url, cs_id, pk)
        if pp.get("ok"):
            due = pp.get("due")
            currency = pp.get("currency") or currency
            stripe_via = f" (via Stripe {pp['via']})"
            log(tag, f"  due={due} currency={currency!r} "
                     f"subtotal={pp.get('subtotal')} total={pp.get('total')}{stripe_via}")
            if args.dump_raw:
                pp_p = dump_dir / f"stripe_pp_{tag}_{int(time.time())}.json"
                pp_p.write_text(json.dumps(pp.get("raw") or {}, ensure_ascii=False, indent=2),
                                encoding="utf-8")
                log(tag, f"  Stripe 响应已落盘: {pp_p}")
        else:
            log(tag, f"  ⚠ Stripe payment_pages 失败: {pp.get('error')}")

    # 输出结果
    print()
    print("=" * 70)
    print("  Stripe 长链接生成成功")
    print("=" * 70)
    print(f"  账号 phone  : {phone}")
    print(f"  出口 IP     : {ip_info['ip']} ({ip_info['country']} / {ip_info['city']} / {ip_info['isp']})")
    print(f"  Plan        : Plus")
    print(f"  账单        : {args.country} / {args.currency}")
    print(f"  优惠码      : {args.promo}")
    print(f"  amount_due  : {due!r} {currency!r}{stripe_via}（最小货币单位）")
    print("-" * 70)
    print("  Checkout URL :")
    print(f"  {checkout_url}")
    print("=" * 70)

    # 节点 8: 金额校验
    if due is None:
        log(tag, "⚠ 无法获取账单金额，请人工核对长链接")
    elif due != 0:
        log(tag, f"⚠ amount_due={due}，promo 未完全命中")
        if args.strict_zero:
            sys.exit(4)
    else:
        log(tag, "✓ amount_due = 0，优惠生效")

    print()
    print("在浏览器打开上方 Checkout URL 即可进入完整 PayPal 支付流程")


if __name__ == "__main__":
    main()
