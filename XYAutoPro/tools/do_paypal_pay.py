"""
tools/do_paypal_pay.py
======================
PayPal 支付完整流程（gen_stripe_url 的后置）：

  1. 从 accounts 表取 top1 未付款账号
  2. 用 JP 代理生成 0 元 Stripe hosted 长链接，确认 due=0 后暂存（前置门槛）
  3. 从 paypal_phone 表取 use_count 最小的 active 接码号
  4. 从 card 表取 top1 指定类型信用卡（默认 Visa）
  5. 单独申请 US 支付代理（与生成链接的 JP 代理隔离）
  6. 调用 paypal_node_rpa.js 用 US 代理打开长链接完成 PayPal 浏览器支付
  7. 更新 accounts.payment_status / paypal_phone.use_count + last_otp_status

双代理设计：
  - 生成长链接 → JP 代理（warm-up 对 chatgpt.com 友好，反欺诈分高）
  - 打开链接/支付 → US 代理（账单 US 区，US 出口匹配，降低 Stripe/PayPal 风控）

用法:
    python tools/do_paypal_pay.py                        # 全默认（JP生成 / US支付）
    python tools/do_paypal_pay.py --phone +573113106370  # 指定账号
    python tools/do_paypal_pay.py --card-type JCB        # 指定信用卡类型
    python tools/do_paypal_pay.py --headless             # 无头模式
    python tools/do_paypal_pay.py --no-pay               # 只生成链接不支付
    python tools/do_paypal_pay.py --proxy-country US     # 改生成代理出口国
    python tools/do_paypal_pay.py --pay-proxy-country JP # 改支付代理出口国
"""

import sys
import os
import re
import json
import time
import sqlite3
import argparse
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

# paypal_node_rpa.js 路径
PAYMENT_ROOT = ROOT.parent / "Gpt-Agreement-Payment-main"
RPA_SCRIPT   = PAYMENT_ROOT / "CTF-pay" / "scripts" / "paypal_node_rpa.js"
RPA_CWD      = PAYMENT_ROOT          # node 脚本要求仓库根目录作为 cwd

DB_PATH = ROOT / "data" / "phone_records.db"
TMP_DIR = ROOT / "tmp"
TMP_DIR.mkdir(exist_ok=True)


def _find_chromium() -> str:
    """定位本机 Chrome/Chromium 可执行文件。
    RPA 脚本的 findChromiumExecutable 只覆盖 Linux 路径，Windows 下需通过
    PPS_CHROMIUM_EXECUTABLE 环境变量注入本机 Chrome。"""
    env = os.environ.get("PPS_CHROMIUM_EXECUTABLE")
    if env and Path(env).exists():
        return env
    candidates = [
        r"C:\Program Files\Google\Chrome\Application\chrome.exe",
        r"C:\Program Files (x86)\Google\Chrome\Application\chrome.exe",
        str(Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "Application" / "chrome.exe"),
        r"C:\Program Files\Microsoft\Edge\Application\msedge.exe",
    ]
    for c in candidates:
        if c and Path(c).exists():
            return c
    return ""


def log(tag: str, msg: str):
    print(f"[{time.strftime('%H:%M:%S')}][{tag}] {msg}", flush=True)


# ── DB 工具 ──────────────────────────────────────────────────────────────────

def _conn():
    c = sqlite3.connect(str(DB_PATH))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA journal_mode=WAL")
    return c


def pick_account(phone: str = "") -> dict:
    conn = _conn()
    try:
        if phone:
            row = conn.execute(
                "SELECT id, phone, name, access_token, session_token, payment_status "
                "FROM accounts WHERE phone = ? LIMIT 1", (phone,)
            ).fetchone()
        else:
            row = conn.execute(
                "SELECT id, phone, name, access_token, session_token, payment_status "
                "FROM accounts "
                "WHERE token_status = 'success' "
                "  AND (payment_status IS NULL OR payment_status != 'success') "
                "  AND access_token != '' "
                "ORDER BY id DESC LIMIT 1"
            ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def pick_paypal_phone() -> dict:
    conn = _conn()
    try:
        row = conn.execute(
            "SELECT id, phone, sms_url FROM paypal_phone "
            "WHERE status = 'active' ORDER BY use_count ASC, id ASC LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def pick_card(card_type: str = "Visa") -> dict:
    conn = _conn()
    try:
        _ensure_card_status(conn)
        row = conn.execute(
            "SELECT id, card_type, card_number, cvv, expires FROM card "
            "WHERE card_type = ? AND (status IS NULL OR status = 'active') "
            "ORDER BY (CASE "
            "  WHEN card_number LIKE '403657%' THEN 0 "
            "  WHEN card_number LIKE '404068%' THEN 0 "
            "  WHEN card_number LIKE '411704%' THEN 0 "
            "  WHEN card_number LIKE '510340%' THEN 0 "
            "  WHEN card_number LIKE '511282%' THEN 0 "
            "  WHEN card_number LIKE '511631%' THEN 0 "
            "  WHEN card_number LIKE '523480%' THEN 0 "
            "  WHEN card_number LIKE '537955%' THEN 0 "
            "  WHEN card_number LIKE '547400%' THEN 0 "
            "  WHEN card_number LIKE '549455%' THEN 0 "
            "  WHEN card_number LIKE '554595%' THEN 0 "
            "  WHEN card_number LIKE '555751%' THEN 0 "
            "  ELSE 1 "
            "END) ASC, id ASC LIMIT 1",
            (card_type,)
        ).fetchone()
        return dict(row) if row else {}
    finally:
        conn.close()


def _ensure_card_status(conn):
    """card 表补 status 字段（兼容旧库）。"""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(card)").fetchall()]
    if "status" not in cols:
        conn.execute("ALTER TABLE card ADD COLUMN status TEXT DEFAULT 'active'")
        conn.execute("ALTER TABLE card ADD COLUMN last_error TEXT DEFAULT ''")
        conn.commit()


def disable_card(card_id: int, reason: str):
    """禁用坏卡（风控/拒付），下次 pick_card 跳过。"""
    conn = _conn()
    try:
        _ensure_card_status(conn)
        conn.execute(
            "UPDATE card SET status = 'disabled', last_error = ? WHERE id = ?",
            (reason[:120], card_id)
        )
        conn.commit()
    finally:
        conn.close()


def disable_paypal_phone(pp_id: int, reason: str):
    """禁用坏号（已注册过 PayPal 账号），下次 pick 跳过。"""
    conn = _conn()
    try:
        conn.execute(
            "UPDATE paypal_phone SET status = 'disabled', last_otp_status = ? WHERE id = ?",
            (reason[:120], pp_id)
        )
        conn.commit()
    finally:
        conn.close()


# PayPal 风控错误 → 处置动作映射
def classify_paypal_error(stderr_text: str, result_error: str) -> dict:
    """从 node 日志 + result.error 识别 PayPal 风控根因，返回处置建议。
    返回 {bad_card, bad_phone, reason}。

    优先级：号风控 > 卡风控（RESTRICTED_USER/R_ERROR 必须在 generic-error 之前判断）
    """
    blob = f"{stderr_text}\n{result_error}"
    out = {"bad_card": False, "bad_phone": False, "reason": result_error or ""}

    # ── 号/账号风控（优先于卡，避免被 generic-error 误判为坏卡）────────────────
    # RESTRICTED_USER  : PayPal 限制该用户（接码号/邮箱/IP 被封禁），错误码 base64=UkVTVFJJQ1RFRF9VU0VS
    # ACCOUNT_ALREADY_EXISTS : 该接码号已注册过 PayPal 账号
    # R_ERROR          : 通常与 RESTRICTED_USER 同时出现的 GraphQL 顶层错误
    _PHONE_PAT = re.compile(
        r"RESTRICTED_USER|UkVTVFJJQ1RFRF9VU0VS|ACCOUNT_ALREADY_EXISTS",
        re.I,
    )
    m_phone = _PHONE_PAT.search(blob)
    if m_phone:
        out["bad_phone"] = True
        out["reason"] = m_phone.group(0).upper()
        # RESTRICTED_USER 是账号/号风控，不是卡问题，直接返回，跳过卡判断
        return out

    # ── 卡相关风控 ──────────────────────────────────────────────────────────────
    # OAS_ERROR / UNMAPPED_OAS_ERROR / paypal generic-error 是宽泛匹配，
    # 只在排除号风控之后才归入 bad_card
    if re.search(
        r"INSTRUMENT_SHARING_LIMIT_EXCEEDED|ISSUER_DECLINE|"
        r"CARD_GENERIC_ERROR|CC_LINKED_TO_FULL_ACCOUNT|"
        r"CARD_DENIED|FUNDING_INSTRUMENT|"
        r"OAS_ERROR|UNMAPPED_OAS_ERROR|paypal generic-error|CARD_INVALID",
        blob, re.I,
    ):
        out["bad_card"] = True
        m = re.search(
            r"(INSTRUMENT_SHARING_LIMIT_EXCEEDED|ISSUER_DECLINE|"
            r"CARD_GENERIC_ERROR|CC_LINKED_TO_FULL_ACCOUNT|CARD_DENIED|"
            r"OAS_ERROR|UNMAPPED_OAS_ERROR|CARD_INVALID)",
            blob, re.I,
        )
        if m:
            out["reason"] = m.group(1)
        elif "paypal generic-error" in blob.lower():
            out["reason"] = "paypal_generic_error_card_rejected"

    return out


def update_payment_status(phone: str, status: str):
    conn = _conn()
    try:
        conn.execute(
            "UPDATE accounts SET payment_status = ?, "
            "payment_updated_at = datetime('now'), updated_at = datetime('now') "
            "WHERE phone = ?",
            (status, phone)
        )
        conn.commit()
    finally:
        conn.close()


def update_paypal_phone_after_use(pp_id: int, otp: str, otp_status: str):
    conn = _conn()
    try:
        conn.execute(
            "UPDATE paypal_phone SET use_count = use_count + 1, "
            "last_used_at = datetime('now'), last_otp = ?, last_otp_status = ? "
            "WHERE id = ?",
            (otp, otp_status, pp_id)
        )
        conn.commit()
    finally:
        conn.close()


# ── 生成 Stripe 长链接（复用 gen_stripe_url 的核心逻辑） ──────────────────────

_GEN_MOD = None

def _gen_mod():
    """懒加载 gen_stripe_url.py 模块（只加载一次），复用其代理/warm-up/checkout 函数。"""
    global _GEN_MOD
    if _GEN_MOD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "gen_stripe_url",
            ROOT / "tools" / "gen_stripe_url.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _GEN_MOD = mod
    return _GEN_MOD


_FETCH_MOD = None

def _fetch_mod():
    """懒加载 fetch_cards.py 模块。"""
    global _FETCH_MOD
    if _FETCH_MOD is None:
        import importlib.util
        spec = importlib.util.spec_from_file_location(
            "fetch_cards",
            ROOT / "tools" / "fetch_cards.py",
        )
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
        _FETCH_MOD = mod
    return _FETCH_MOD


def trigger_refetch_cards(target_types: list[str], count: int = 20) -> bool:
    """当遇到卡片问题或卡片用尽时，重新从 API 抓取或本地生成新鲜信用卡存入 DB。"""
    log("REFETCH", f"开始重新拉取卡号，目标类型: {target_types} ...")
    try:
        fc = _fetch_mod()
    except Exception as e:
        log("REFETCH", f"❌ 无法加载 fetch_cards 模块: {e}")
        return False
    
    # 1. 尝试从 API 在线抓取
    try:
        log("REFETCH", f"尝试从 API 在线拉取 {count} 张卡 ...")
        api_cards = fc.fetch_cards(count)
        if api_cards:
            added, skipped = fc.save_cards(api_cards, set(target_types), batch_id=999)
            log("REFETCH", f"✓ API 抓取成功，新增 {added} 张，去重跳过 {skipped} 张")
            if added > 0:
                return True
    except Exception as e:
        log("REFETCH", f"⚠ API 抓取未成功: {e}，将降级为本地真实 BIN Luhn 算法生成...")

    # 2. 兜底方案：本地真实 BIN Luhn 生成
    try:
        log("REFETCH", f"正在本地为每种类型 {target_types} 生成 5 张卡号 ...")
        local_cards = fc.generate_local(target_types, count=5)
        if local_cards:
            added, skipped = fc.save_cards(local_cards, set(target_types), batch_id=999)
            log("REFETCH", f"✓ 本地 Luhn 生成成功，新增 {added} 张，去重跳过 {skipped} 张")
            return True
    except Exception as e:
        log("REFETCH", f"❌ 本地生成也失败: {e}")
        
    return False


def _gen_checkout_url(acct: dict, proxy_country: str, args):
    """生成 0 元 Stripe 长链接（带代理/403 自动重试机制，最多尝试 5 次）。"""
    mod = _gen_mod()
    tag = acct["phone"][-8:]
    
    MAX_GEN_RETRIES = 5
    for attempt in range(1, MAX_GEN_RETRIES + 1):
        log(tag, f"开始第 {attempt}/{MAX_GEN_RETRIES} 次生成长链接尝试 ...")
        try:
            log(tag, f"申请 {proxy_country} 代理 ...")
            proxy_url, ip_info = mod.acquire_valid_proxy(proxy_country, max_retries=args.max_proxy_retries)
        except Exception as e:
            log(tag, f"❌ 申请 {proxy_country} 代理异常: {e}")
            continue
            
        s = mod.build_authed_session(
            access_token=acct["access_token"],
            session_token=acct.get("session_token") or "",
            proxy_url=proxy_url,
        )

        log(tag, "warm-up（6 个 GET）...")
        warm_ok = mod.warmup_chatgpt(s, args.country)
        if warm_ok == 0:
            log(tag, "❌ warm-up 全部被 OpenAI 风控 403 阻断，该 IP 不合格，将更换代理重试...")
            continue

        log(tag, f"POST /payments/checkout ({args.country}/{args.currency}/{args.promo}) ...")
        result = mod.fetch_hosted_checkout_url(s, args.country, args.currency, args.promo)
        if not result.get("ok"):
            log(tag, f"❌ POST /payments/checkout 失败: {result.get('error')}，更换代理重试...")
            continue

        raw = result.get("raw") or {}
        cs_id = (raw.get("checkout_session_id") or "").strip()
        pk    = (raw.get("publishable_key") or "").strip()

        # 二次拉 Stripe 确认 0 元
        due = None
        if cs_id and pk:
            pp = mod.fetch_stripe_amount(proxy_url, cs_id, pk)
            if pp.get("ok"):
                due = pp.get("due")
                log(tag, f"Stripe 二次确认: due={due} {pp.get('currency')}")
        if due is None:
            log(tag, "⚠ 无法确认账单金额，继续（人工核查）")
        elif due != 0:
            log(tag, f"⚠ amount_due={due}，promo 未命中，终止此 IP")
            continue

        # 生成成功，直接返回结果
        return result["checkout_url"], proxy_url, ip_info

    # 达到重试上限
    log(tag, f"❌ 连续 {MAX_GEN_RETRIES} 次换代理尝试，长链接生成最终失败")
    return None, None, None


def acquire_pay_proxy(pay_country: str, max_retries: int = 6):
    """为打开长链接/PayPal 支付单独申请一个出口代理（默认 US）。
    与生成长链接用的 JP 代理隔离：账单是 US 区，支付页用 US 出口更匹配、降低风控。
    返回 (proxy_url, ip_info) 或 (None, None)。"""
    mod = _gen_mod()
    log("PAY-PROXY", f"为支付阶段申请 {pay_country} 代理 ...")
    try:
        proxy_url, ip_info = mod.acquire_valid_proxy(pay_country, max_retries=max_retries)
    except Exception as e:
        log("PAY-PROXY", f"❌ 申请 {pay_country} 代理失败: {e}")
        return None, None
    log("PAY-PROXY", f"✓ 支付代理: {ip_info.get('ip')} "
                     f"({ip_info.get('country')} / {ip_info.get('city')} / {ip_info.get('isp')})")
    return proxy_url, ip_info


# ── 调用 paypal_node_rpa.js ──────────────────────────────────────────────────

def run_rpa(
    checkout_url: str,
    phone: str,
    sms_url: str,
    card: dict,
    proxy_url: str,
    headless: bool = False,
    timeout_ms: int = 600_000,
) -> dict:
    """启动 paypal_node_rpa.js，stdin 注入 payload，返回解析后的 result dict。"""
    if not RPA_SCRIPT.exists():
        return {"success": False, "error": f"RPA 脚本不存在: {RPA_SCRIPT}"}

    # 信用卡有效期：API 返回 MM/YYYY，RPA 需要 MM/YY
    raw_exp = card.get("expires", "")      # e.g. "12/2027"
    parts = raw_exp.split("/")
    if len(parts) == 2 and len(parts[1]) == 4:
        card_expiry = f"{parts[0]}/{parts[1][2:]}"   # "12/27"
    else:
        card_expiry = raw_exp

    # Windows 合法的临时 profile 目录（RPA 默认用 /tmp/... 在 Windows 非法）
    import tempfile
    profile_dir = tempfile.mkdtemp(prefix="paypal_rpa_")

    payload = {
        "checkoutUrl": checkout_url,
        "phone":       phone,
        "smsApiUrl":   sms_url,
        "cardNumber":  card.get("card_number", ""),
        "cardExpiry":  card_expiry,
        "cardCvv":     card.get("cvv", ""),
        "proxy":       proxy_url or "",
        "headless":    headless,
        "profileDir":  profile_dir,
        "timeoutMs":   timeout_ms,
        "otpTimeoutMs": 180_000,
        "expectedDueCents": 0,
    }

    node_bin = "node"
    cmd = [node_bin, str(RPA_SCRIPT)]

    # 注入 Chrome 路径（RPA 脚本的浏览器查找只覆盖 Linux，Windows 必须显式指定）
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    # 覆盖 node 端落盘根目录（/tmp 在 Windows 不可用）→ 快照/result.json 落到 tmp/
    rpa_tmp = TMP_DIR / "rpa_artifacts"
    rpa_tmp.mkdir(exist_ok=True)
    env["PPS_TMP_DIR"] = str(rpa_tmp).replace("\\", "/")
    chromium = _find_chromium()
    if chromium:
        env["PPS_CHROMIUM_EXECUTABLE"] = chromium
        log("RPA", f"  使用 Chromium: {chromium}")
    else:
        log("RPA", "  ⚠ 未找到本机 Chrome/Chromium，RPA 可能无法启动浏览器")

    log("RPA", f"启动 paypal_node_rpa.js  phone={phone}  card={card.get('card_number','')[-4:]}")
    log("RPA", f"  checkoutUrl={checkout_url[:60]}...")
    log("RPA", f"  proxy={proxy_url or '(无)'}")

    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(RPA_CWD),
            env=env,
            bufsize=1,  # 行缓冲，便于实时读取
        )
    except Exception as e:
        return {"success": False, "error": f"启动 RPA 失败: {e}"}

    # 实时透传 node stderr（边跑边打印完整日志，并落盘到 tmp/）
    import threading
    log_path = TMP_DIR / f"rpa_{int(time.time())}.log"
    stderr_lines: list[str] = []

    def _pump_stderr():
        try:
            with open(log_path, "w", encoding="utf-8") as lf:
                for raw in proc.stderr:
                    line = raw.rstrip("\n")
                    stderr_lines.append(line)
                    lf.write(line + "\n")
                    lf.flush()
                    if line.strip():
                        log("RPA", f"  [node] {line}")
        except Exception:
            pass

    t = threading.Thread(target=_pump_stderr, daemon=True)
    t.start()

    # 写入 stdin payload，等待进程结束
    stdout_data = ""
    try:
        proc.stdin.write(json.dumps(payload, ensure_ascii=False))
        proc.stdin.close()
        stdout_data = proc.stdout.read()
        proc.wait(timeout=timeout_ms // 1000 + 30)
    except subprocess.TimeoutExpired:
        proc.kill()
        return {"success": False, "error": "RPA 进程超时"}
    except Exception as e:
        return {"success": False, "error": f"RPA 执行异常: {e}"}
    finally:
        t.join(timeout=5)

    log("RPA", f"  node 完整日志: {log_path}")

    # 解析 stdout JSON
    raw_out = (stdout_data or "").strip()
    if not raw_out:
        # node 端 result.json 落盘在 PPS_TMP_DIR（rpa_artifacts）
        for fb in (TMP_DIR / "rpa_artifacts" / "paypal_node_rpa_result.json",
                   TMP_DIR / "paypal_node_rpa_result.json"):
            if fb.exists():
                raw_out = fb.read_text(encoding="utf-8").strip()
                break
    try:
        res = json.loads(raw_out)
        res["_stderr"] = "\n".join(stderr_lines)
        res["_log_path"] = str(log_path)
        return res
    except Exception:
        return {"success": False,
                "error": f"RPA 输出解析失败（见 {log_path}）",
                "_stderr": "\n".join(stderr_lines),
                "_log_path": str(log_path)}


# ── 主入口 ───────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PayPal 支付全流程（Stripe 长链接 → RPA 支付）")
    parser.add_argument("--phone", default="", help="指定账号手机号；不填则取 top1 未付款账号")
    parser.add_argument("--card-type", default="Visa", help="信用卡类型（Visa/JCB，默认 Visa）")
    parser.add_argument("--proxy-country", default="JP", help="生成长链接用的代理出口国（默认 JP）")
    parser.add_argument("--pay-proxy-country", default="US", help="打开长链接/支付用的代理出口国（默认 US）")
    parser.add_argument("--country",  default="US",  help="账单国家（默认 US）")
    parser.add_argument("--currency", default="USD", help="账单货币（默认 USD）")
    parser.add_argument("--promo",    default="plus-1-month-free", help="优惠码")
    parser.add_argument("--max-proxy-retries", type=int, default=6)
    parser.add_argument("--headless", action="store_true", help="RPA 无头模式")
    parser.add_argument("--no-pay",   action="store_true", help="只生成链接，不启动 RPA")
    parser.add_argument("--timeout",  type=int, default=600, help="RPA 总超时秒数（默认 600）")
    args = parser.parse_args()

    print("=" * 70)
    print(f"  do_paypal_pay: 代理={args.proxy_country} / {args.country}-{args.currency} / {args.promo}")
    print("=" * 70)

    # ── 1. 取账号 ────────────────────────────────────────────────────────────
    log("DB", "查询账号 ...")
    acct = pick_account(args.phone)
    if not acct:
        log("DB", "❌ 未找到符合规则账号")
        sys.exit(1)
    tag = acct["phone"][-8:]
    log("DB", f"✓ id={acct['id']} phone={acct['phone']} name={acct.get('name') or '?'}")
    if not acct.get("access_token"):
        log("DB", "❌ 账号缺 access_token")
        sys.exit(1)

    # ── 2. 生成 Stripe 0 元长链接（前置门槛：失败则不消耗号/卡）────────────────
    #      生成阶段用 JP 代理（warm-up 友好）；成功后将长链接暂存，支付阶段另换 US 代理
    checkout_url, gen_proxy_url, gen_ip = _gen_checkout_url(acct, args.proxy_country, args)
    if not checkout_url:
        log(tag, "❌ 长链接生成失败，终止")
        sys.exit(2)

    log(tag, f"✓ Checkout URL 已暂存（生成出口: {gen_ip.get('ip')} {gen_ip.get('country')}）")
    log(tag, f"  {checkout_url[:80]}...")

    if args.no_pay:
        print()
        print("=" * 70)
        print("  --no-pay 模式，长链接如下（不执行 RPA，不消耗号/卡）:")
        print("=" * 70)
        print(f"  {checkout_url}")
        return

    # ── 3. 取接码号（仅在进入 PayPal 流程时才占用）────────────────────────────
    log("DB", "查询接码号 ...")
    pp = pick_paypal_phone()
    if not pp:
        log("DB", "❌ paypal_phone 表无可用号码，请先用 import_paypal_phones.py 导入")
        sys.exit(1)
    log("DB", f"✓ 接码号: {pp['phone']}  sms_url={pp['sms_url'][:60]}...")

    # ── 5. 申请支付出口代理（默认 US，同一轮复用）────────────────────────────
    pay_proxy_url, pay_ip = acquire_pay_proxy(args.pay_proxy_country, max_retries=args.max_proxy_retries)
    if not pay_proxy_url:
        log(tag, f"❌ 支付代理（{args.pay_proxy_country}）申请失败，终止")
        sys.exit(2)

    # ── 4+6. 取卡 + RPA 支付（拒卡时换卡重试）──────────────────────────────────
    # 策略：优先当前指定的 card-type，然后依次交替尝试 Visa, MasterCard, Discover, AMEX, JCB，最多 10 次
    ALL_TYPES = ["Visa", "MasterCard", "Discover", "AmericanExpress", "JCB"]
    MAX_CARD_RETRIES = 10
    
    # 构造卡种循环列表，优先用户指定的 card_type，然后是其他所有类型
    other_types = [t for t in ALL_TYPES if t != args.card_type]
    card_type_cycle = ([args.card_type] + other_types) * ((MAX_CARD_RETRIES + len(ALL_TYPES) - 1) // len(ALL_TYPES))
    card_type_cycle = card_type_cycle[:MAX_CARD_RETRIES]

    result = None
    card = None
    for card_attempt, cur_card_type in enumerate(card_type_cycle, start=1):
        log("DB", f"查询 {cur_card_type} 信用卡（第 {card_attempt}/{MAX_CARD_RETRIES} 次）...")
        card = pick_card(cur_card_type)
        if not card:
            log("DB", f"❌ card 表无更多可用 {cur_card_type} 卡，尝试重新拉取卡号...")
            # 实时补充当前类型的卡号
            trigger_refetch_cards([cur_card_type], count=15)
            card = pick_card(cur_card_type)
            
        if not card:
            log("DB", f"❌ 重新拉取后依然无可用 {cur_card_type} 卡，跳过此类型")
            continue
            
        log("DB", f"✓ 卡号: xxxx-xxxx-xxxx-{card['card_number'][-4:]}  "
                  f"exp={card['expires']}  cvv=***  type={cur_card_type}")

        result = run_rpa(
            checkout_url=checkout_url,
            phone=pp["phone"],
            sms_url=pp["sms_url"],
            card=card,
            proxy_url=pay_proxy_url,
            headless=args.headless,
            timeout_ms=args.timeout * 1000,
        )

        success = result.get("success") is True
        error   = result.get("error", "")

        if success:
            break

        # 失败：先分类风控原因
        diag = classify_paypal_error(result.get("_stderr", ""), error)

        if diag["bad_card"]:
            disable_card(card["id"], diag["reason"])
            
            # 【新增：如果提示卡的问题，重新拉取所有类型卡号补充卡池】
            log(tag, f"⚠ 卡片风控拒绝，立即重新拉取各类型卡号补充卡池...")
            trigger_refetch_cards(ALL_TYPES, count=20)
            
            more = card_attempt < MAX_CARD_RETRIES
            log(tag, f"⚠ 卡 xxxx-{card['card_number'][-4:]}({cur_card_type}) 已禁用（{diag['reason']}）"
                     f"{'，换卡重试' if more else '，已达重试上限'}")
            if more:
                continue
            else:
                break

        # 非拒卡错误（号风控 / 网络 / 其他）→ 不换卡，直接终止本轮
        if diag["bad_phone"]:
            log(tag, f"⚠ 接码号 {pp['phone']} 风控，终止本轮")
        else:
            log(tag, f"  非拒卡错误（{error[:60]}），终止本轮")
        break

    if result is None:
        log(tag, "❌ 无可用卡，终止")
        sys.exit(1)

    success   = result.get("success") is True
    error     = result.get("error", "")
    final_url = result.get("finalUrl", "")
    diag      = classify_paypal_error(result.get("_stderr", ""), error)

    print()
    print("=" * 70)
    print("  PayPal 支付成功" if success else "  PayPal 支付失败")
    print("=" * 70)
    print(f"  账号 phone : {acct['phone']}")
    print(f"  接码号    : {pp['phone']}")
    print(f"  信用卡    : xxxx-{card['card_number'][-4:]} ({card.get('card_type', args.card_type)})")
    print(f"  生成出口  : {gen_ip.get('ip')} ({gen_ip.get('country')})")
    print(f"  支付出口  : {pay_ip.get('ip')} ({pay_ip.get('country')})")
    print(f"  结果      : {'✓ 成功' if success else f'✗ 失败 ({error})'}")
    if final_url:
        print(f"  finalUrl  : {final_url[:100]}")
    print("=" * 70)

    # ── 7. 回写 DB ───────────────────────────────────────────────────────────
    if success:
        update_payment_status(acct["phone"], "success")
        log(tag, "✓ accounts.payment_status → success")
        update_paypal_phone_after_use(pp["id"], otp="", otp_status="used")
    else:
        update_payment_status(acct["phone"], "failed")
        log(tag, f"✗ accounts.payment_status → failed  error={error}")
        update_paypal_phone_after_use(pp["id"], otp="", otp_status=f"failed:{error[:50]}")

        if diag["bad_phone"]:
            disable_paypal_phone(pp["id"], diag["reason"])
            log(tag, f"⚠ 接码号 {pp['phone']} 已禁用（{diag['reason']}）")
        if not diag["bad_card"] and not diag["bad_phone"]:
            log(tag, "  （非卡/号风控，卡和号保留）")

    sys.exit(0 if success else 3)


if __name__ == "__main__":
    main()
