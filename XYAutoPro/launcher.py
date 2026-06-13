"""
XYAuto 多进程启动器
==================
输入进程数量，每隔20秒启动一个 XYAutoRs.py 子进程

用法:
  python launcher.py
  python launcher.py --count 3
  python launcher.py --count 3 --interval 20
"""

import argparse
import subprocess
import sys
import time
import os
import signal
from pathlib import Path

WORKDIR = Path(__file__).parent
TARGET_SCRIPT = WORKDIR / "core" / "register.py"

processes = []


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def launch_process(index: int, total: int):
    log(f"启动第 {index}/{total} 个进程 ...")
    proc = subprocess.Popen(
        [sys.executable, str(TARGET_SCRIPT)],
        cwd=str(WORKDIR),
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
    )
    processes.append({"index": index, "pid": proc.pid, "proc": proc})
    log(f"第 {index} 个进程已启动 PID={proc.pid}")


def monitor_processes():
    while True:
        alive = []
        for p in processes:
            ret = p["proc"].poll()
            if ret is None:
                alive.append(p)
            else:
                log(f"进程 PID={p['pid']} (第{p['index']}个) 已退出, 返回码={ret}")
        processes[:] = alive
        if not alive:
            log("所有子进程已退出")
            break
        time.sleep(5)


def cleanup(signum=None, frame=None):
    log(f"收到终止信号, 正在关闭 {len(processes)} 个子进程 ...")
    for p in processes:
        try:
            p["proc"].terminate()
            log(f"  终止 PID={p['pid']}")
        except Exception as e:
            log(f"  终止 PID={p['pid']} 失败: {e}")
    for p in processes:
        try:
            p["proc"].wait(timeout=10)
        except Exception:
            try:
                p["proc"].kill()
            except Exception:
                pass
    log("所有子进程已关闭")
    sys.exit(0)


def main():
    parser = argparse.ArgumentParser(description="XYAuto 多进程启动器")
    parser.add_argument("--count", "-n", type=int, default=0, help="启动的进程数量")
    parser.add_argument("--interval", "-i", type=int, default=20, help="进程启动间隔(秒)")
    args = parser.parse_args()

    log("=" * 50)
    log("  XYAuto 多进程启动器")
    log("=" * 50)

    if not TARGET_SCRIPT.exists():
        log(f"错误: 找不到 {TARGET_SCRIPT}")
        sys.exit(1)

    count = args.count
    if count <= 0:
        try:
            count = int(input("请输入要启动的进程数量: ").strip())
        except ValueError:
            log("输入无效, 退出")
            sys.exit(1)

    if count <= 0:
        log("进程数量必须大于0, 退出")
        sys.exit(1)

    interval = args.interval
    log(f"将启动 {count} 个进程, 间隔 {interval} 秒")
    log(f"目标脚本: {TARGET_SCRIPT}")

    if os.name == "nt":
        signal.signal(signal.SIGINT, cleanup)
        signal.signal(signal.SIGTERM, cleanup)

    for i in range(1, count + 1):
        launch_process(i, count)
        if i < count:
            log(f"等待 {interval} 秒后启动下一个进程 ...")
            try:
                time.sleep(interval)
            except KeyboardInterrupt:
                log("用户中断, 停止启动新进程")
                break

    log(f"全部 {len(processes)} 个进程已启动")
    log("按 Ctrl+C 终止所有进程")

    try:
        monitor_processes()
    except KeyboardInterrupt:
        cleanup()


if __name__ == "__main__":
    main()
