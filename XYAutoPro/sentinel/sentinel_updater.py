"""
Sentinel SDK 版本探测与更新脚本
================================
纯协议方式:
  1. GET /backend-api/sentinel/frame.html → 提取版本号
  2. 对比本地 sdk.js 版本 → 一致则跳过，不一致则下载替换
  3. 返回更新结果

用法:
  python sentinel_updater.py [--proxy http://127.0.0.1:7897]
"""

import argparse
import json
import os
import re
import sys
import tempfile
import time
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")

try:
    from curl_cffi import requests as curl_requests
except ImportError:
    import requests as curl_requests

FRAME_URL = "https://sentinel.openai.com/backend-api/sentinel/frame.html"
SDK_BASE = "https://sentinel.openai.com/sentinel"

CACHE_DIR = Path(tempfile.gettempdir()) / "sentinel_sdk"
CACHE_DIR.mkdir(parents=True, exist_ok=True)

LOCAL_VERSION_FILE = CACHE_DIR / "version.txt"
LOCAL_SDK_FILE = CACHE_DIR / "sdk.js"

DEFAULT_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
    "AppleWebKit/537.36 (KHTML, like Gecko) "
    "Chrome/145.0.0.0 Safari/537.36"
)


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def create_session(proxy: str = ""):
    session = curl_requests.Session()
    session.headers.update({
        "User-Agent": DEFAULT_UA,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "none",
        "Sec-Fetch-User": "?1",
    })
    return session


def fetch_frame_html(session, proxy: str = "") -> str:
    kwargs = {"timeout": 30, "impersonate": "chrome"}
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    resp = session.get(FRAME_URL, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"获取 frame.html 失败: HTTP {resp.status_code}")
    return resp.text


def extract_version(frame_html: str) -> str:
    patterns = [
        r'/sentinel/([a-zA-Z0-9]+)/sdk\.js',
        r'sentinel/([a-zA-Z0-9]+)/sdk\.js',
        r'"([a-zA-Z0-9]+)"\s*,\s*//\s*sentinel\s+version',
        r'sv=([a-zA-Z0-9]+)',
    ]
    for pattern in patterns:
        matches = re.findall(pattern, frame_html)
        if matches:
            version = matches[0]
            log(f"从 frame.html 提取版本号: {version} (pattern: {pattern})")
            return version
    raise RuntimeError(f"无法从 frame.html 提取版本号, HTML 长度={len(frame_html)}")


def get_local_version() -> str:
    if LOCAL_VERSION_FILE.exists():
        return LOCAL_VERSION_FILE.read_text(encoding="utf-8").strip()
    return ""


def download_sdk(session, version: str, proxy: str = "") -> bytes:
    sdk_url = f"{SDK_BASE}/{version}/sdk.js"
    kwargs = {
        "timeout": 60,
        "impersonate": "chrome",
        "headers": {
            "Accept": "*/*",
            "Referer": FRAME_URL,
            "Sec-Fetch-Dest": "script",
            "Sec-Fetch-Mode": "no-cors",
            "Sec-Fetch-Site": "same-origin",
        },
    }
    if proxy:
        kwargs["proxies"] = {"http": proxy, "https": proxy}
    log(f"下载 sdk.js: {sdk_url}")
    resp = session.get(sdk_url, **kwargs)
    if resp.status_code != 200:
        raise RuntimeError(f"下载 sdk.js 失败: HTTP {resp.status_code}")
    content = resp.content
    if not content:
        raise RuntimeError("下载 sdk.js 失败: 响应为空")
    log(f"下载完成, 大小: {len(content)} bytes")
    return content


def update_sentinel(proxy: str = "") -> dict:
    session = create_session(proxy)
    try:
        frame_html = fetch_frame_html(session, proxy)
    except Exception as e:
        log(f"获取 frame.html 失败: {e}")
        return {"success": False, "error": f"fetch_frame_failed: {e}"}

    try:
        remote_version = extract_version(frame_html)
    except Exception as e:
        log(f"提取版本号失败: {e}")
        return {"success": False, "error": f"extract_version_failed: {e}"}

    local_version = get_local_version()
    log(f"远程版本: {remote_version}, 本地版本: {local_version or '(无)'}")

    if remote_version == local_version and LOCAL_SDK_FILE.exists() and LOCAL_SDK_FILE.stat().st_size > 0:
        log("版本一致, 本地 sdk.js 有效, 跳过下载")
        return {
            "success": True,
            "version": remote_version,
            "action": "skipped",
            "sdk_path": str(LOCAL_SDK_FILE),
        }

    try:
        sdk_content = download_sdk(session, remote_version, proxy)
    except Exception as e:
        log(f"下载 sdk.js 失败: {e}")
        return {"success": False, "error": f"download_failed: {e}", "version": remote_version}

    backup_file = CACHE_DIR / f"sdk_{local_version}.js.bak" if local_version else None
    if backup_file and LOCAL_SDK_FILE.exists():
        try:
            LOCAL_SDK_FILE.rename(backup_file)
            log(f"旧版备份: {backup_file.name}")
        except Exception:
            pass

    LOCAL_SDK_FILE.write_bytes(sdk_content)
    LOCAL_VERSION_FILE.write_text(remote_version, encoding="utf-8")
    log(f"已更新 sdk.js (版本 {remote_version}, {len(sdk_content)} bytes)")

    return {
        "success": True,
        "version": remote_version,
        "action": "updated",
        "sdk_path": str(LOCAL_SDK_FILE),
        "sdk_size": len(sdk_content),
        "previous_version": local_version or None,
    }


def main():
    parser = argparse.ArgumentParser(description="Sentinel SDK 版本探测与更新")
    parser.add_argument("--proxy", default="", help="代理地址, 如 http://127.0.0.1:7897")
    parser.add_argument("--json", action="store_true", help="以 JSON 输出结果")
    args = parser.parse_args()

    result = update_sentinel(proxy=args.proxy)

    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        if result["success"]:
            action_text = "跳过下载" if result["action"] == "skipped" else "已更新"
            log(f"结果: {action_text}, 版本={result['version']}, 路径={result.get('sdk_path', '')}")
        else:
            log(f"失败: {result.get('error', 'unknown')}")

    return 0 if result["success"] else 1


if __name__ == "__main__":
    sys.exit(main())
