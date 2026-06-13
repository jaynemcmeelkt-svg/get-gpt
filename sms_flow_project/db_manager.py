import sqlite3
import os
import json
from datetime import datetime
from typing import Optional, Dict, Any, List

class DBManager:
    def __init__(self, db_path: str = "sms_flow.db"):
        self.db_path = db_path
        self._memory_conn = None
        if db_path == ":memory:":
            # 对于内存数据库，我们需要保持连接不关闭，否则数据库会被销毁
            self._memory_conn = sqlite3.connect(db_path)
            self._memory_conn.row_factory = sqlite3.Row
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if self._memory_conn:
            return self._memory_conn
        conn = sqlite3.connect(self.db_path)
        conn.row_factory = sqlite3.Row  # 允许通过列名访问数据
        return conn

    def _close_connection(self, conn: sqlite3.Connection):
        """如果不是内存数据库，则关闭连接"""
        if not self._memory_conn:
            conn.close()

    def _init_db(self):
        """初始化数据库，创建 runs 和 accounts 表"""
        create_runs_table = """
        CREATE TABLE IF NOT EXISTS runs (
            run_id TEXT PRIMARY KEY,
            flow_type TEXT NOT NULL,
            status TEXT NOT NULL,
            phone_used TEXT,
            email_used TEXT,
            proxy_used TEXT,
            data_path TEXT NOT NULL,
            error_message TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL
        );
        """
        create_accounts_table = """
        CREATE TABLE IF NOT EXISTS accounts (
            phone TEXT PRIMARY KEY,
            email TEXT,
            password TEXT,
            status TEXT NOT NULL,
            access_token TEXT,
            session_token TEXT,
            refresh_token TEXT,
            run_id TEXT,
            created_at TEXT NOT NULL,
            updated_at TEXT NOT NULL,
            FOREIGN KEY (run_id) REFERENCES runs (run_id)
        );
        """
        conn = self._get_connection()
        try:
            cursor = conn.cursor()
            cursor.execute(create_runs_table)
            cursor.execute(create_accounts_table)
            conn.commit()
        finally:
            self._close_connection(conn)

    # --- Runs 表操作 ---

    def create_run(self, run_id: str, flow_type: str, data_path: str, email_used: Optional[str] = None, proxy_used: Optional[str] = None) -> bool:
        """创建一条新的运行记录"""
        now = datetime.now().isoformat()
        query = """
        INSERT INTO runs (run_id, flow_type, status, email_used, proxy_used, data_path, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """
        conn = self._get_connection()
        try:
            conn.execute(query, (run_id, flow_type, "running", email_used, proxy_used, data_path, now, now))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] 创建运行记录失败: {e}")
            return False
        finally:
            self._close_connection(conn)

    def update_run_phone(self, run_id: str, phone: str) -> bool:
        """更新运行记录中实际使用的手机号"""
        now = datetime.now().isoformat()
        query = """
        UPDATE runs 
        SET phone_used = ?, updated_at = ?
        WHERE run_id = ?
        """
        conn = self._get_connection()
        try:
            conn.execute(query, (phone, now, run_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] 更新运行手机号失败: {e}")
            return False
        finally:
            self._close_connection(conn)

    def update_run_status(self, run_id: str, status: str, error_message: Optional[str] = None) -> bool:
        """更新运行记录的状态 (success, failed)"""
        now = datetime.now().isoformat()
        query = """
        UPDATE runs 
        SET status = ?, error_message = ?, updated_at = ?
        WHERE run_id = ?
        """
        conn = self._get_connection()
        try:
            conn.execute(query, (status, error_message, now, run_id))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] 更新运行状态失败: {e}")
            return False
        finally:
            self._close_connection(conn)

    def get_run(self, run_id: str) -> Optional[Dict[str, Any]]:
        """获取单条运行记录"""
        query = "SELECT * FROM runs WHERE run_id = ?"
        conn = self._get_connection()
        try:
            row = conn.execute(query, (run_id,)).fetchone()
            return dict(row) if row else None
        finally:
            self._close_connection(conn)

    # --- Accounts 表操作 ---

    def upsert_account(self, phone: str, email: Optional[str], password: Optional[str], 
                       access_token: Optional[str], session_token: Optional[str], 
                       refresh_token: Optional[str], run_id: Optional[str], status: str = "active") -> bool:
        """插入或更新账号资产信息"""
        now = datetime.now().isoformat()
        
        # 先检查账号是否存在
        check_query = "SELECT created_at FROM accounts WHERE phone = ?"
        insert_query = """
        INSERT INTO accounts (phone, email, password, status, access_token, session_token, refresh_token, run_id, created_at, updated_at)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """
        update_query = """
        UPDATE accounts 
        SET email = COALESCE(?, email),
            password = COALESCE(?, password),
            status = ?,
            access_token = COALESCE(?, access_token),
            session_token = COALESCE(?, session_token),
            refresh_token = COALESCE(?, refresh_token),
            run_id = COALESCE(?, run_id),
            updated_at = ?
        WHERE phone = ?
        """
        conn = self._get_connection()
        try:
            row = conn.execute(check_query, (phone,)).fetchone()
            if row:
                # 更新已存在的账号 (COALESCE 保证传入 None 时保留原值)
                conn.execute(update_query, (email, password, status, access_token, session_token, refresh_token, run_id, now, phone))
            else:
                # 插入新账号
                conn.execute(insert_query, (phone, email, password, status, access_token, session_token, refresh_token, run_id, now, now))
            conn.commit()
            return True
        except Exception as e:
            print(f"[DB] 保存账号资产失败: {e}")
            return False
        finally:
            self._close_connection(conn)

    def get_account(self, phone: str) -> Optional[Dict[str, Any]]:
        """获取单个账号资产"""
        query = "SELECT * FROM accounts WHERE phone = ?"
        conn = self._get_connection()
        try:
            row = conn.execute(query, (phone,)).fetchone()
            return dict(row) if row else None
        finally:
            self._close_connection(conn)

    def list_accounts(self, status: Optional[str] = None) -> List[Dict[str, Any]]:
        """列出所有账号资产"""
        if status:
            query = "SELECT * FROM accounts WHERE status = ? ORDER BY updated_at DESC"
            params = (status,)
        else:
            query = "SELECT * FROM accounts ORDER BY updated_at DESC"
            params = ()
            
        conn = self._get_connection()
        try:
            rows = conn.execute(query, params).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close_connection(conn)

    def list_runs(self, limit: int = 50) -> List[Dict[str, Any]]:
        """获取最近的历史运行记录"""
        query = "SELECT * FROM runs ORDER BY created_at DESC LIMIT ?"
        conn = self._get_connection()
        try:
            rows = conn.execute(query, (limit,)).fetchall()
            return [dict(row) for row in rows]
        finally:
            self._close_connection(conn)
