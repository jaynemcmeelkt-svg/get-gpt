import json
import logging
import random
import re
import string
import time
from pathlib import Path


class RunContext:
    def __init__(self, flow_type: str = "register", identifier: str = "", base_dir: Path = None):
        self.flow_type = flow_type
        self.run_id = self._gen_run_id()
        self.base_dir = base_dir or Path(__file__).parent.parent / "runs"
        self.task_dir = self.base_dir / self.run_id
        self.task_dir.mkdir(parents=True, exist_ok=True)

        self.config_dir = self.task_dir / "config"
        self.auth_dir = self.task_dir / "auth"
        self.logs_dir = self.task_dir / "logs"
        self.debug_dir = self.task_dir / "debug"
        self.steps_dir = self.task_dir / "steps"
        self.cookies_dir = self.task_dir / "cookies"
        self.tokens_dir = self.task_dir / "tokens"

        for d in (self.config_dir, self.auth_dir, self.logs_dir,
                  self.debug_dir, self.steps_dir, self.cookies_dir, self.tokens_dir):
            d.mkdir(exist_ok=True)

        self._meta = {
            "run_id": self.run_id,
            "flow_type": flow_type,
            "status": "running",
            "final_phone": "",
            "attempts": [],
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        }
        self._write_meta()

        self._run_logger = self._setup_run_logger()

        self._trace_file = self.logs_dir / "http_trace.jsonl"
        self._trace_count = 0

    @staticmethod
    def _gen_run_id() -> str:
        ts = time.strftime("%Y%m%d_%H%M%S")
        rand = ''.join(random.choices(string.ascii_lowercase + string.digits, k=6))
        return f"run_{ts}_{rand}"

    def _write_json(self, path: Path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    def _write_meta(self):
        self._write_json(self.task_dir / "meta.json", self._meta)

    def _setup_run_logger(self) -> logging.Logger:
        logger = logging.getLogger(f"run.{self.run_id}")
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        if not logger.handlers:
            fh = logging.FileHandler(self.logs_dir / "run.log", encoding="utf-8")
            fh.setFormatter(logging.Formatter("%(asctime)s %(message)s", datefmt="%H:%M:%S"))
            logger.addHandler(fh)
        return logger

    def log_to_file(self, msg: str):
        self._run_logger.info(msg)

    def update_meta_status(self, status: str, **kwargs):
        self._meta["status"] = status
        self._meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        for k, v in kwargs.items():
            if k in self._meta:
                self._meta[k] = v
        self._write_meta()

    def add_attempt(self, phone: str):
        self._meta["attempts"].append(phone)
        self._meta["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S")
        if not self._meta["final_phone"]:
            self._meta["final_phone"] = phone
        self._write_meta()

    def log_step(self, step_name, request_info, response_info, status_code=None):
        self._write_json(self.steps_dir / f"{step_name}.json", {
            "step": step_name,
            "status_code": status_code,
            "request": request_info,
            "response": response_info,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

    def log_http_trace(self, method: str, url: str, status_code: int,
                       elapsed_ms: float, step_name: str = "", error: str = ""):
        self._trace_count += 1
        entry = {
            "seq": self._trace_count,
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "method": method,
            "url": url[:200],
            "status": status_code,
            "elapsed_ms": round(elapsed_ms, 1),
            "step": step_name,
        }
        if error:
            entry["error"] = error[:200]
        with open(self._trace_file, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def save_cookies(self, step_name, session):
        cookies = []
        jar = getattr(session, "cookies", None)
        if jar is not None:
            inner_jar = getattr(jar, "jar", None)
            if inner_jar is not None:
                for cookie in inner_jar:
                    cookies.append({
                        "name": getattr(cookie, "name", ""),
                        "value": getattr(cookie, "value", ""),
                        "domain": getattr(cookie, "domain", ""),
                        "path": getattr(cookie, "path", "/"),
                        "secure": getattr(cookie, "secure", False),
                        "expires": getattr(cookie, "expires", None),
                    })
            else:
                for k, v in jar.items():
                    cookies.append({"name": k, "value": str(v)})
        self._write_json(self.cookies_dir / f"{step_name}.json", cookies)

    def save_cookies_to_auth(self, session):
        cookies = []
        jar = getattr(session, "cookies", None)
        if jar is not None:
            inner_jar = getattr(jar, "jar", None)
            if inner_jar is not None:
                for cookie in inner_jar:
                    cookies.append({
                        "name": getattr(cookie, "name", ""),
                        "value": getattr(cookie, "value", ""),
                        "domain": getattr(cookie, "domain", ""),
                        "path": getattr(cookie, "path", "/"),
                        "secure": getattr(cookie, "secure", False),
                        "expires": getattr(cookie, "expires", None),
                    })
            else:
                for k, v in jar.items():
                    cookies.append({"name": k, "value": str(v)})
        self._write_json(self.auth_dir / "cookies.json", cookies)

    def save_token(self, name, value):
        self._write_json(self.auth_dir / "tokens.json" if name == "full_session"
                         else self.tokens_dir / f"{name}.json",
                         {"value": value} if name != "full_session" else value)

    def save_token_json(self, name, value):
        self._write_json(self.auth_dir / "tokens.json", value)

    def save_tokens_to_auth(self, access_token: str = "", session_token: str = "",
                            refresh_token: str = "", device_id: str = ""):
        self._write_json(self.auth_dir / "tokens.json", {
            "access_token": access_token,
            "session_token": session_token,
            "refresh_token": refresh_token,
            "device_id": device_id,
        })

    def save_config(self, input_data: dict = None, identity: dict = None):
        if input_data:
            self._write_json(self.config_dir / "input.json", input_data)
        if identity:
            self._write_json(self.config_dir / "identity.json", identity)

    def save_result(self, payload):
        self._write_json(self.task_dir / "result.json", payload)

    def save_account(self, payload):
        self._write_json(self.task_dir / "account.json", payload)

    def save_error(self, error_msg: str, step: str = "", traceback_str: str = ""):
        self._write_json(self.debug_dir / "error.json", {
            "error": error_msg,
            "step": step,
            "traceback": traceback_str,
            "timestamp": time.strftime("%Y-%m-%dT%H:%M:%S"),
        })

    def close(self):
        for h in self._run_logger.handlers[:]:
            h.close()
            self._run_logger.removeHandler(h)
