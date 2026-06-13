"""
XYAutoPro GUI 多进程启动器 (VS Code 风格分栏 - 极简高雅浅色版)
==========================================================
特点：
1. 采用高颜值的 Slate-Light 现代浅色配色系统，配合 Segoe UI / 微软雅黑等高清字体。
2. 左侧侧边栏展示当前所有子进程的状态，右侧日志面板支持联动过滤（选择单个进程则只看该进程的详细日志，选择“合并所有”查看联合流）。
3. 优雅停止 (Graceful Stop) 机制：通过生成全局 stop.flag 熔断信号，在不产生任何多余扣费的情况下安全退出子进程。
4. 紧急强杀 (Force Kill) 机制：多平台进程终止保护。
"""

import os
import sys
import time
import json
import uuid
import queue
import signal
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, messagebox, scrolledtext
import re

# 路径初始化
WORKDIR = Path(__file__).parent.resolve()
TARGET_SCRIPT = WORKDIR / "core" / "register.py"
STOP_FLAG_FILE = WORKDIR / "stop.flag"

# 极简高雅浅色配色系统 (Slate-Light)
CLR_BG = "#F8FAFC"          # 全局主背景 (Slate 50)
CLR_CARD = "#FFFFFF"        # 卡片/容器纯白背景
CLR_BORDER = "#E2E8F0"      # 极细边框线 (Slate 200)
CLR_TEXT_PRIMARY = "#0F172A" # 主要标题/文字 (Slate 900)
CLR_TEXT_SEC = "#64748B"    # 次要说明/标签 (Slate 500)
CLR_PRIMARY = "#4F46E5"     # 科技靛蓝 (Indigo 600)
CLR_PRIMARY_HOVER = "#4338CA"# 靛蓝 Hover
CLR_SUCCESS = "#0D9488"     # 优质柔和青绿 (Teal 600)
CLR_WARN = "#EA580C"        # 柔和暖橙 (Orange 600)
CLR_DANGER = "#E11D48"      # 柔和玫瑰红 (Rose 600)
CLR_CONSOLE_BG = "#F1F5F9"  # 终端控制台浅灰底色 (Slate 100)
CLR_CONSOLE_TEXT = "#1E293B"# 终端主文本碳黑 (Slate 800)

FONT_PRIMARY = "Segoe UI"
FONT_CHINESE = "Microsoft YaHei"
FONT_CONSOLE = "Consolas"

class ModernApp:
    def __init__(self, root):
        self.root = root
        self.root.title("XYAutoPro - 多进程智能控制台 (极简版)")
        self.root.geometry("1150x780") # 稍微增加高度以完美容纳大屏看板
        self.root.minsize(980, 680)
        self.root.configure(bg=CLR_BG)

        # 数据库挂钩与初始化
        sys.path.insert(0, str(WORKDIR / "core"))
        from phone_db import PhoneDB
        self.db = PhoneDB()

        # 状态参数
        self.is_running = False
        self.processes = []           # 存储子进程字典 {"index": int, "pid": int, "proc": Popen, "status": str, "round": int, "action": str}
        self.log_queue = queue.Queue() # 线程安全日志队列
        self.logs_cache = {"all": []}  # 存储所有进程的独立日志行缓存
        self.selected_proc_key = "all" # 当前在右侧日志框选择显示的进程 key ("all" 或 PID)
        self.round_counters = {}       # 进程 PID -> 当前轮数

        # 样式定制
        self._setup_styles()
        
        # 页面布局
        self._build_ui()

        # 首次即时渲染历史累积指标看板
        self._update_dashboard_metrics()

        # 启动后台UI刷新定时器
        self.root.after(100, self._process_log_queue_loop)
        self.root.after(1000, self._monitor_processes_loop)

        # 初始清理
        self._cleanup_stop_flag()

    def _setup_styles(self):
        style = ttk.Style()
        style.theme_use("clam")

        # 全局 Frame 样式
        style.configure("TFrame", background=CLR_BG)
        style.configure("Card.TFrame", background=CLR_CARD, relief="flat")

        # Label 字体
        style.configure("TLabel", background=CLR_BG, foreground=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 10))
        style.configure("Title.TLabel", background=CLR_BG, foreground=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 17, "bold"))
        style.configure("Sub.TLabel", background=CLR_BG, foreground=CLR_TEXT_SEC, font=(FONT_PRIMARY, 9))
        
        style.configure("CardTitle.TLabel", background=CLR_CARD, foreground=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 11, "bold"))
        style.configure("CardText.TLabel", background=CLR_CARD, foreground=CLR_TEXT_SEC, font=(FONT_PRIMARY, 9))

        # Tab TreeView 表格美化 (浅色简约风格)
        style.configure("Treeview",
                        background=CLR_CARD,
                        foreground=CLR_TEXT_PRIMARY,
                        fieldbackground=CLR_CARD,
                        rowheight=35,
                        font=(FONT_PRIMARY, 9),
                        borderwidth=0)
        
        # 行选择高亮 (清爽的淡靛蓝底色与深色文字)
        style.map("Treeview", 
                  background=[("selected", "#EEF2FF")], 
                  foreground=[("selected", CLR_PRIMARY)])
        
        style.configure("Treeview.Heading",
                        background=CLR_BG,
                        foreground=CLR_TEXT_SEC,
                        font=(FONT_PRIMARY, 9, "bold"),
                        borderwidth=1,
                        relief="flat")

    def _build_ui(self):
        # 顶栏 Header
        header_frame = ttk.Frame(self.root, style="TFrame")
        header_frame.pack(fill="x", padx=28, pady=(20, 10))

        title_lbl = ttk.Label(header_frame, text="XYAutoPro 多进程智能控制台", style="Title.TLabel")
        title_lbl.pack(anchor="w")
        sub_lbl = ttk.Label(header_frame, text="极速注册 · 纯协议版自动化控制中心 · 交互式 VS Code 风格日志分流器", style="Sub.TLabel")
        sub_lbl.pack(anchor="w", pady=4)

        # 参数配置与控制卡片
        ctrl_card = tk.Frame(self.root, bg=CLR_CARD, highlightbackground=CLR_BORDER, highlightcolor=CLR_BORDER, highlightthickness=1, bd=0)
        ctrl_card.pack(fill="x", padx=28, pady=8)

        # 内衬 Padding Frame
        inner_ctrl = tk.Frame(ctrl_card, bg=CLR_CARD, padx=20, pady=16)
        inner_ctrl.pack(fill="both", expand=True)

        # 进程数量标签与文本框
        tk.Label(inner_ctrl, text="启动子进程数:", bg=CLR_CARD, fg=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 9, "bold")).grid(row=0, column=0, padx=6, sticky="w")
        
        self.proc_count_entry = tk.Entry(inner_ctrl, width=6, bg=CLR_BG, fg=CLR_TEXT_PRIMARY, insertbackground=CLR_TEXT_PRIMARY, relief="flat", highlightbackground=CLR_BORDER, highlightcolor=CLR_PRIMARY, highlightthickness=1, font=(FONT_CONSOLE, 10, "bold"), justify="center")
        self.proc_count_entry.insert(0, "3")
        self.proc_count_entry.grid(row=0, column=1, padx=6)

        # 启动间隔标签与文本框
        tk.Label(inner_ctrl, text="启动间隔秒数:", bg=CLR_CARD, fg=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 9, "bold")).grid(row=0, column=2, padx=12, sticky="w")
        
        self.interval_entry = tk.Entry(inner_ctrl, width=6, bg=CLR_BG, fg=CLR_TEXT_PRIMARY, insertbackground=CLR_TEXT_PRIMARY, relief="flat", highlightbackground=CLR_BORDER, highlightcolor=CLR_PRIMARY, highlightthickness=1, font=(FONT_CONSOLE, 10, "bold"), justify="center")
        self.interval_entry.insert(0, "20")
        self.interval_entry.grid(row=0, column=3, padx=6)

        # 弹性空白占位
        inner_ctrl.columnconfigure(4, weight=1)

        # 按钮控制区 (扁平化高雅设计，摒弃老旧凸起边框)
        self.btn_start = tk.Button(inner_ctrl, text="🚀  开始任务", bg=CLR_SUCCESS, fg="#FFFFFF", activebackground=CLR_SUCCESS, activeforeground="#FFFFFF", font=(FONT_PRIMARY, 9, "bold"), relief="flat", bd=0, padx=20, pady=8, cursor="hand2", command=self._start_task)
        self.btn_start.grid(row=0, column=5, padx=6)

        self.btn_grace_stop = tk.Button(inner_ctrl, text="🛑  优雅停止", bg=CLR_WARN, fg="#FFFFFF", activebackground=CLR_WARN, activeforeground="#FFFFFF", font=(FONT_PRIMARY, 9, "bold"), relief="flat", bd=0, padx=20, pady=8, cursor="hand2", command=self._graceful_stop)
        self.btn_grace_stop.grid(row=0, column=6, padx=6)

        self.btn_force_kill = tk.Button(inner_ctrl, text="⚡  紧急强杀", bg=CLR_DANGER, fg="#FFFFFF", activebackground=CLR_DANGER, activeforeground="#FFFFFF", font=(FONT_PRIMARY, 9, "bold"), relief="flat", bd=0, padx=20, pady=8, cursor="hand2", command=self._force_kill)
        self.btn_force_kill.grid(row=0, column=7, padx=6)

        # ==================== 数据大屏大局看板 (Metrics Ribbon) ====================
        stats_ribbon = ttk.Frame(self.root, style="TFrame")
        stats_ribbon.pack(fill="x", padx=28, pady=(8, 4))
        
        # 4列等宽拉伸布局
        stats_ribbon.columnconfigure(0, weight=1)
        stats_ribbon.columnconfigure(1, weight=1)
        stats_ribbon.columnconfigure(2, weight=1)
        stats_ribbon.columnconfigure(3, weight=1)
        
        # 卡片快捷创建函数，确保像素级极致比例与浅色卡片化风格
        def create_stat_card(parent, col, title, initial_val, subtext, pad_left=False, pad_right=False):
            px_left = 6 if not pad_left else 0
            px_right = 6 if not pad_right else 0
            
            card = tk.Frame(parent, bg=CLR_CARD, highlightbackground=CLR_BORDER, highlightcolor=CLR_BORDER, highlightthickness=1, bd=0)
            card.grid(row=0, column=col, padx=(px_left, px_right), sticky="nsew")
            
            inner = tk.Frame(card, bg=CLR_CARD, padx=16, pady=12)
            inner.pack(fill="both", expand=True)
            
            lbl_title = tk.Label(inner, text=title, bg=CLR_CARD, fg=CLR_TEXT_SEC, font=(FONT_PRIMARY, 9, "bold"))
            lbl_title.pack(anchor="w")
            
            lbl_val = tk.Label(inner, text=initial_val, bg=CLR_CARD, fg=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 16, "bold"))
            lbl_val.pack(anchor="w", pady=(4, 2))
            
            lbl_sub = tk.Label(inner, text=subtext, bg=CLR_CARD, fg=CLR_TEXT_SEC, font=(FONT_PRIMARY, 8))
            lbl_sub.pack(anchor="w")
            
            return lbl_val, lbl_sub

        self.lbl_val_accounts, self.lbl_sub_accounts = create_stat_card(
            stats_ribbon, 0, "🏆  账号产出 (成功数)", "0 个", "成功率: 0.0% | 库总计: 0", pad_left=True
        )
        self.lbl_val_cost, self.lbl_sub_cost = create_stat_card(
            stats_ribbon, 1, "💰  接码开支 (财务消耗)", "$0.00", "平均成本: $0.000 / 号"
        )
        self.lbl_val_shield, self.lbl_sub_shield = create_stat_card(
            stats_ribbon, 2, "🛡️  接码防线 (风控自愈)", "0 封锁 | 0 优质", "动态阻断黑名单/优质优先"
        )
        self.lbl_val_speed, self.lbl_sub_speed = create_stat_card(
            stats_ribbon, 3, "⚡  产出速率 (吞吐效能)", "0 RPH", "过去1小时成功 | 活跃进程: 0", pad_right=True
        )

        # 主显示工作区 (双栏 Split-Screen Frame)
        main_workspace = ttk.Frame(self.root, style="TFrame")
        main_workspace.pack(fill="both", expand=True, padx=28, pady=12)

        # 左侧栏 (进程监控 Treeview 看板)
        left_frame = tk.Frame(main_workspace, bg=CLR_CARD, highlightbackground=CLR_BORDER, highlightcolor=CLR_BORDER, highlightthickness=1, bd=0)
        left_frame.pack(side="left", fill="both", expand=True, padx=(0, 10))

        left_inner = tk.Frame(left_frame, bg=CLR_CARD, padx=14, pady=14)
        left_inner.pack(fill="both", expand=True)

        tk.Label(left_inner, text="进程监控看板 (单击行切换日志)", bg=CLR_CARD, fg=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 10, "bold")).pack(anchor="w", pady=(0, 8))

        columns = ("index", "pid", "status", "round", "action")
        self.tree = ttk.Treeview(left_inner, columns=columns, show="headings", selectmode="browse")
        self.tree.heading("index", text="进程")
        self.tree.heading("pid", text="PID")
        self.tree.heading("status", text="状态")
        self.tree.heading("round", text="轮次")
        self.tree.heading("action", text="当前步骤/动作")

        self.tree.column("index", width=55, minwidth=45, anchor="center")
        self.tree.column("pid", width=75, minwidth=65, anchor="center")
        self.tree.column("status", width=85, minwidth=75, anchor="center")
        self.tree.column("round", width=65, minwidth=55, anchor="center")
        self.tree.column("action", width=145, minwidth=110, anchor="w")
        self.tree.pack(fill="both", expand=True)

        # 绑定 Treeview 的选择事件
        self.tree.bind("<<TreeviewSelect>>", self._on_treeview_select)

        # 右侧栏 (VS Code 风格的分流终端)
        right_frame = tk.Frame(main_workspace, bg=CLR_CARD, highlightbackground=CLR_BORDER, highlightcolor=CLR_BORDER, highlightthickness=1, bd=0)
        right_frame.pack(side="right", fill="both", expand=True, padx=(10, 0))

        right_inner = tk.Frame(right_frame, bg=CLR_CARD, padx=14, pady=14)
        right_inner.pack(fill="both", expand=True)

        # 终端标题区
        terminal_header = tk.Frame(right_inner, bg=CLR_CARD)
        terminal_header.pack(fill="x", pady=(0, 8))

        self.terminal_title = tk.Label(terminal_header, text="📟  实时联合控制台日志 (合并全部进程)", bg=CLR_CARD, fg=CLR_TEXT_PRIMARY, font=(FONT_PRIMARY, 10, "bold"))
        self.terminal_title.pack(side="left")

        # 快速查看“合并流”按钮
        self.btn_view_all = tk.Button(terminal_header, text="显示合并日志", bg=CLR_BG, fg=CLR_PRIMARY, activebackground=CLR_BORDER, font=(FONT_PRIMARY, 8, "bold"), relief="flat", highlightbackground=CLR_BORDER, highlightcolor=CLR_BORDER, highlightthickness=1, bd=0, padx=10, pady=3, cursor="hand2", command=self._select_all_logs)
        self.btn_view_all.pack(side="right")

        # 终端控制台核心文本区 (高品质护眼浅色终端背景)
        self.console = scrolledtext.ScrolledText(right_inner, bg=CLR_CONSOLE_BG, fg=CLR_CONSOLE_TEXT, insertbackground=CLR_CONSOLE_TEXT, selectbackground="#DDE2FF", relief="flat", bd=0, font=(FONT_CONSOLE, 9), padx=8, pady=8)
        self.console.pack(fill="both", expand=True)
        
        # 配置控制台文字颜色标签
        self.console.tag_config("sys", foreground=CLR_TEXT_SEC)    # 系统灰
        self.console.tag_config("proc_tag", foreground=CLR_PRIMARY, font=(FONT_CONSOLE, 9, "bold")) # 进程标签蓝
        self.console.tag_config("success", foreground=CLR_SUCCESS, font=(FONT_CONSOLE, 9, "bold")) # 成功青绿
        self.console.tag_config("warn", foreground=CLR_WARN)       # 警告暖橙
        self.console.tag_config("danger", foreground=CLR_DANGER, font=(FONT_CONSOLE, 9, "bold"))   # 错误红
        self.console.tag_config("normal", foreground=CLR_CONSOLE_TEXT) # 普通碳黑

    # ----------------- 逻辑控制区 -----------------

    def _cleanup_stop_flag(self):
        """清除残留的 stop.flag 信号文件"""
        if STOP_FLAG_FILE.exists():
            try:
                STOP_FLAG_FILE.unlink()
                self._append_log("sys", f"[系统] 成功清理残留停止标记 stop.flag。")
            except Exception as e:
                self._append_log("sys", f"[系统] 清理 stop.flag 失败: {e}")

    def _start_task(self):
        """点击开始任务"""
        if self.is_running:
            messagebox.showwarning("警告", "多进程任务当前已处于运行状态，无需重复开始。")
            return

        # 校验输入
        try:
            proc_count = int(self.proc_count_entry.get().strip())
            interval = int(self.interval_entry.get().strip())
        except ValueError:
            messagebox.showerror("错误", "子进程数或间隔秒数输入必须为有效正整数！")
            return

        if proc_count <= 0 or interval < 0:
            messagebox.showerror("错误", "进程数必须大于0，间隔不能小于0。")
            return

        if not TARGET_SCRIPT.exists():
            messagebox.showerror("错误", f"找不到核心注册脚本路径：\n{TARGET_SCRIPT}")
            return

        # 清除残留停止标记
        self._cleanup_stop_flag()

        # 初始化状态
        self.is_running = True
        self.processes.clear()
        self.logs_cache = {"all": []}
        self.round_counters.clear()
        self._clear_console_widget()
        self._clear_treeview()

        self.btn_start.configure(state="disabled", bg=CLR_BORDER, fg=CLR_TEXT_SEC, cursor="arrow")
        self._append_log("sys", f"[控制中心] 🚀 任务启动：共计 {proc_count} 个进程，每隔 {interval} 秒启动一个...")

        # 启动子线程分批拉起子进程
        threading.Thread(target=self._launch_processes_thread, args=(proc_count, interval), daemon=True).start()

    def _launch_processes_thread(self, total: int, interval: int):
        """分批拉起进程的专属工作线程"""
        for i in range(1, total + 1):
            if not self.is_running or STOP_FLAG_FILE.exists():
                break

            self._append_log("sys", f"[控制中心] 启动第 {i}/{total} 个子进程...")
            try:
                # 隐藏子进程 cmd 控制台以便由 GUI 接管 stdout/stderr
                startupinfo = None
                creationflags = 0
                if os.name == "nt":
                    startupinfo = subprocess.STARTUPINFO()
                    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
                    startupinfo.wShowWindow = subprocess.SW_HIDE
                    creationflags = 0 

                proc = subprocess.Popen(
                    [sys.executable, str(TARGET_SCRIPT)],
                    cwd=str(WORKDIR),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,  # 合并标准错误输出
                    text=True,
                    encoding="utf-8",
                    errors="replace",
                    bufsize=1,  # 行缓冲
                    startupinfo=startupinfo,
                    creationflags=creationflags
                )

                pid = proc.pid
                proc_info = {
                    "index": i,
                    "pid": pid,
                    "proc": proc,
                    "status": "🟢 运行中",
                    "round": 1,
                    "action": "JP住宅代理获取中..."
                }

                # 存入列表与标签页缓存
                self.processes.append(proc_info)
                self.logs_cache[str(pid)] = []
                self.round_counters[pid] = 1

                # 在 Treeview 中插入行数据
                self.root.after(0, self._insert_treeview_row, proc_info)
                self._append_log("sys", f"[控制中心] 子进程 #{i} 成功启动，PID={pid}")

                # 启动非阻塞读子进程输出的后台监控线程
                threading.Thread(target=self._read_subprocess_output, args=(i, pid, proc), daemon=True).start()

            except Exception as e:
                self._append_log("sys", f"[控制中心] ❌ 子进程 #{i} 启动失败: {e}")

            # 启动间隔睡眠
            if i < total:
                for _ in range(interval):
                    if not self.is_running or STOP_FLAG_FILE.exists():
                        break
                    time.sleep(1)

        self._append_log("sys", f"[控制中心] 🌟 所有子进程分配与调度执行线程完毕。")

    def _read_subprocess_output(self, index: int, pid: int, proc: subprocess.Popen):
        """单独读取子进程输出的线程，避免 I/O 阻塞"""
        while True:
            line = proc.stdout.readline()
            if not line:
                break
            
            self.log_queue.put({"index": index, "pid": pid, "text": line})

        # 子进程输出读取完毕，代表该进程已关闭
        proc.wait()
        ret = proc.poll()
        self.log_queue.put({"index": index, "pid": pid, "sys_exit": True, "code": ret})

    def _process_log_queue_loop(self):
        """UI 主线程专职拉取日志队列渲染 UI (Thread-safe)"""
        try:
            while True:
                item = self.log_queue.get_nowait()
                pid = item.get("pid")
                idx = item.get("index")

                # 如果是进程退出信号
                if item.get("sys_exit"):
                    code = item.get("code")
                    self._append_log("sys", f"[系统] 进程 PID={pid} (第 {idx} 个) 已安全退出，返回码={code}")
                    self._update_process_status(pid, "status", "⚪ 已退出")
                    self._update_process_status(pid, "action", f"进程已完全结束 (退出码 {code})")
                    continue

                raw_text = item.get("text", "")
                text = raw_text.rstrip("\r\n")
                if not text:
                    continue

                # 1. 解析核心控制动作 (根据日志标志解析并动态反馈到左侧看板)
                action = self._parse_log_action(text)
                if action:
                    self._update_process_status(pid, "action", action)

                # 2. 解析当前轮次变化
                round_num = self._parse_log_round(text)
                if round_num:
                    self.round_counters[pid] = round_num
                    self._update_process_status(pid, "round", f"第 {round_num} 轮")

                # 3. 追加到多端日志缓存 (动态初始化，完美防范任何多线程竞争)
                formatted_line = (idx, pid, text)
                self.logs_cache["all"].append(formatted_line)
                pid_key = str(pid)
                if pid_key not in self.logs_cache:
                    self.logs_cache[pid_key] = []
                self.logs_cache[pid_key].append(formatted_line)

                # 4. 判断当前是否需要动态显示在终端上
                if self.selected_proc_key == "all" or self.selected_proc_key == str(pid):
                    self._write_to_console(idx, pid, text)

        except queue.Empty:
            pass

        self.root.after(80, self._process_log_queue_loop)

    def _monitor_processes_loop(self):
        """主线程定时循环：轮询子进程存活状态，并刷新实时大屏看板"""
        self._update_dashboard_metrics()

        if self.is_running:
            any_alive = False
            for p in self.processes:
                ret = p["proc"].poll()
                if ret is None:
                    any_alive = True
                    if STOP_FLAG_FILE.exists():
                        self._update_process_status(p["pid"], "status", "🟡 停止中")
                else:
                    self._update_process_status(p["pid"], "status", "⚪ 已退出")

            if not any_alive and len(self.processes) > 0:
                self.is_running = False
                self._append_log("sys", "[控制中心] 🏁 所有子进程均已关闭退出。任务流结束。")
                self.btn_start.configure(state="normal", bg=CLR_SUCCESS, fg="#FFFFFF", cursor="hand2")
                self._cleanup_stop_flag()

        self.root.after(1000, self._monitor_processes_loop)

    def _update_dashboard_metrics(self):
        """计算并动态渲染大屏上的各项高级业务指标"""
        try:
            stats = self.db.stats()
            
            # 1. 账号产出看板更新
            success_count = stats.get("accounts", 0)
            total_records = stats.get("total", 0)
            success_rate = 0.0
            if total_records > 0:
                success_rate = (stats.get("success", 0) / total_records) * 100
                
            self.lbl_val_accounts.configure(text=f"{success_count} 个")
            self.lbl_sub_accounts.configure(text=f"注册成功率: {success_rate:.1f}% | 库总计: {total_records}")
            
            # 2. 财务预算看板更新 (精确聚合已成功注册的号码扣费)
            total_cost_row = self.db.conn.execute("SELECT SUM(sms_cost) FROM accounts").fetchone()
            total_cost = total_cost_row[0] if total_cost_row and total_cost_row[0] else 0.0
            avg_cost_row = self.db.conn.execute("SELECT AVG(sms_cost) FROM accounts").fetchone()
            avg_cost = avg_cost_row[0] if avg_cost_row and avg_cost_row[0] else 0.0
            
            self.lbl_val_cost.configure(text=f"${total_cost:.2f}")
            self.lbl_sub_cost.configure(text=f"每号平均卡费: ${avg_cost:.3f}")
            
            # 3. 采购风控防线指标更新
            blacklisted_count = len(self.db.get_blacklisted_operators("dr"))
            premium_count = len(self.db.get_premium_operators("dr"))
            
            self.lbl_val_shield.configure(text=f"{blacklisted_count} 封锁 | {premium_count} 优质")
            self.lbl_sub_shield.configure(text="失败10次自动拉黑 | 成功20次极速优先")
            
            # 4. 注册速率指标更新
            rph_row = self.db.conn.execute(
                "SELECT COUNT(*) FROM accounts WHERE created_at >= datetime('now', '-1 hour', 'localtime')"
            ).fetchone()
            rph = rph_row[0] if rph_row else 0
            
            active_count = sum(1 for p in self.processes if p["proc"].poll() is None)
            
            self.lbl_val_speed.configure(text=f"{rph} RPH")
            self.lbl_sub_speed.configure(text=f"近1小时产出 | 活跃子进程数: {active_count}")
            
        except Exception:
            # 容错降级，保障在偶发锁冲突时GUI正常流畅渲染
            pass

    # ----------------- 优雅停止 & 强杀 -----------------

    def _graceful_stop(self):
        """触发优雅停止"""
        if not self.is_running:
            messagebox.showinfo("提示", "当前无运行中的任务，无需停止。")
            return

        try:
            STOP_FLAG_FILE.write_text(str(int(time.time())), encoding="utf-8")
            self._append_log("sys", "[控制中心] 🛑 已下发 [优雅停止] 指令！全局 stop.flag 文件创建成功。")
            self._append_log("sys", "[控制中心] 提示：子进程将在完成当前步骤或准备下一次拿号前自动拦截并安全退出。")
            
            for p in self.processes:
                if p["proc"].poll() is None:
                    self._update_process_status(p["pid"], "status", "🟡 停止中")
                    self._update_process_status(p["pid"], "action", "优雅安全收尾退出中...")

        except Exception as e:
            messagebox.showerror("错误", f"无法创建优雅停止标记文件: {e}")

    def _force_kill(self):
        """紧急强制杀进程"""
        if not self.processes:
            messagebox.showinfo("提示", "当前没有任何被启动的进程记录。")
            return

        confirm = messagebox.askyesno("紧急强杀确认", "警告：紧急强杀将立即向子进程下发 SIGKILL，可能导致接码平台有部分锁定的号码未来得及注销释放而扣费！\n\n确定执行强行终止吗？")
        if not confirm:
            return

        self._append_log("sys", "[控制中心] ⚡ 正在执行 [紧急强杀] 命令，清除所有子进程...")
        self.is_running = False

        killed_count = 0
        for p in self.processes:
            proc = p["proc"]
            pid = p["pid"]
            if proc.poll() is None:
                try:
                    if os.name == "nt":
                        subprocess.run(f"taskkill /F /PID {pid}", shell=True, capture_output=True)
                    else:
                        proc.kill()
                    killed_count += 1
                    self._update_process_status(pid, "status", "🔴 强杀")
                    self._update_process_status(pid, "action", "已被强制终止")
                except Exception as e:
                    self._append_log("sys", f"[系统] 强杀 PID={pid} 失败: {e}")

        self._append_log("sys", f"[控制中心] ⚡ 强制清理完毕，成功强杀 {killed_count} 个进程。")
        self.btn_start.configure(state="normal", bg=CLR_SUCCESS, fg="#FFFFFF", cursor="hand2")
        self._cleanup_stop_flag()

    # ----------------- 日志分流切换面板 -----------------

    def _on_treeview_select(self, event):
        """点击左侧列表行，无缝切换右侧日志面板"""
        selected = self.tree.selection()
        if not selected:
            return
        
        # selected[0] 就是行 iid，我们在 insert 时明确指定为 str(p["pid"])，这绝对是精确的 PID 字符串
        pid_key = str(selected[0])
        
        # 获取 values 用于提取展示用的进程编号 (例如 #1)
        item = self.tree.item(selected[0])
        values = item.get("values")
        idx = values[0] if values else "?"
        
        self._switch_to_log_pane(pid_key, f"📟  进程 {idx} (PID {pid_key}) 专属终端控制台日志")

    def _select_all_logs(self):
        """切换回合并全部日志视图"""
        self._switch_to_log_pane("all", "📟  实时联合控制台日志 (合并全部进程)")

    def _switch_to_log_pane(self, key: str, title: str):
        """重刷右侧 Text 控件展示指定缓冲"""
        self.selected_proc_key = key
        self.terminal_title.configure(text=title)
        self._clear_console_widget()

        lines = self.logs_cache.get(key, [])
        for idx, pid, text in lines:
            self._write_to_console(idx, pid, text)

    # ----------------- 数据流解析器 -----------------

    def _parse_log_action(self, text: str) -> str:
        """根据核心日志分析出子进程最近的物理状态"""
        if "获取JP住宅代理" in text:
            return "JP住宅代理获取中..."
        if "动作:验证代理" in text:
            return "代理可用性验证中"
        if "提取 Sentinel token" in text:
            return "提取 OpenAI 盾牌(Sentinel)"
        if "获取号码" in text:
            m = re.search(r"获取号码.*: (\+\d+)", text)
            return f"接码中: {m.group(1)}" if m else "获取接码号码中..."
        if "建立 Auth session" in text:
            return "初始化登录握手..."
        if "检查重定向" in text:
            return "安全重定向验证"
        if "注册 (提交手机号+密码)" in text:
            return "提交 OpenAI 账号注册中..."
        if "等待验证码" in text:
            return "等待接收验证码(轮询)..."
        if "验证码已发送" in text:
            return "已发送短信，等待码中"
        if "动作:获取验证码 | 状态:OK" in text:
            return "🟢 成功接收验证码"
        if "验证 OTP" in text:
            return "验证码 OTP 提交中..."
        if "创建账户" in text:
            return "注册成功，建立个人资料..."
        if "策略Z-reauthorize" in text:
            return "获取最终 AccessToken..."
        if "注册成功!" in text:
            return "🎉 恭喜！本轮注册大功告成"
        if "已成功释放号码" in text or "取消激活成功" in text:
            return "释放当前号码，防扣费"
        if "优雅停止" in text:
            return "收到优雅停止信号"
        
        return ""

    def _parse_log_round(self, text: str) -> int:
        """从日志中解析当前进行到了第几轮"""
        m = re.search(r"第 (\d+) 轮", text)
        if m:
            return int(m.group(1))
        return None

    # ----------------- UI 增删改工具方法 -----------------

    def _insert_treeview_row(self, p: dict):
        """插入 Treeview 行"""
        self.tree.insert("", "end", iid=str(p["pid"]), values=(
            f"#{p['index']}",
            p["pid"],
            p["status"],
            f"第 {p['round']} 轮",
            p["action"]
        ))

    def _update_process_status(self, pid: int, col: str, val: str):
        """动态修改 Treeview 某一行某一列的值"""
        pid_str = str(pid)
        if self.tree.exists(pid_str):
            col_map = {"index": 0, "pid": 1, "status": 2, "round": 3, "action": 4}
            idx = col_map.get(col)
            if idx is not None:
                current_vals = list(self.tree.item(pid_str, "values"))
                current_vals[idx] = val
                self.tree.item(pid_str, values=current_vals)

    def _clear_treeview(self):
        for child in self.tree.get_children():
            self.tree.delete(child)

    def _append_log(self, tag_type: str, text: str):
        """系统日志追加"""
        formatted_line = ("SYS", 0, text)
        self.logs_cache["all"].append(formatted_line)
        if self.selected_proc_key == "all":
            self._write_to_console("SYS", 0, text)

    def _write_to_console(self, idx, pid, text: str):
        """将文字带颜色渲染进 Text 终端文本框中"""
        self.console.configure(state="normal")
        
        if idx == "SYS":
            self.console.insert(tk.END, f"[{time.strftime('%H:%M:%S')}] {text}\n", "sys")
        else:
            tag = "normal"
            if "FAIL" in text or "❌" in text or "错误" in text or "异常" in text:
                tag = "danger"
            elif "OK" in text or "🎉" in text or "成功" in text:
                tag = "success"
            elif "🛑" in text or "⚠️" in text or "警告" in text or "超时" in text:
                tag = "warn"

            # 插入前缀进程标识 [#1 - PID 123] 
            self.console.insert(tk.END, f"[#{idx} | PID {pid}] ", "proc_tag")
            self.console.insert(tk.END, f"{text}\n", tag)

        self.console.configure(state="disabled")
        self.console.see(tk.END) # 始终滚动到底部

    def _clear_console_widget(self):
        self.console.configure(state="normal")
        self.console.delete("1.0", tk.END)
        self.console.configure(state="disabled")

# ----------------- 启动入口 -----------------
if __name__ == "__main__":
    # Windows 环境下支持高DPI清晰度缩放
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            pass
            
    root = tk.Tk()
    app = ModernApp(root)
    
    def on_closing():
        if app.is_running:
            if messagebox.askyesno("退出确认", "当前有多进程注册任务正在运行！优雅退出可避免产生无用资费。\n\n您确定要【强制关闭】GUI程序吗？"):
                app._force_kill()
                root.destroy()
        else:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_closing)
    root.mainloop()
