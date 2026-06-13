"""
tools/import_paypal_phones.py
=============================
将 US 接码号码导入 paypal_phone 表。

号码格式（每行一条）：
    +10000000001|http://YOUR_PAYPAL_SMS_PROVIDER_URL/api/get_sms?key=xxx
    +10000000002|http://YOUR_PAYPAL_SMS_PROVIDER_URL/api/get_sms?key=yyy

用法:
    # 从文件导入
    python tools/import_paypal_phones.py phones.txt

    # 直接命令行传入一条
    python tools/import_paypal_phones.py --line "+10000000001|http://..."

    # 列出当前库存
    python tools/import_paypal_phones.py --list
"""

import sys
import time
import sqlite3
import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.stdout.reconfigure(encoding="utf-8", errors="replace")

DB_PATH = ROOT / "data" / "phone_records.db"


def log(msg: str):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def get_conn():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    return conn


def import_line(conn, line: str) -> str:
    """解析一行 phone|sms_url，写入 paypal_phone。返回 'added'/'dup'/'invalid'。"""
    line = line.strip()
    if not line or line.startswith("#"):
        return "skip"
    parts = line.split("|", 1)
    if len(parts) != 2:
        return "invalid"
    phone, sms_url = parts[0].strip(), parts[1].strip()
    if not phone.startswith("+") or not sms_url.startswith("http"):
        return "invalid"
    now = time.strftime("%Y-%m-%d %H:%M:%S")
    try:
        conn.execute(
            "INSERT INTO paypal_phone (phone, sms_url, status, use_count, created_at) "
            "VALUES (?, ?, 'active', 0, ?)",
            (phone, sms_url, now),
        )
        return "added"
    except sqlite3.IntegrityError:
        return "dup"


def list_phones(conn):
    rows = conn.execute(
        "SELECT id, phone, status, use_count, last_used_at, last_otp_status "
        "FROM paypal_phone ORDER BY id"
    ).fetchall()
    if not rows:
        log("paypal_phone 表为空")
        return
    log(f"paypal_phone 表共 {len(rows)} 条：")
    for r in rows:
        log(f"  id={r['id']} {r['phone']} status={r['status']} "
            f"use_count={r['use_count']} last_otp_status={r['last_otp_status'] or '-'}")


def main():
    parser = argparse.ArgumentParser(description="US 接码号码导入 paypal_phone 表")
    parser.add_argument("file", nargs="?", help="包含 phone|sms_url 的文本文件路径")
    parser.add_argument("--line", help="直接传入一行 phone|sms_url")
    parser.add_argument("--list", action="store_true", help="展示当前库存")
    args = parser.parse_args()

    conn = get_conn()

    if args.list:
        list_phones(conn)
        conn.close()
        return

    lines = []
    if args.line:
        lines = [args.line]
    elif args.file:
        path = Path(args.file)
        if not path.exists():
            log(f"文件不存在: {path}")
            sys.exit(1)
        lines = path.read_text(encoding="utf-8").splitlines()
    else:
        parser.print_help()
        sys.exit(0)

    added = dup = invalid = 0
    for line in lines:
        r = import_line(conn, line)
        if r == "added":
            added += 1
            log(f"  + {line.split('|')[0].strip()}")
        elif r == "dup":
            dup += 1
        elif r == "invalid":
            invalid += 1
            log(f"  ✗ 格式错误: {line[:80]}")

    conn.commit()
    conn.close()
    log(f"完成：新增 {added}，重复跳过 {dup}，格式错误 {invalid}")


if __name__ == "__main__":
    main()
