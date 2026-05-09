#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Outlook 邮件管理系统 - Windows GUI 启动器
"""

import logging
import os
import queue
import secrets
import shutil
import sys
import threading
import webbrowser
from pathlib import Path
from tkinter import messagebox, scrolledtext, ttk
from typing import Optional
import tkinter as tk


# ─── 路径工具 ─────────────────────────────────────────────────────────────────

def app_dir() -> Path:
    """返回 exe 所在目录（打包后）或脚本所在目录（源码运行时）"""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


# ─── .env 处理 ────────────────────────────────────────────────────────────────

def ensure_env() -> None:
    """确保 .env 文件存在且包含有效 SECRET_KEY"""
    env_path = app_dir() / ".env"
    env_example = app_dir() / ".env.example"

    if not env_path.exists():
        if env_example.exists():
            shutil.copy(str(env_example), str(env_path))
        else:
            env_path.write_text("", encoding="utf-8")

    lines = env_path.read_text(encoding="utf-8").splitlines(keepends=True)
    sk_index = -1
    sk_value = None

    for i, line in enumerate(lines):
        if line.strip().startswith("SECRET_KEY="):
            sk_index = i
            sk_value = line.split("=", 1)[1].strip()
            break

    if sk_value in (None, "", "your-secret-key-here"):
        new_key = secrets.token_hex(32)
        if sk_index >= 0:
            lines[sk_index] = f"SECRET_KEY={new_key}\n"
        else:
            lines.insert(0, f"SECRET_KEY={new_key}\n")
        env_path.write_text("".join(lines), encoding="utf-8")


# ─── 日志队列 Handler ─────────────────────────────────────────────────────────

class QueueHandler(logging.Handler):
    def __init__(self, log_queue: "queue.Queue[str]") -> None:
        super().__init__()
        self.log_queue = log_queue

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.log_queue.put_nowait(self.format(record))
        except Exception:
            pass


# ─── Flask 服务器线程 ──────────────────────────────────────────────────────────

class FlaskServerThread(threading.Thread):
    def __init__(self, flask_app, host: str, port: int) -> None:
        super().__init__(daemon=True, name="FlaskServer")
        self.flask_app = flask_app
        self.host = host
        self.port = port
        self._server = None
        self._started = threading.Event()
        self._error: Optional[Exception] = None

    def run(self) -> None:
        from werkzeug.serving import make_server
        try:
            self._server = make_server(self.host, self.port, self.flask_app)
            self._started.set()
            self._server.serve_forever()
        except Exception as exc:
            self._error = exc
            self._started.set()

    def shutdown(self) -> None:
        if self._server:
            self._server.shutdown()

    def wait_started(self, timeout: float = 8.0) -> bool:
        return self._started.wait(timeout=timeout)


# ─── 主 GUI ───────────────────────────────────────────────────────────────────

class LauncherApp:
    DEFAULT_PORT = 5000

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("Outlook 邮件管理系统")
        self.root.resizable(True, True)
        self._server_thread: Optional[FlaskServerThread] = None
        self._log_queue: "queue.Queue[str]" = queue.Queue()
        self._flask_app = None
        self._build_ui()
        self._poll_log()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self) -> None:
        PAD = 10

        # ── 配置区 ──
        frm_cfg = ttk.LabelFrame(self.root, text="服务器配置", padding=PAD)
        frm_cfg.grid(row=0, column=0, sticky="ew", padx=PAD, pady=(PAD, 4))
        self.root.columnconfigure(0, weight=1)

        ttk.Label(frm_cfg, text="监听端口:").grid(row=0, column=0, sticky="w")
        self._port_var = tk.StringVar(value=str(self.DEFAULT_PORT))
        self._port_entry = ttk.Entry(frm_cfg, textvariable=self._port_var, width=8)
        self._port_entry.grid(row=0, column=1, sticky="w", padx=(4, 16))

        ttk.Label(frm_cfg, text="监听地址:").grid(row=0, column=2, sticky="w")
        self._host_var = tk.StringVar(value="0.0.0.0")
        host_combo = ttk.Combobox(
            frm_cfg, textvariable=self._host_var, width=13,
            values=["0.0.0.0", "127.0.0.1"], state="readonly",
        )
        host_combo.grid(row=0, column=3, sticky="w", padx=(4, 0))

        # ── 状态栏 ──
        frm_status = ttk.Frame(self.root, padding=(PAD, 2))
        frm_status.grid(row=1, column=0, sticky="ew", padx=PAD)

        self._status_canvas = tk.Canvas(frm_status, width=14, height=14, highlightthickness=0)
        self._status_canvas.grid(row=0, column=0)
        self._status_dot = self._status_canvas.create_oval(2, 2, 12, 12, fill="#888888", outline="")

        self._status_label = ttk.Label(frm_status, text="未运行", anchor="w")
        self._status_label.grid(row=0, column=1, sticky="w", padx=(6, 0))

        # ── 按钮区 ──
        frm_btns = ttk.Frame(self.root, padding=(PAD, 4))
        frm_btns.grid(row=2, column=0, sticky="ew", padx=PAD)

        self._btn_toggle = ttk.Button(
            frm_btns, text="▶  启动服务", command=self._toggle_server, width=14,
        )
        self._btn_toggle.grid(row=0, column=0, padx=(0, 8))

        self._btn_browser = ttk.Button(
            frm_btns, text="打开浏览器", command=self._open_browser,
            state="disabled", width=12,
        )
        self._btn_browser.grid(row=0, column=1, padx=(0, 8))

        ttk.Button(frm_btns, text="清空日志", command=self._clear_log, width=10).grid(row=0, column=2)

        # ── 日志区 ──
        frm_log = ttk.LabelFrame(self.root, text="运行日志", padding=PAD)
        frm_log.grid(row=3, column=0, sticky="nsew", padx=PAD, pady=(4, PAD))
        self.root.rowconfigure(3, weight=1)
        frm_log.rowconfigure(0, weight=1)
        frm_log.columnconfigure(0, weight=1)

        self._log_text = scrolledtext.ScrolledText(
            frm_log, width=72, height=20, state="disabled",
            font=("Consolas", 9), bg="#1e1e1e", fg="#d4d4d4",
            insertbackground="white", relief="flat",
        )
        self._log_text.grid(row=0, column=0, sticky="nsew")

    # ── 状态控制 ──────────────────────────────────────────────────────────────

    def _set_status(self, running: bool) -> None:
        if running:
            self._status_canvas.itemconfig(self._status_dot, fill="#2ecc71")
            port = self._port_var.get()
            host = self._host_var.get()
            display = "127.0.0.1" if host == "0.0.0.0" else host
            self._status_label.config(text=f"运行中  →  http://{display}:{port}")
            self._btn_toggle.config(text="■  停止服务")
            self._btn_browser.config(state="normal")
            self._port_entry.config(state="disabled")
        else:
            self._status_canvas.itemconfig(self._status_dot, fill="#888888")
            self._status_label.config(text="未运行")
            self._btn_toggle.config(text="▶  启动服务")
            self._btn_browser.config(state="disabled")
            self._port_entry.config(state="normal")

    # ── 日志操作 ──────────────────────────────────────────────────────────────

    def _append_log(self, text: str) -> None:
        self._log_text.config(state="normal")
        self._log_text.insert("end", text + "\n")
        self._log_text.see("end")
        self._log_text.config(state="disabled")

    def _clear_log(self) -> None:
        self._log_text.config(state="normal")
        self._log_text.delete("1.0", "end")
        self._log_text.config(state="disabled")

    def _poll_log(self) -> None:
        """每 150ms 从队列拉取日志刷新到文本框"""
        try:
            while True:
                msg = self._log_queue.get_nowait()
                self._append_log(msg)
        except queue.Empty:
            pass
        self.root.after(150, self._poll_log)

    # ── 服务控制 ──────────────────────────────────────────────────────────────

    def _toggle_server(self) -> None:
        if self._server_thread and self._server_thread.is_alive():
            self._stop_server()
        else:
            self._start_server()

    def _start_server(self) -> None:
        port_str = self._port_var.get().strip()
        if not port_str.isdigit() or not (1 <= int(port_str) <= 65535):
            messagebox.showerror("端口错误", "请输入 1-65535 之间的有效端口号")
            return
        port = int(port_str)
        host = self._host_var.get()
        self._btn_toggle.config(state="disabled")
        self._append_log(f"[INFO] 正在初始化，端口 {port} ...")
        threading.Thread(target=self._do_start, args=(host, port), daemon=True).start()

    def _do_start(self, host: str, port: int) -> None:
        try:
            ensure_env()
            try:
                from dotenv import load_dotenv
                load_dotenv(dotenv_path=str(app_dir() / ".env"), override=True)
            except ImportError:
                pass

            if not os.getenv("SECRET_KEY"):
                raise RuntimeError("SECRET_KEY 未设置，请检查 .env 文件")

            self._setup_logging()

            if self._flask_app is None:
                self._log_queue.put("[INFO] 正在加载应用模块...")
                from outlook_web.app import create_app
                self._flask_app = create_app(autostart_scheduler=None)

            self._server_thread = FlaskServerThread(self._flask_app, host, port)
            self._server_thread.start()
            ok = self._server_thread.wait_started(timeout=10.0)

            if not ok or self._server_thread._error is not None:
                raise RuntimeError(f"服务器启动失败: {self._server_thread._error}")

            display = "127.0.0.1" if host == "0.0.0.0" else host
            self._log_queue.put(f"[INFO] 服务已启动  →  http://{display}:{port}")
            self.root.after(0, lambda: self._set_status(True))
            self.root.after(0, lambda: self._btn_toggle.config(state="normal"))

        except Exception as exc:
            self._log_queue.put(f"[ERROR] 启动失败: {exc}")
            self.root.after(0, lambda: self._btn_toggle.config(state="normal"))
            self.root.after(0, lambda: self._set_status(False))

    def _stop_server(self) -> None:
        self._btn_toggle.config(state="disabled")
        threading.Thread(target=self._do_stop, daemon=True).start()

    def _do_stop(self) -> None:
        try:
            if self._server_thread:
                self._server_thread.shutdown()
                self._server_thread.join(timeout=6)
                self._server_thread = None
            self._log_queue.put("[INFO] 服务已停止")
        except Exception as exc:
            self._log_queue.put(f"[WARN] 停止时发生异常: {exc}")
        self.root.after(0, lambda: self._set_status(False))
        self.root.after(0, lambda: self._btn_toggle.config(state="normal"))

    def _open_browser(self) -> None:
        port = self._port_var.get()
        webbrowser.open(f"http://127.0.0.1:{port}")

    def _setup_logging(self) -> None:
        handler = QueueHandler(self._log_queue)
        handler.setFormatter(logging.Formatter("[%(levelname)s] %(name)s: %(message)s"))
        handler.setLevel(logging.INFO)
        root_logger = logging.getLogger()
        if not any(isinstance(h, QueueHandler) for h in root_logger.handlers):
            root_logger.addHandler(handler)
            root_logger.setLevel(logging.INFO)

    def _on_close(self) -> None:
        if self._server_thread and self._server_thread.is_alive():
            if messagebox.askyesno("退出确认", "服务正在运行，确定要停止并退出吗？"):
                self._server_thread.shutdown()
                self.root.destroy()
        else:
            self.root.destroy()


# ─── 入口 ─────────────────────────────────────────────────────────────────────

def main() -> None:
    os.chdir(str(app_dir()))

    root = tk.Tk()
    root.minsize(560, 480)

    try:
        ttk.Style().theme_use("vista")
    except Exception:
        pass

    LauncherApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
