"""
Sentinel Token 纯协议生成
========================
完整流程:
  1. 确保 sdk.js 已下载 (复用 sentinel_updater)
  2. Node 子进程执行 sdk.js → 生成 request_p (requirements)
  3. POST /sentinel/req → 获取 challenge (seed, difficulty, token)
  4. Node 子进程执行 sdk.js → 解 PoW (solve) → 生成 final_p + t
  5. 组装最终 sentinel token: {p, t, c, id, flow}

用法:
  python sentinel_token_gen.py [--proxy http://127.0.0.1:7897] [--flow authorize_continue]
"""

import argparse
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    import requests as curl_requests

CACHE_DIR = Path(tempfile.gettempdir()) / "sentinel_sdk"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_SDK_FILE = CACHE_DIR / "sdk.js"
LOCAL_VERSION_FILE = CACHE_DIR / "version.txt"
QUICKJS_SCRIPT = CACHE_DIR / "openai_sentinel_quickjs.js"

SENTINEL_REQ_URL = "https://sentinel.openai.com/backend-api/sentinel/req"
SENTINEL_REFERER = "https://sentinel.openai.com/backend-api/sentinel/frame.html"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)
DEFAULT_SEC_CH_UA = (
    '"Not:A-Brand";v="99", "Google Chrome";v="145", "Chromium";v="145"'
)

WRAPPER_JS = """
const fs = require('fs');
const timeoutMs = Number(process.env.OPENAI_SENTINEL_VM_TIMEOUT_MS || '15000');
const sdkFile = process.env.OPENAI_SENTINEL_SDK_FILE;
const scriptFile = process.env.OPENAI_SENTINEL_QUICKJS_SCRIPT;

let input = '';
process.stdin.setEncoding('utf8');
process.stdin.on('data', (chunk) => { input += chunk; });
process.stdin.on('end', async () => {
  try {
    const payload = JSON.parse(input || '{}');
    globalThis.__payload_json = JSON.stringify(payload);
    globalThis.__sdk_source = fs.readFileSync(sdkFile, 'utf8');
    globalThis.__vm_done = false;
    globalThis.__vm_output_json = '';
    globalThis.__vm_error = '';
    const script = fs.readFileSync(scriptFile, 'utf8');
    eval(script);

    const started = Date.now();
    while (!globalThis.__vm_done) {
      if ((Date.now() - started) > timeoutMs) {
        throw new Error('QuickJS script timeout');
      }
      await new Promise((resolve) => setTimeout(resolve, 1));
    }

    if (String(globalThis.__vm_error || '').trim()) {
      throw new Error(String(globalThis.__vm_error));
    }

    process.stdout.write(String(globalThis.__vm_output_json || ''));
  } catch (err) {
    const msg = err && err.stack ? String(err.stack) : String(err);
    process.stderr.write(msg);
    process.exit(1);
  }
});
""".strip()


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def create_session(proxy: str = ""):
    session = curl_requests.Session()
    session.headers.update({"User-Agent": DEFAULT_UA, "Accept": "*/*"})
    return session


def run_node_action(session, action: str, sdk_file: Path, payload: dict, timeout_ms: int = 30000) -> dict:
    body = dict(payload)
    body["action"] = action
    node_path = os.getenv("OPENAI_SENTINEL_NODE_PATH", "node").strip() or "node"
    proc = subprocess.run(
        [node_path, "-e", WRAPPER_JS],
        input=json.dumps(body, ensure_ascii=False),
        text=True,
        capture_output=True,
        timeout=max(15, int(timeout_ms / 1000) + 10),
        env={
            **os.environ,
            "OPENAI_SENTINEL_SDK_FILE": str(sdk_file),
            "OPENAI_SENTINEL_QUICKJS_SCRIPT": str(QUICKJS_SCRIPT),
            "OPENAI_SENTINEL_VM_TIMEOUT_MS": str(min(timeout_ms, 30000)),
        },
    )
    if proc.returncode != 0:
        err_msg = (proc.stderr or proc.stdout or "unknown").strip()[:500]
        raise RuntimeError(f"Node 执行失败 (action={action}): {err_msg}")
    out = (proc.stdout or "").strip()
    if not out:
        raise RuntimeError(f"Node 返回空输出 (action={action})")
    data = json.loads(out)
    if not isinstance(data, dict):
        raise RuntimeError(f"Node 输出不是 JSON 对象 (action={action})")
    return data


def fetch_challenge(session, device_id: str, flow: str, request_p: str, proxy: str = "") -> dict:
    body = {"p": request_p, "id": device_id, "flow": flow}
    kwargs = {
        "data": json.dumps(body, separators=(",", ":")),
        "headers": {
            "Content-Type": "text/plain;charset=UTF-8",
            "Accept": "*/*",
            "Accept-Encoding": "gzip, deflate, br, zstd",
            "Referer": SENTINEL_REFERER,
            "Origin": "https://sentinel.openai.com",
            "User-Agent": DEFAULT_UA,
            "sec-ch-ua": DEFAULT_SEC_CH_UA,
            "sec-ch-ua-mobile": "?0",
            "sec-ch-ua-platform": '"Windows"',
            "Sec-Fetch-Dest": "empty",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Site": "same-origin",
        },
        "timeout": 20,
        "impersonate": "chrome",
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    resp = session.post(SENTINEL_REQ_URL, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"/sentinel/req 失败: HTTP {resp.status_code}, body={resp.text[:300]}")
    return resp.json()


def generate_sentinel_token(proxy: str = "", flow: str = "authorize_continue") -> dict:
    if not LOCAL_SDK_FILE.exists() or LOCAL_SDK_FILE.stat().st_size == 0:
        raise RuntimeError(f"sdk.js 不存在或为空, 请先运行 sentinel_updater.py 下载")

    if not QUICKJS_SCRIPT.exists():
        raise RuntimeError(f"openai_sentinel_quickjs.js 不存在: {QUICKJS_SCRIPT}")

    version = LOCAL_VERSION_FILE.read_text(encoding="utf-8").strip() if LOCAL_VERSION_FILE.exists() else "unknown"
    device_id = str(uuid.uuid4())

    log(f"开始生成 Sentinel Token")
    log(f"  版本: {version}")
    log(f"  device_id: {device_id}")
    log(f"  flow: {flow}")

    session = create_session(proxy)

    log("[1/4] Node 执行 requirements → 生成 request_p ...")
    requirements = run_node_action(
        session,
        action="requirements",
        sdk_file=LOCAL_SDK_FILE,
        payload={"device_id": device_id},
        timeout_ms=30000,
    )
    request_p = str(requirements.get("request_p") or "").strip()
    if not request_p:
        raise RuntimeError(f"requirements 未返回 request_p, 返回: {requirements}")
    log(f"  request_p 长度: {len(request_p)}")

    log("[2/4] POST /sentinel/req → 获取 challenge ...")
    challenge = fetch_challenge(session, device_id, flow, request_p, proxy)
    c_value = str(challenge.get("token") or "").strip()
    if not c_value:
        raise RuntimeError(f"challenge 缺少 token 字段, 返回: {json.dumps(challenge, ensure_ascii=False)[:300]}")
    pow_data = challenge.get("proofofwork") or {}
    log(f"  challenge.token 长度: {len(c_value)}")
    log(f"  PoW required: {pow_data.get('required')}, seed 长度: {len(str(pow_data.get('seed', '')))}")

    log("[3/4] Node 执行 solve → 解 PoW ...")
    solved = run_node_action(
        session,
        action="solve",
        sdk_file=LOCAL_SDK_FILE,
        payload={
            "device_id": device_id,
            "request_p": request_p,
            "challenge": challenge,
        },
        timeout_ms=30000,
    )
    final_p = str(solved.get("final_p") or solved.get("p") or "").strip()
    t_value = str(solved.get("t") or "").strip() if solved.get("t") is not None else ""
    if not final_p:
        raise RuntimeError(f"solve 未返回 final_p, 返回: {solved}")
    log(f"  final_p 长度: {len(final_p)}")
    log(f"  t 长度: {len(t_value)}")

    log("[4/4] 组装 Sentinel Token ...")
    token_payload = {"p": final_p, "t": t_value, "c": c_value, "id": device_id, "flow": flow}
    sentinel_token = json.dumps(token_payload, separators=(",", ":"), ensure_ascii=False)

    log(f"  Sentinel Token 生成成功! 总长度: {len(sentinel_token)}")

    return {
        "success": True,
        "sentinel_token": sentinel_token,
        "version": version,
        "device_id": device_id,
        "flow": flow,
        "p_len": len(final_p),
        "t_len": len(t_value),
        "c_len": len(c_value),
    }


def main():
    parser = argparse.ArgumentParser(description="Sentinel Token 纯协议生成")
    parser.add_argument("--proxy", default="", help="代理地址, 如 http://127.0.0.1:7897")
    parser.add_argument("--flow", default="authorize_continue", help="flow 类型, 默认 authorize_continue")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args()

    try:
        result = generate_sentinel_token(proxy=args.proxy, flow=args.flow)
    except Exception as e:
        log(f"生成失败: {e}")
        result = {"success": False, "error": str(e)}

    if args.json:
        output = dict(result)
        if "sentinel_token" in output:
            output["sentinel_token_preview"] = output["sentinel_token"][:100] + "..."
            del output["sentinel_token"]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    else:
        if result["success"]:
            log(f"完成! p_len={result['p_len']}, t_len={result['t_len']}, c_len={result['c_len']}")
        else:
            log(f"失败: {result.get('error', 'unknown')}")

    return 0 if result.get("success") else 1


if __name__ == "__main__":
    sys.exit(main())
