"""
tools/pay_registered_paypal.py
==============================
新工具：为已成功注册的账号生成 Stripe Element 托管支付长链接（长连接），并调用 PayPal 自动支付。

使用方法:
    # 交互式选择数据库中未支付账号进行生成长链接并支付
    python tools/pay_registered_paypal.py

    # 针对指定手机号直接生成并支付
    python tools/pay_registered_paypal.py --phone +818021257841

    # 仅生成支付长链接（不进行实际的 PayPal Playwright 浏览器支付）
    python tools/pay_registered_paypal.py --phone +818021257841 --no-pay

    # 指定自定义配置文件、订阅方案或账单国家/货币
    python tools/pay_registered_paypal.py --config ../Gpt-Agreement-Payment-main/CTF-pay/config.paypal.json --plan plus --country ID --currency IDR
"""

import sys
import os
import json
import time
import argparse
import sqlite3
import tempfile
import subprocess
from pathlib import Path

# 设置路径导入
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# 设置 UTF-8 编码输出
sys.stdout.reconfigure(encoding='utf-8', errors='replace')

# 查找 sibling 支付目录
PARENT_DIR = ROOT.parent
PAYMENT_ROOT = PARENT_DIR / "Gpt-Agreement-Payment-main"
CARD_DIR = PAYMENT_ROOT / "CTF-pay"

# 数据库路径
DB_PATH = ROOT / "data" / "phone_records.db"


def log(tag: str, msg: str):
    ts = time.strftime("%H:%M:%S")
    print(f"[{ts}][{tag}] {msg}", flush=True)


# ── Step 1: 数据库查询 ────────────────────────────────────────────────────────

def get_unpaid_accounts() -> list[dict]:
    """获取所有成功注册且未付款成功的账号"""
    if not DB_PATH.exists():
        print(f"[ERROR] 数据库不存在: {DB_PATH}")
        return []
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """SELECT id, phone, password, name, created_at, access_token, session_token, payment_status, token_status, proxy_ip 
               FROM accounts 
               WHERE token_status = 'success' AND (payment_status IS NULL OR payment_status != 'success')
               ORDER BY id DESC"""
        ).fetchall()
        return [dict(r) for r in rows]
    except sqlite3.OperationalError as e:
        print(f"[ERROR] 读取 accounts 表失败: {e}")
        return []
    finally:
        conn.close()


def get_account_by_phone(phone: str) -> dict:
    """按手机号精准查找账号"""
    if not DB_PATH.exists():
        return {}
    
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    try:
        row = conn.execute(
            """SELECT id, phone, password, name, created_at, access_token, session_token, payment_status, token_status, proxy_ip 
               FROM accounts 
               WHERE phone = ? LIMIT 1""",
            (phone,)
        ).fetchone()
        return dict(row) if row else {}
    except Exception as e:
        print(f"[ERROR] 读取指定手机号失败: {e}")
        return {}
    finally:
        conn.close()


def update_payment_status(phone: str, status: str):
    """更新账号付款状态"""
    if not DB_PATH.exists():
        return
    
    conn = sqlite3.connect(str(DB_PATH))
    try:
        conn.execute(
            """UPDATE accounts 
               SET payment_status = ?, payment_updated_at = datetime('now'), updated_at = datetime('now') 
               WHERE phone = ?""",
            (status, phone)
        )
        conn.commit()
        log(phone[-8:], f"✅ 数据库已成功更新 payment_status = {status}")
    except Exception as e:
        print(f"[ERROR] 更新付款状态失败: {e}")
    finally:
        conn.close()


# ── Step 2: 纯协议生成托管支付长链接 ───────────────────────────────────────────

def fetch_checkout_url(access_token: str, session_token: str, plan: str = "team", 
                       country: str = "IE", currency: str = "EUR", 
                       promo_campaign_id: str = "", proxy_url: str = "") -> dict:
    """
    请求 ChatGPT API 获得 Stripe Element 托管的 hosted 长链接
    """
    is_plus = "plus" in plan.lower()
    plan_name = "chatgptplusplan" if is_plus else "chatgptteamplan"
    entry_point = "all_plans_pricing_modal" if is_plus else "team_workspace_purchase_modal"
    
    if not promo_campaign_id:
        promo_campaign_id = "plus-1-month-free" if is_plus else "team-1-month-free"
        
    body = {
        "entry_point": entry_point,
        "plan_name": plan_name,
        "billing_details": {"country": country, "currency": currency},
        "cancel_url": "https://chatgpt.com/#pricing",
        "checkout_ui_mode": "hosted",
        "promo_campaign": {
            "promo_campaign_id": promo_campaign_id,
            "is_coupon_from_query_param": False,
        },
    }
    if not is_plus:
        body["team_plan_data"] = {
            "workspace_name": "MyWorkspace",
            "price_interval": "month",
            "seat_quantity": 5,
        }
        
    headers = {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
        "Accept": "*/*",
        "Origin": "https://chatgpt.com",
        "Referer": "https://chatgpt.com/",
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    }
    if session_token:
        headers["Cookie"] = f"__Secure-next-auth.session-token={session_token}"
        
    proxies = None
    if proxy_url:
        # 支持 SOCKS5
        pu = proxy_url.replace("socks5://", "socks5h://")
        proxies = {"http": pu, "https": pu}
        
    try:
        from curl_cffi import requests as curl_requests
        s = curl_requests.Session(impersonate="chrome136")
    except ImportError:
        import requests
        s = requests.Session()
        
    try:
        resp = s.post(
            "https://chatgpt.com/backend-api/payments/checkout",
            headers=headers,
            json=body,
            proxies=proxies,
            timeout=30,
        )
        if resp.status_code != 200:
            return {"ok": False, "error": f"HTTP {resp.status_code}: {resp.text[:250]}"}
        
        data = resp.json()
        checkout_url = (data.get("checkout_url") or data.get("url") or "").strip()
        if not checkout_url:
            return {"ok": False, "error": "响应数据中缺 checkout_url 字段", "raw": data}
            
        return {"ok": True, "checkout_url": checkout_url, "raw": data}
    except Exception as e:
        return {"ok": False, "error": f"发起请求发生异常: {e}"}


# ── Step 3: 调用 PayPal 支付子进程 ─────────────────────────────────────────────

def run_paypal_payment(acct: dict, checkout_url: str, base_config_path: Path, timeout: int = 600) -> bool:
    """
    通过创建临时配置文件，调用 CTF-pay/card.py 的 PayPal 自动化进行支付
    """
    phone = acct["phone"]
    tag = phone[-8:]
    
    if not CARD_DIR.exists() or not (CARD_DIR / "card.py").exists():
        log(tag, f"❌ 找不到支付模块 CTF-pay 目录: {CARD_DIR}")
        return False

    # 加载基础配置
    try:
        with open(base_config_path, "r", encoding="utf-8") as f:
            cfg = json.load(f)
    except Exception as e:
        log(tag, f"❌ 无法读取基础支付配置: {e}")
        return False
        
    # 动态注入 Access Token & Session Token 等参数
    auth = cfg.setdefault("fresh_checkout", {}).setdefault("auth", {})
    auth["mode"] = "access_token"
    auth["access_token"] = acct["access_token"]
    auth["session_token"] = acct["session_token"]
    auth["prefer_session_refresh"] = True
    
    # 禁用自动注册（已注册）
    auto = auth.setdefault("auto_register", {})
    auto["enabled"] = False
    
    # 生成临时 JSON 配置文件
    runtime_pay_dir = CARD_DIR / ".runtime"
    runtime_pay_dir.mkdir(parents=True, exist_ok=True)
    
    tmp_config = tempfile.NamedTemporaryFile(
        mode="w", suffix=".json", prefix="paypal_pay_",
        dir=str(runtime_pay_dir), delete=False, encoding="utf-8"
    )
    json.dump(cfg, tmp_config, ensure_ascii=False, indent=2)
    tmp_config.close()
    
    config_to_use = Path(tmp_config.name).resolve()
    log(tag, f"已生成临时支付配置: {config_to_use.name}")
    
    # 构造命令行
    # 采用 python -m card auto 模式运行，cwd为 CTF-pay
    cmd = [
        sys.executable, "-m", "card", "auto",
        "--config", str(config_to_use),
        "--paypal",
        "--json-result"
    ]
    
    env = dict(os.environ)
    env.pop("HTTP_PROXY", None)
    env.pop("HTTPS_PROXY", None)
    
    # 检查 codex client_id 并写入环境变量
    if not env.get("OAUTH_CODEX_CLIENT_ID"):
        client_id = cfg.get("cpa", {}).get("oauth_client_id") or cfg.get("fresh_checkout", {}).get("auth", {}).get("auto_register", {}).get("oauth_client_id", "")
        if client_id:
            env["OAUTH_CODEX_CLIENT_ID"] = client_id

    log(tag, "🚀 启动 PayPal 自动化支付流 (Playwright/Camoufox) ...")
    
    result_json = None
    success = False
    try:
        proc = subprocess.Popen(
            cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, env=env, cwd=str(CARD_DIR)
        )
        
        deadline = time.time() + timeout
        for line in proc.stdout:
            line = line.rstrip("\n")
            print(f"  [pay:{tag}] {line}")
            
            if line.startswith("CARD_RESULT_JSON="):
                payload = line.split("=", 1)[1]
                result_json = json.loads(payload)
                
            if time.time() > deadline:
                proc.kill()
                log(tag, "❌ 支付进程已运行超时，强制中止")
                break
                
        proc.wait()
        
        if result_json:
            status = result_json.get("state", "unknown")
            log(tag, f"PayPal 支付执行完成: state={status}")
            if status == "succeeded":
                success = True
        elif proc.returncode == 0:
            log(tag, "PayPal 支付执行完成 (返回值=0)")
            success = True
        else:
            log(tag, f"❌ 支付失败 (退出码={proc.returncode})")
            
    except Exception as e:
        log(tag, f"❌ 执行支付命令发生异常: {e}")
    finally:
        if config_to_use.exists():
            try:
                os.unlink(config_to_use)
            except Exception:
                pass
                
    return success


# ── 主入口与交互 ─────────────────────────────────────────────────────────────

def select_config_file() -> Path:
    """寻找最合适的 PayPal 支付配置文件"""
    candidates = [
        CARD_DIR / "config.paypal.json",
        CARD_DIR / "config.auto.json",
        CARD_DIR / "config.paypal.example.json",
    ]
    for c in candidates:
        if c.exists():
            return c.resolve()
    return candidates[0]


def main():
    parser = argparse.ArgumentParser(description="为注册成功账号生成长链接并进行 PayPal 支付")
    parser.add_argument("--phone", default="", help="指定精准支付的手机号。如果不指定，则进入交互式菜单")
    parser.add_argument("--config", default="", help="card.py 支付配置文件路径")
    parser.add_argument("--plan", choices=("team", "plus"), default=None, help="覆盖订阅方案（默认根据配置文件的 plan 推导）")
    parser.add_argument("--country", default="", help="账单国家（如 ID, IE, US），默认自动根据配置文件的 plan 匹配")
    parser.add_argument("--currency", default="", help="账单货币（如 IDR, EUR, USD），默认自动根据配置文件的 plan 匹配")
    parser.add_argument("--promo", default="", help="覆盖折扣优惠代码（Promo Campaign ID）")
    parser.add_argument("--no-pay", action="store_true", help="仅生成并打印 Stripe 托管支付长链接，不拉起 Playwright 支付")
    parser.add_argument("--timeout", type=int, default=720, help="Playwright 支付最大超时时间（秒，默认 720）")
    args = parser.parse_args()

    # 1. 选择配置文件
    config_path = Path(args.config) if args.config else select_config_file()
    if not config_path.exists():
        print(f"[ERROR] 找不到支付配置文件: {config_path}")
        print("请在 Gpt-Agreement-Payment-main/CTF-pay 目录下准备好 config.paypal.json 文件")
        sys.exit(1)
        
    print(f"[INIT] 使用基础支付配置: {config_path.name}")
    
    # 2. 读取配置详情以便获取 plan 等默认值
    try:
        with open(config_path, "r", encoding="utf-8") as f:
            cfg_data = json.load(f)
    except Exception as e:
        print(f"[ERROR] 无法读取配置文件: {e}")
        sys.exit(1)
        
    base_plan = cfg_data.get("fresh_checkout", {}).get("plan", {})
    plan = args.plan or base_plan.get("plan_name", "chatgptteamplan")
    if "plus" in plan.lower():
        plan = "plus"
    else:
        plan = "team"
        
    country = args.country or base_plan.get("billing_country", "IE")
    currency = args.currency or base_plan.get("billing_currency", "EUR")
    promo = args.promo or base_plan.get("promo_campaign_id", "")
    proxy_url = cfg_data.get("proxy", "")
    
    if isinstance(proxy_url, dict):
        # 如果是字典 {"host": "...", "port": 123}，拼装成 socks5 字符串
        host = proxy_url.get("host", "127.0.0.1")
        port = proxy_url.get("port")
        proxy_url = f"socks5://{host}:{port}" if port else ""

    print(f"[INIT] 支付模式: {plan.upper()} | 账单国家: {country} | 账单币种: {currency} | 优惠代码: {promo or '(自动默认)'}")

    # 3. 寻找待支付的账号
    acct = None
    if args.phone:
        acct = get_account_by_phone(args.phone)
        if not acct:
            print(f"[ERROR] 数据库中未找到符合条件的手机号: {args.phone}")
            sys.exit(1)
    else:
        unpaid = get_unpaid_accounts()
        if not unpaid:
            print("\n" + "=" * 60)
            print("🎉 恭喜！当前数据库中没有已注册未支付的账号！")
            print("=" * 60 + "\n")
            return
            
        print("\n" + "=" * 75)
        print("          成功注册且未支付的账号列表 (Unpaid Registered Accounts)")
        print("=" * 75)
        print(f"  {'序号':<4} | {'手机号 (Phone)':<16} | {'注册时间 (Created)':<20} | {'接码省份':<6}")
        print("-" * 75)
        for idx, r in enumerate(unpaid, 1):
            phone = r.get("phone", "")
            created = r.get("created_at", "")
            region = r.get("phone_region", "") or "未知"
            print(f"  [{idx:<2}]  | {phone:<16} | {created:<20} | {region:<6}")
        print("=" * 75 + "\n")
        
        try:
            choice = input("👉 请选择要进行支付的账号序号 (输入q退出): ").strip()
            if choice.lower() in ("q", "quit", "exit", ""):
                print("已取消退出。")
                return
            idx = int(choice)
            if idx < 1 or idx > len(unpaid):
                print("[ERROR] 序号超出范围，退出")
                return
            acct = unpaid[idx - 1]
        except ValueError:
            print("[ERROR] 输入非有效数字，退出")
            return

    phone = acct["phone"]
    tag = phone[-8:]
    log(tag, f"开始处理账号: phone={phone} (name={acct.get('name') or '?'})")
    
    # 4. 生成托管支付长链接
    log(tag, "正在向 ChatGPT payments API 请求托管支付长链接...")
    
    # 使用账号自带的 proxy_ip 尝试，如果没有则回退使用全局配置中的 proxy
    acct_proxy = acct.get("proxy_ip") or proxy_url
    
    url_res = fetch_checkout_url(
        access_token=acct["access_token"],
        session_token=acct["session_token"],
        plan=plan,
        country=country,
        currency=currency,
        promo_campaign_id=promo,
        proxy_url=acct_proxy
    )
    
    if not url_res.get("ok"):
        log(tag, f"❌ 获取支付长链接失败: {url_res.get('error')}")
        sys.exit(1)
        
    checkout_url = url_res["checkout_url"]
    print("\n" + "=" * 80)
    print("💎 成功生成 Stripe 托管支付长链接 (长连接)：")
    print("-" * 80)
    print(checkout_url)
    print("=" * 80 + "\n")
    
    # 5. 如果是 no-pay 模式，直接安全退出
    if args.no_pay:
        log(tag, "已开启 --no-pay，任务已安全结束（仅获取了长连接，未发生真实付款）。")
        return
        
    # 6. 开始进行 PayPal 实际扣款授权
    success = run_paypal_payment(acct, checkout_url, config_path, timeout=args.timeout)
    
    if success:
        log(tag, "🎉 PayPal 支付执行成功！正在更新数据库状态...")
        update_payment_status(phone, "success")
        log(tag, "🚀 搞定！账号现在可以使用了。")
    else:
        log(tag, "❌ PayPal 支付执行失败，账号付款状态未更新。")
        sys.exit(1)


if __name__ == "__main__":
    main()
