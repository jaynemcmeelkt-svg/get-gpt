"""
号码数据库管理
==============
表:
  - phone_records: 每次获取号码的完整记录
  - blacklist_prefixes: 黑名单号段 (前缀匹配)
  - accounts: 注册成功的账号信息

使用:
  from phone_db import PhoneDB
  db = PhoneDB()
  db.is_phone_used("+12895433547")  → True/False
  db.is_blacklisted("+12895433547") → True/False
  db.add_record(...)
  db.update_status(record_id, status="success")
  db.add_account(phone="+1289...", password="...", ...)
"""

import sqlite3
import time
from pathlib import Path

DB_DIR = Path(__file__).parent.parent / "data"
DB_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = DB_DIR / "phone_records.db"

DB_WRITE_RETRIES = 5
DB_WRITE_RETRY_DELAY = 0.5


def _db_write(func):
    def wrapper(*args, **kwargs):
        for attempt in range(1, DB_WRITE_RETRIES + 1):
            try:
                return func(*args, **kwargs)
            except sqlite3.OperationalError as e:
                if "locked" in str(e).lower() and attempt < DB_WRITE_RETRIES:
                    time.sleep(DB_WRITE_RETRY_DELAY * attempt)
                else:
                    raise
    return wrapper


class PhoneDB:
    def __init__(self, db_path=None):
        self.db_path = db_path or str(DB_PATH)
        self._conn = None
        self._ensure_tables()

    @property
    def conn(self):
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path)
            self._conn.row_factory = sqlite3.Row
            self._conn.execute("PRAGMA journal_mode=WAL")
        return self._conn

    def _ensure_tables(self):
        c = self.conn.cursor()
        c.execute("""
            CREATE TABLE IF NOT EXISTS phone_records (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                sms_provider TEXT DEFAULT '',
                sms_cost REAL DEFAULT 0,
                phone_region TEXT DEFAULT '',
                project TEXT DEFAULT '',
                status TEXT DEFAULT 'pending',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_phone ON phone_records(phone)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_status ON phone_records(status)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS blacklist_prefixes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                prefix TEXT NOT NULL UNIQUE,
                reason TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_prefix ON blacklist_prefixes(prefix)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS runs (
                run_id TEXT PRIMARY KEY,
                flow_type TEXT DEFAULT 'register',
                status TEXT DEFAULT 'running',
                phone_used TEXT DEFAULT '',
                email_used TEXT DEFAULT '',
                proxy_used TEXT DEFAULT '',
                data_path TEXT DEFAULT '',
                error_message TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS accounts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL,
                password TEXT DEFAULT '',
                name TEXT DEFAULT '',
                birthdate TEXT DEFAULT '',
                email TEXT DEFAULT '',
                user_id TEXT DEFAULT '',
                plan_type TEXT DEFAULT '',
                access_token TEXT DEFAULT '',
                session_token TEXT DEFAULT '',
                refresh_token TEXT DEFAULT '',
                token_status TEXT DEFAULT 'pending',
                oauth_status TEXT DEFAULT 'unknown',
                payment_status TEXT DEFAULT 'pending',
                account_status TEXT DEFAULT 'active',
                run_id TEXT DEFAULT '',
                codex_status TEXT DEFAULT 'pending',
                codex_token TEXT DEFAULT '',
                proxy_ip TEXT DEFAULT '',
                sms_provider TEXT DEFAULT '',
                sms_cost REAL DEFAULT 0,
                phone_region TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT DEFAULT '',
                token_updated_at TEXT DEFAULT '',
                payment_updated_at TEXT DEFAULT ''
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_acc_phone ON accounts(phone)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_acc_token_status ON accounts(token_status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_acc_payment_status ON accounts(payment_status)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_runs_status ON runs(status)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS failed_operators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                country_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                error_code TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_fo_service ON failed_operators(service)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_fo_lookup ON failed_operators(country_id, operator_id)")
        c.execute("""
            CREATE TABLE IF NOT EXISTS successful_operators (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                service TEXT NOT NULL,
                country_id TEXT NOT NULL,
                operator_id TEXT NOT NULL,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_so_service ON successful_operators(service)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_so_lookup ON successful_operators(country_id, operator_id)")

        self._migrate_accounts_columns(c)

        try:
            c.execute("CREATE INDEX IF NOT EXISTS idx_acc_run_id ON accounts(run_id)")
        except Exception:
            pass

        c.execute("""
            CREATE TABLE IF NOT EXISTS card (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                card_type TEXT NOT NULL,
                card_number TEXT NOT NULL UNIQUE,
                cvv TEXT DEFAULT '',
                expires TEXT DEFAULT '',
                batch_id INTEGER DEFAULT 0,
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_card_type ON card(card_type)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_card_number ON card(card_number)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_card_batch ON card(batch_id)")

        c.execute("""
            CREATE TABLE IF NOT EXISTS paypal_phone (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                phone TEXT NOT NULL UNIQUE,
                sms_url TEXT NOT NULL,
                status TEXT DEFAULT 'active',
                use_count INTEGER DEFAULT 0,
                last_used_at TEXT DEFAULT '',
                last_otp TEXT DEFAULT '',
                last_otp_status TEXT DEFAULT '',
                created_at TEXT NOT NULL
            )
        """)
        c.execute("CREATE INDEX IF NOT EXISTS idx_pp_phone ON paypal_phone(phone)")
        c.execute("CREATE INDEX IF NOT EXISTS idx_pp_status ON paypal_phone(status)")

        self.conn.commit()

    def is_phone_used(self, phone: str) -> bool:
        row = self.conn.execute(
            "SELECT id FROM phone_records WHERE phone = ? LIMIT 1",
            (phone,)
        ).fetchone()
        return row is not None

    def is_blacklisted(self, phone: str) -> tuple[bool, str]:
        prefixes = self.conn.execute(
            "SELECT prefix, reason FROM blacklist_prefixes"
        ).fetchall()
        for row in prefixes:
            if phone.startswith(row["prefix"]):
                return True, row["reason"]
        return False, ""

    @_db_write
    def add_record(self, phone: str, sms_provider: str = "", sms_cost: float = 0,
                   phone_region: str = "", project: str = "",
                   status: str = "pending") -> int:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO phone_records (phone, sms_provider, sms_cost, phone_region, project, status, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """, (phone, sms_provider, sms_cost, phone_region, project, status, now, now))
        self.conn.commit()
        return c.lastrowid

    @_db_write
    def update_status(self, record_id: int, status: str, **kwargs):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sets = ["status = ?", "updated_at = ?"]
        vals = [status, now]
        for k, v in kwargs.items():
            if k in ("sms_provider", "sms_cost", "phone_region", "project"):
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(record_id)
        self.conn.execute(
            f"UPDATE phone_records SET {', '.join(sets)} WHERE id = ?",
            vals
        )
        self.conn.commit()

    def get_phone_status(self, phone: str) -> str:
        row = self.conn.execute(
            "SELECT status FROM phone_records WHERE phone = ? ORDER BY id DESC LIMIT 1",
            (phone,)
        ).fetchone()
        return row["status"] if row else ""

    @_db_write
    def add_blacklist_prefix(self, prefix: str, reason: str = ""):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("""
            INSERT OR IGNORE INTO blacklist_prefixes (prefix, reason, created_at)
            VALUES (?, ?, ?)
        """, (prefix, reason, now))
        self.conn.commit()

    @_db_write
    def remove_blacklist_prefix(self, prefix: str):
        self.conn.execute("DELETE FROM blacklist_prefixes WHERE prefix = ?", (prefix,))
        self.conn.commit()

    def list_blacklist(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT prefix, reason, created_at FROM blacklist_prefixes ORDER BY prefix"
        ).fetchall()
        return [dict(r) for r in rows]

    def stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) c FROM phone_records").fetchone()["c"]
        success = self.conn.execute(
            "SELECT COUNT(*) c FROM phone_records WHERE status = 'success'"
        ).fetchone()["c"]
        failed = self.conn.execute(
            "SELECT COUNT(*) c FROM phone_records WHERE status IN ('failed', 'timeout', 'blacklisted', 'used')"
        ).fetchone()["c"]
        pending = self.conn.execute(
            "SELECT COUNT(*) c FROM phone_records WHERE status = 'pending'"
        ).fetchone()["c"]
        bl_count = self.conn.execute("SELECT COUNT(*) c FROM blacklist_prefixes").fetchone()["c"]
        acc_total = self.conn.execute("SELECT COUNT(*) c FROM accounts").fetchone()["c"]
        acc_token_ok = self.conn.execute(
            "SELECT COUNT(*) c FROM accounts WHERE token_status = 'success'"
        ).fetchone()["c"]
        acc_payment_ok = self.conn.execute(
            "SELECT COUNT(*) c FROM accounts WHERE payment_status = 'success'"
        ).fetchone()["c"]
        return {
            "total": total, "success": success, "failed": failed, "pending": pending,
            "blacklist_count": bl_count,
            "accounts": acc_total, "token_ok": acc_token_ok, "payment_ok": acc_payment_ok,
        }

    def _migrate_accounts_columns(self, cursor):
        existing = {r[1] for r in cursor.execute("PRAGMA table_info(accounts)").fetchall()}
        new_cols = {
            "refresh_token": "TEXT DEFAULT ''",
            "oauth_status": "TEXT DEFAULT 'unknown'",
            "account_status": "TEXT DEFAULT 'active'",
            "run_id": "TEXT DEFAULT ''",
            "codex_status": "TEXT DEFAULT 'pending'",
            "codex_token": "TEXT DEFAULT ''",
        }
        for col, dtype in new_cols.items():
            if col not in existing:
                cursor.execute(f"ALTER TABLE accounts ADD COLUMN {col} {dtype}")

    # ============================================================
    # runs table
    # ============================================================
    @_db_write
    def add_run(self, run_id: str, flow_type: str = "register",
                proxy_used: str = "", data_path: str = "") -> str:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("""
            INSERT INTO runs (run_id, flow_type, status, proxy_used, data_path, created_at, updated_at)
            VALUES (?, ?, 'running', ?, ?, ?, ?)
        """, (run_id, flow_type, proxy_used, data_path, now, now))
        self.conn.commit()
        return run_id

    @_db_write
    def update_run(self, run_id: str, **kwargs):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sets = ["updated_at = ?"]
        vals = [now]
        for k, v in kwargs.items():
            if k in ("status", "phone_used", "email_used", "proxy_used", "data_path", "error_message"):
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(run_id)
        self.conn.execute(f"UPDATE runs SET {', '.join(sets)} WHERE run_id = ?", vals)
        self.conn.commit()

    def get_run(self, run_id: str) -> dict:
        row = self.conn.execute("SELECT * FROM runs WHERE run_id = ?", (run_id,)).fetchone()
        return dict(row) if row else {}

    # ============================================================
    # accounts table (enhanced)
    # ============================================================
    @_db_write
    def add_account(self, phone: str, password: str = "", name: str = "",
                    birthdate: str = "", email: str = "", user_id: str = "",
                    plan_type: str = "", access_token: str = "", session_token: str = "",
                    refresh_token: str = "", token_status: str = "pending",
                    oauth_status: str = "unknown", payment_status: str = "pending",
                    account_status: str = "active", run_id: str = "",
                    proxy_ip: str = "", sms_provider: str = "", sms_cost: float = 0,
                    phone_region: str = "", codex_status: str = "pending",
                    codex_token: str = "") -> int:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c = self.conn.cursor()
        c.execute("""
            INSERT INTO accounts (
                phone, password, name, birthdate, email, user_id, plan_type,
                access_token, session_token, refresh_token,
                token_status, oauth_status, payment_status, account_status,
                run_id, codex_status, codex_token, proxy_ip, sms_provider,
                sms_cost, phone_region,
                created_at, updated_at, token_updated_at, payment_updated_at
            ) VALUES (
                ?, ?, ?, ?, ?, ?, ?,
                ?, ?, ?,
                ?, ?, ?, ?,
                ?, ?, ?, ?, ?,
                ?, ?,
                ?, ?, ?, ?
            )
        """, (
            phone, password, name, birthdate, email, user_id, plan_type,
            access_token, session_token, refresh_token,
            token_status, oauth_status, payment_status, account_status,
            run_id, codex_status, codex_token, proxy_ip, sms_provider, sms_cost, phone_region,
            now, now, now if token_status != "pending" else "", ""
        ))
        self.conn.commit()
        return c.lastrowid

    @_db_write
    def update_account_codex(self, account_id: int, codex_status: str, codex_token: str = ""):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sets = ["codex_status = ?", "updated_at = ?"]
        vals = [codex_status, now]
        if codex_token:
            sets.append("codex_token = ?")
            vals.append(codex_token)
        vals.append(account_id)
        self.conn.execute(
            f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?",
            vals
        )
        self.conn.commit()

    @_db_write
    def update_account_token(self, account_id: int, token_status: str,
                             access_token: str = "", session_token: str = "",
                             **kwargs):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sets = ["token_status = ?", "updated_at = ?", "token_updated_at = ?"]
        vals = [token_status, now, now]
        if access_token:
            sets.append("access_token = ?")
            vals.append(access_token)
        if session_token:
            sets.append("session_token = ?")
            vals.append(session_token)
        for k, v in kwargs.items():
            if k in ("user_id", "email", "plan_type"):
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(account_id)
        self.conn.execute(
            f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?",
            vals
        )
        self.conn.commit()

    @_db_write
    def update_account_payment(self, account_id: int, payment_status: str, **kwargs):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sets = ["payment_status = ?", "updated_at = ?", "payment_updated_at = ?"]
        vals = [payment_status, now, now]
        for k, v in kwargs.items():
            if k in ("plan_type",):
                sets.append(f"{k} = ?")
                vals.append(v)
        vals.append(account_id)
        self.conn.execute(
            f"UPDATE accounts SET {', '.join(sets)} WHERE id = ?",
            vals
        )
        self.conn.commit()

    def get_account_by_phone(self, phone: str) -> dict:
        row = self.conn.execute(
            "SELECT * FROM accounts WHERE phone = ? ORDER BY id DESC LIMIT 1",
            (phone,)
        ).fetchone()
        return dict(row) if row else {}

    def list_accounts(self, token_status: str = "", payment_status: str = "",
                      limit: int = 50) -> list[dict]:
        query = "SELECT * FROM accounts WHERE 1=1"
        params = []
        if token_status:
            query += " AND token_status = ?"
            params.append(token_status)
        if payment_status:
            query += " AND payment_status = ?"
            params.append(payment_status)
        query += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        rows = self.conn.execute(query, params).fetchall()
        return [dict(r) for r in rows]

    @_db_write
    def add_failed_operator(self, service: str, country_id: str, operator_id: str, error_code: str = ""):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("""
            INSERT INTO failed_operators (service, country_id, operator_id, error_code, created_at)
            VALUES (?, ?, ?, ?, ?)
        """, (str(service), str(country_id), str(operator_id), str(error_code), now))
        self.conn.commit()

    def get_blacklisted_operators(self, service: str, threshold: int = 10) -> set[tuple[str, str]]:
        rows = self.conn.execute("""
            SELECT country_id, operator_id 
            FROM failed_operators 
            WHERE service = ? 
            GROUP BY country_id, operator_id 
            HAVING COUNT(*) >= ?
        """, (str(service), threshold)).fetchall()
        return {(str(row["country_id"]), str(row["operator_id"])) for row in rows}

    @_db_write
    def add_successful_operator(self, service: str, country_id: str, operator_id: str):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        self.conn.execute("""
            INSERT INTO successful_operators (service, country_id, operator_id, created_at)
            VALUES (?, ?, ?, ?)
        """, (str(service), str(country_id), str(operator_id), now))
        self.conn.commit()

    def get_premium_operators(self, service: str, threshold: int = 20) -> set[tuple[str, str]]:
        rows = self.conn.execute("""
            SELECT country_id, operator_id 
            FROM successful_operators 
            WHERE service = ? 
            GROUP BY country_id, operator_id 
            HAVING COUNT(*) >= ?
        """, (str(service), threshold)).fetchall()
        return {(str(row["country_id"]), str(row["operator_id"])) for row in rows}

    @_db_write
    def add_card(self, card_type: str, card_number: str, cvv: str = "",
                 expires: str = "", batch_id: int = 0) -> int:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c = self.conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO card (card_type, card_number, cvv, expires, batch_id, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (card_type, card_number, cvv, expires, batch_id, now))
        self.conn.commit()
        return c.lastrowid

    def get_card_stats(self) -> dict:
        total = self.conn.execute("SELECT COUNT(*) c FROM card").fetchone()["c"]
        visa = self.conn.execute("SELECT COUNT(*) c FROM card WHERE card_type = 'Visa'").fetchone()["c"]
        jcb = self.conn.execute("SELECT COUNT(*) c FROM card WHERE card_type = 'JCB'").fetchone()["c"]
        return {"total": total, "visa": visa, "jcb": jcb}

    @_db_write
    def add_paypal_phone(self, phone: str, sms_url: str) -> int:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        c = self.conn.cursor()
        c.execute("""
            INSERT OR IGNORE INTO paypal_phone (phone, sms_url, status, created_at)
            VALUES (?, ?, 'active', ?)
        """, (phone, sms_url, now))
        self.conn.commit()
        return c.lastrowid

    def pick_paypal_phone(self) -> dict:
        row = self.conn.execute(
            "SELECT id, phone, sms_url, status, use_count FROM paypal_phone "
            "WHERE status = 'active' ORDER BY use_count ASC, RANDOM() LIMIT 1"
        ).fetchone()
        return dict(row) if row else {}

    @_db_write
    def update_paypal_phone_usage(self, phone_id: int, otp: str = "", otp_status: str = ""):
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sets = ["use_count = use_count + 1", "last_used_at = ?"]
        vals = [now]
        if otp:
            sets.append("last_otp = ?")
            vals.append(otp)
        if otp_status:
            sets.append("last_otp_status = ?")
            vals.append(otp_status)
        vals.append(phone_id)
        self.conn.execute(
            f"UPDATE paypal_phone SET {', '.join(sets)} WHERE id = ?",
            vals
        )
        self.conn.commit()

    @_db_write
    def set_paypal_phone_status(self, phone_id: int, status: str):
        self.conn.execute(
            "UPDATE paypal_phone SET status = ? WHERE id = ?",
            (status, phone_id)
        )
        self.conn.commit()

    def list_paypal_phones(self) -> list[dict]:
        rows = self.conn.execute(
            "SELECT * FROM paypal_phone ORDER BY use_count ASC, id ASC"
        ).fetchall()
        return [dict(r) for r in rows]

    def close(self):
        if self._conn:
            self._conn.close()
            self._conn = None
