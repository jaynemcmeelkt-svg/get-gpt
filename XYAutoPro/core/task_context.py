import json
import re
import time
from pathlib import Path


class TaskContext:
    """最小运行上下文：为脚本保存步骤日志、Cookies、Token 和最终结果。"""

    def __init__(self, task_type: str, identifier: str):
        safe_id = re.sub(r"[^0-9A-Za-z_.-]+", "_", str(identifier or "unknown").strip()).strip("_") or "unknown"
        timestamp = time.strftime("%Y%m%d_%H%M%S")
        self.task_dir = Path(__file__).parent / "runs" / f"{task_type}_{timestamp}_{safe_id}"
        self.task_dir.mkdir(parents=True, exist_ok=True)
        self.steps_dir = self.task_dir / "steps"
        self.steps_dir.mkdir(exist_ok=True)
        self.tokens_dir = self.task_dir / "tokens"
        self.tokens_dir.mkdir(exist_ok=True)
        self.cookies_dir = self.task_dir / "cookies"
        self.cookies_dir.mkdir(exist_ok=True)

    def _write_json(self, path: Path, payload):
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2, default=str)

    def log_step(self, step_name, request_info, response_info, status_code=None):
        self._write_json(self.steps_dir / f"{step_name}.json", {
            "step": step_name,
            "status_code": status_code,
            "request": request_info,
            "response": response_info,
            "timestamp": time.strftime("%Y-%m-%d %H:%M:%S"),
        })

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

    def save_token(self, name, value):
        self._write_json(self.tokens_dir / f"{name}.json", {"value": value})

    def save_token_json(self, name, value):
        self._write_json(self.tokens_dir / f"{name}.json", value)

    def save_result(self, payload):
        self._write_json(self.task_dir / "result.json", payload)

    def save_account(self, payload):
        self._write_json(self.task_dir / "account.json", payload)
