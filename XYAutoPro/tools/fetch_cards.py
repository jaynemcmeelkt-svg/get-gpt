"""
tools/fetch_cards.py
====================
从 api2.suijidaquan.com 批量采集随机信用卡并写入 card 表。

用法:
    python tools/fetch_cards.py                         # 默认拉 100 条，存 Visa+JCB
    python tools/fetch_cards.py --count 200             # 指定每轮拉取数量
    python tools/fetch_cards.py --rounds 5              # 重复 5 轮（共 500 条请求）
    python tools/fetch_cards.py --types Visa JCB        # 只存指定类型

    # 本地生成模式（不调用外部 API，用 Luhn 算法本地生成）：
    python tools/fetch_cards.py --local                 # 清空后本地生成 JCB×10 Visa×10 AmericanExpress×10
    python tools/fetch_cards.py --local --clear         # 同上（--local 默认含清空）
    python tools/fetch_cards.py --local --count 20      # 每种生成 20 张
    python tools/fetch_cards.py --local --types JCB Visa AmericanExpress --count 10

    # 按 BIN 生成模式（对每个真实 BIN 各生成 N 张）：
    python tools/fetch_cards.py --by-bin                # 每个 BIN 生成 10 张，共 1330 张
    python tools/fetch_cards.py --by-bin --count 5      # 每个 BIN 生成 5 张
"""

import sys
import time
import random
import sqlite3
import argparse
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = ROOT / "data" / "phone_records.db"
API_URL = "https://api2.suijidaquan.com/api/v2/random-credit-card"
HEADERS = {
    "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36",
    "Referer":  "https://www.suijidaquan.com/",
    "Origin":   "https://www.suijidaquan.com",
    "Accept":   "application/json, */*",
}


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


# ─── 真实发卡行 BIN 卡头 ───────────────────────────────────────────────────────
# 数据来源：开源真实 BIN 数据库 venelinkochev/bin-list-data（约37万条），
#           每个 BIN 均对应真实发卡行（如 SAISON / Itau Unibanco / Discover Issuer 等）。
# 下方 BIN 均为该库中标记为 CREDIT 且带真实发卡行名的 6 位 IIN，确认真实存在。
# 生成逻辑：真实BIN作前缀 + 随机卡身 + Luhn 校验位，可通过 BIN 前缀检测与 Luhn 双重校验。
REAL_BINS = {
    "JCB": [          # JCB（日本），真实发卡行如 SAISON 等
        "352822", "352865", "352868", "352897", "354006", "354180",
        "354259", "354267", "356006", "356067", "356090", "356162",
        "356323", "356356", "356363", "356413", "356515", "356685",
        "356702", "356780", "356858", "356907", "358715",
    ],
    "Visa": [         # Visa，真实发卡行（多国）
        "400959", "403393", "403657", "404068", "404333", "411704",
        "412697", "413892", "415163", "422733", "428498", "431349",
        "431697", "432100", "433508", "435357", "441136", "441568",
        "462031", "462132", "467149", "477750", "485477", "485619",
        "489321", "493594", "493819",
    ],
    "AmericanExpress": [   # American Express（34/37 开头），真实发卡行
        "340151", "340454", "340503", "341178", "341210", "371325",
        "371436", "371580", "371725", "372458", "372526", "373171",
        "373516", "373545", "373591", "373745", "373955", "374488",
        "374539", "375627", "376998", "377037", "377049", "377505",
        "378547", "379280", "379287",
    ],
    "MasterCard": [   # MasterCard（51-55 / 2221-2720），真实发卡行
        "510340", "510538", "511282", "511631", "515196", "515717",
        "516309", "517355", "518845", "523480", "523763", "524236",
        "524983", "526489", "537955", "543112", "545104", "545122",
        "547400", "547852", "549455", "550021", "552523", "553531",
        "554595", "555465", "555751", "555814",
    ],
    "Discover": [     # Discover，真实发卡行
        "644059", "644362", "644387", "644441", "644473", "645377",
        "645488", "645632", "645777", "647210", "647535", "647564",
        "647610", "647764", "647965", "648459", "648510", "651329",
        "651868", "653525", "654188", "654195", "654467", "654928",
        "654948", "655196", "655849", "658003",
    ],
}

# 各卡种总长度与 CVV 位数
CARD_SPECS = {
    "JCB":             {"length": 16, "cvv_len": 3},
    "Visa":            {"length": 16, "cvv_len": 3},
    "AmericanExpress": {"length": 15, "cvv_len": 4},
    "MasterCard":      {"length": 16, "cvv_len": 3},
    "Discover":        {"length": 16, "cvv_len": 3},
}


# ─── 本地 Luhn 生成 ────────────────────────────────────────────────────────────

def _luhn_check_digit(partial: str) -> int:
    digits = [int(d) for d in partial]
    total = 0
    for i, d in enumerate(reversed(digits)):
        if i % 2 == 0:
            d *= 2
            if d > 9:
                d -= 9
        total += d
    return (10 - total % 10) % 10


def _rand_digits(n: int) -> str:
    return "".join(str(random.randint(0, 9)) for _ in range(n))


def _random_exp() -> str:
    year = datetime.now().year + random.randint(1, 5)
    month = random.randint(1, 12)
    return f"{month:02d}/{year}"


def _gen_one(brand: str) -> dict:
    """用真实发卡行 BIN 卡头作前缀 + 随机卡身 + Luhn 校验位生成卡号。"""
    if brand not in REAL_BINS:
        raise ValueError(f"本地模式不支持卡种: {brand}（可选: {', '.join(REAL_BINS)}）")
    spec = CARD_SPECS[brand]
    bin_prefix = random.choice(REAL_BINS[brand])
    # 卡身 = 真实BIN + 随机填充至 length-1 位，最后补 Luhn 校验位
    body = bin_prefix + _rand_digits(spec["length"] - 1 - len(bin_prefix))
    card_no = body + str(_luhn_check_digit(body))
    cvv = f"{random.randint(0, 10**spec['cvv_len'] - 1):0{spec['cvv_len']}d}"
    return {"card_type": brand, "card_number": card_no, "cvv": cvv, "expires": _random_exp()}


def generate_local(types: list[str], count: int) -> list[dict]:
    cards = []
    for brand in types:
        for _ in range(count):
            cards.append(_gen_one(brand))
    return cards


def generate_by_bin(count_per_bin: int) -> list[dict]:
    """对 REAL_BINS 中每一个真实 BIN 各生成 count_per_bin 张卡。"""
    # BIN -> 所属品牌的反向映射
    bin_to_brand = {}
    for brand, bins in REAL_BINS.items():
        for b in bins:
            bin_to_brand[b] = brand

    cards = []
    for brand, bins in REAL_BINS.items():
        spec = CARD_SPECS[brand]
        for bin_prefix in sorted(bins):
            for _ in range(count_per_bin):
                body = bin_prefix + _rand_digits(spec["length"] - 1 - len(bin_prefix))
                card_no = body + str(_luhn_check_digit(body))
                cvv = f"{random.randint(0, 10**spec['cvv_len'] - 1):0{spec['cvv_len']}d}"
                cards.append({
                    "card_type":   brand,
                    "card_number": card_no,
                    "cvv":         cvv,
                    "expires":     _random_exp(),
                    "_bin":        bin_prefix,   # 仅用于展示，不写库
                })
    return cards


# ─── 清空 card 表 ──────────────────────────────────────────────────────────────

def clear_card_table():
    conn = sqlite3.connect(str(DB_PATH))
    conn.execute("DELETE FROM card")
    conn.commit()
    conn.close()
    log("已清空 card 表。")


# ─── API 采集 ──────────────────────────────────────────────────────────────────

def fetch_cards(count: int) -> list[dict]:
    import requests
    resp = requests.post(
        API_URL,
        json={"count": str(count), "method": "random_credit_card"},
        headers=HEADERS,
        timeout=20,
    )
    resp.raise_for_status()
    return resp.json().get("data") or []


# ─── 写库 ──────────────────────────────────────────────────────────────────────

def save_cards(cards: list[dict], allowed_types: set[str], batch_id: int) -> tuple[int, int]:
    """写入 card 表，跳过重复卡号。返回 (新增, 跳过)。"""
    conn = sqlite3.connect(str(DB_PATH))
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    added = skipped = 0
    try:
        for c in cards:
            # 兼容 API 格式（Credit_Card_Type）和本地格式（card_type）
            ctype  = c.get("card_type") or c.get("Credit_Card_Type", "")
            number = str(c.get("card_number") or c.get("Credit_Card_Number", "")).strip()
            cvv    = str(c.get("cvv") or c.get("CVV2", "")).strip()
            exp    = str(c.get("expires") or c.get("Expires", "")).strip()
            if allowed_types and ctype not in allowed_types:
                continue
            if not number:
                continue
            try:
                conn.execute(
                    "INSERT INTO card (card_type, card_number, cvv, expires, batch_id, created_at) "
                    "VALUES (?, ?, ?, ?, ?, ?)",
                    (ctype, number, cvv, exp, batch_id, now),
                )
                added += 1
            except sqlite3.IntegrityError:
                skipped += 1
        conn.commit()
    finally:
        conn.close()
    return added, skipped


def print_inventory():
    conn = sqlite3.connect(str(DB_PATH))
    rows = conn.execute(
        "SELECT card_type, COUNT(*) FROM card GROUP BY card_type ORDER BY card_type"
    ).fetchall()
    conn.close()
    log("当前 card 表库存:")
    for ctype, cnt in rows:
        log(f"  {ctype}: {cnt} 张")


# ─── 主入口 ────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="批量采集/生成随机信用卡到 card 表")
    parser.add_argument("--count",     type=int, default=100,
                        help="每轮 API 拉取数量 / 本地模式每种生成数量（默认 100）")
    parser.add_argument("--rounds",    type=int, default=1,
                        help="API 模式重复轮数（默认 1）")
    parser.add_argument("--types",     nargs="+", default=["Visa", "JCB"],
                        help="保留/生成的卡类型（默认 Visa JCB）")
    parser.add_argument("--all-types", action="store_true",
                        help="API 模式：保留全部卡类型")
    parser.add_argument("--local",     action="store_true",
                        help="本地 Luhn 生成模式，不调用外部 API（自动清空旧数据）")
    parser.add_argument("--by-bin",    action="store_true",
                        help="按 BIN 生成模式：对 REAL_BINS 中每个真实 BIN 各生成 --count 张（默认10），共 133×N 张")
    parser.add_argument("--clear",     action="store_true",
                        help="写入前清空 card 表（--local/--by-bin 默认含此操作）")
    args = parser.parse_args()

    # ── 按 BIN 生成模式 ──
    if args.by_bin:
        count_per_bin = args.count if args.count != 100 else 10
        total_bins = sum(len(v) for v in REAL_BINS.values())
        total_cards = total_bins * count_per_bin
        log(f"按 BIN 生成模式：{total_bins} 个真实 BIN × {count_per_bin} 张 = {total_cards} 张")
        clear_card_table()
        cards = generate_by_bin(count_per_bin)
        added, skipped = save_cards(cards, set(), batch_id=0)
        log(f"写入完成：新增 {added} 张，跳过重复 {skipped} 张")

        # 打印明细（按品牌分组）
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT id, card_type, card_number, cvv, expires FROM card ORDER BY card_type, card_number"
        ).fetchall()
        conn.close()
        cur_brand = None
        for row in rows:
            id_, brand, no, cvv, exp = row
            if brand != cur_brand:
                cur_brand = brand
                print(f"\n{'─'*65}")
                print(f"  {brand}")
                print(f"{'─'*65}")
                print(f"  {'ID':<6} {'卡号':<20} {'CVV':<6} 有效期")
                print(f"  {'-'*52}")
            print(f"  {id_:<6} {no:<20} {cvv:<6} {exp}")
        print()
        print_inventory()
        return

    # ── 本地生成模式 ──
    if args.local:
        local_types = args.types if args.types != ["Visa", "JCB"] else ["JCB", "Visa", "AmericanExpress"]
        count_each  = args.count if args.count != 100 else 10
        log(f"本地生成模式：{'/'.join(local_types)}，每种 {count_each} 张")
        clear_card_table()
        cards = generate_local(local_types, count_each)
        added, skipped = save_cards(cards, set(), batch_id=0)
        log(f"写入完成：新增 {added} 张，跳过重复 {skipped} 张")

        # 打印明细
        conn = sqlite3.connect(str(DB_PATH))
        rows = conn.execute(
            "SELECT id, card_type, card_number, cvv, expires FROM card ORDER BY id"
        ).fetchall()
        conn.close()
        print(f"\n{'ID':<5} {'类型':<18} {'卡号':<20} {'CVV':<6} 有效期")
        print("-" * 60)
        for row in rows:
            print(f"{row[0]:<5} {row[1]:<18} {row[2]:<20} {row[3]:<6} {row[4]}")
        print()
        print_inventory()
        return

    # ── API 采集模式 ──
    if args.clear:
        clear_card_table()

    allowed = set() if args.all_types else set(args.types)
    label   = "全部" if not allowed else "/".join(sorted(allowed))
    log(f"开始采集，每轮 {args.count} 条 × {args.rounds} 轮，保留类型: {label}")

    total_added = total_skipped = 0
    for r in range(1, args.rounds + 1):
        log(f"第 {r}/{args.rounds} 轮 ...")
        try:
            cards = fetch_cards(args.count)
        except Exception as e:
            log(f"  API 请求失败: {e}")
            continue

        from collections import Counter
        dist = Counter(c.get("Credit_Card_Type") for c in cards)
        log(f"  API 返回 {len(cards)} 条，分布: {dict(dist)}")

        added, skipped = save_cards(cards, allowed, batch_id=r)
        total_added   += added
        total_skipped += skipped
        log(f"  新增 {added} 条，重复跳过 {skipped} 条")

        if r < args.rounds:
            time.sleep(1)

    log(f"完成。总计新增 {total_added} 条，跳过重复 {total_skipped} 条")
    print_inventory()


if __name__ == "__main__":
    main()
