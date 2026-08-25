"""
功能界面化.py
整合三个脚本为 GUI：生存演习修改.py、组队副本版本_2.py、强者降临.py
要求：不修改原脚本，按固定顺序执行（生存 → 组队 → 强者），支持停止、日志实时显示、关闭时清理子进程。
"""

import os
import sys
import threading
import subprocess
import queue
import time
import tkinter as tk
from tkinter import ttk, messagebox
import ctypes
from ctypes import wintypes, byref

# 确保工作目录为脚本所在目录
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# Windows VK codes (used for global hotkeys)
VK_F10 = 0x79
VK_F11 = 0x7A


# 脚本（按固定顺序执行）
SCRIPTS = [
    ("生存演习修改.py", "生存演习"),
    ("组队副本版本_2.py", "组队副本"),
    ("强者降临.py", "强者降临"),
]

# GUI 主程序
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("脚本执行器 - 功能界面化")
        self.geometry("350x400")

        # 状态
        self.running = False
        self.current_proc = None
        self.stop_flag = threading.Event()
        self.log_q = queue.Queue()

        # 选择变量（与SCRIPTS对应）
        self.vars = [tk.BooleanVar(value=False) for _ in SCRIPTS]

        self._build_ui()
        # 启动定时器从队列刷新日志（线程安全）
        self.after(100, self._flush_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        frame_top = ttk.LabelFrame(self, text="脚本选择")
        frame_top.pack(fill="x", padx=8, pady=8)

        # 全选/反选
        self.var_all = tk.BooleanVar(value=False)
        cb_all = ttk.Checkbutton(frame_top, text="全选", variable=self.var_all, command=self._toggle_all)
        cb_all.grid(row=0, column=0, padx=6, pady=6, sticky="w")

        for idx, (_, name) in enumerate(SCRIPTS, start=1):
            cb = ttk.Checkbutton(frame_top, text=name, variable=self.vars[idx-1])
            cb.grid(row=0, column=idx, padx=6, pady=6, sticky="w")

        frame_ctrl = ttk.Frame(self)
        frame_ctrl.pack(fill="x", padx=8)
        self.btn_start = ttk.Button(frame_ctrl, text="开始执行 (F10)", command=self.start)
        self.btn_start.pack(side="left", padx=6, pady=6)
        self.btn_stop = ttk.Button(frame_ctrl, text="停止 (F11)", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=6, pady=6)

        frame_log = ttk.LabelFrame(self, text="执行日志")
        frame_log.pack(fill="both", expand=True, padx=8, pady=8)
        self.txt_log = tk.Text(frame_log, wrap="word", state="normal")
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb = ttk.Scrollbar(frame_log, orient="vertical", command=self.txt_log.yview)
        sb.pack(side="right", fill="y")
        self.txt_log.config(yscrollcommand=sb.set)

        # 全局快捷键：F10 开始，F11 停止（应用内快捷键）
        # 使用 bind_all 以确保在任何子部件拥有焦点时仍能响应
        self.bind_all('<F10>', self.start)
        self.bind_all('<F11>', self.stop)

        # 初始化用于系统全局热键的控制结构
        self._hotkey_stop_event = threading.Event()
        self._hk_ids = []
        self._hotkey_thread = None
        # 在 Windows 上尝试注册系统级热键，以便在程序在后台时也能响应
        if os.name == 'nt':
            self._register_hotkeys()
        else:
            self._log('系统不支持全局热键注册（非 Windows）。')

    def _toggle_all(self):
        v = self.var_all.get()
        for var in self.vars:
            var.set(v)

    # 日志入队
    def _log(self, text, flush_immediately=False):
        ts = time.strftime("[%H:%M:%S]")
        self.log_q.put(f"{ts} {text}")
        if flush_immediately:
            self._flush_log_queue()

    # 定时从队列写入 Text（避免跨线程直接访问）
    def _flush_log_queue(self):
        try:
            while True:
                line = self.log_q.get_nowait()
                self.txt_log.insert(tk.END, line + "\n")
                self.txt_log.see(tk.END)
        except queue.Empty:
            pass
        # 继续调度
        self.after(100, self._flush_log_queue)

    # 启动任务
    def start(self, event=None):
        if self.running:
            return
        # 按固定顺序过滤选中脚本
        selected = [s for (s, name), var in zip(SCRIPTS, self.vars) if var.get()]
        if not selected:
            messagebox.showwarning("提示", "请至少选择一个脚本")
            return

        self.running = True
        self.stop_flag.clear()
        self.btn_start.config(state="disabled")
        self.btn_stop.config(state="normal")
        self._log(f"启动任务：{', '.join(selected)}")

        self.worker = threading.Thread(target=self._run_sequence, args=(selected,), daemon=True)
        self.worker.start()

    # 停止（发信号并强制终止当前进程）
    def stop(self, event=None):
        if not self.running:
            return
        self.stop_flag.set()
        self._log("停止信号已发送，正在终止当前脚本...")
        self.btn_stop.config(state="disabled")
        # terminate current process if exists
        self._terminate_current_process()

    def _terminate_current_process(self):
        p = self.current_proc
        if p and p.poll() is None:
            try:
                pid = p.pid
                # 尝试优雅终止
                p.terminate()
                # 等待短暂时间
                try:
                    p.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    # 强制杀掉进程树 (Windows 使用 taskkill)
                    if os.name == 'nt':
                        try:
                            subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                        except Exception:
                            pass
                    else:
                        try:
                            p.kill()
                        except Exception:
                            pass
            except Exception as e:
                self._log(f"终止进程失败：{e}")

    # 顺序执行选中脚本
    def _run_sequence(self, selected):
        try:
            for script in [name for name, _ in SCRIPTS if name in selected]:
                if self.stop_flag.is_set():
                    self._log("流程被中止，退出执行序列")
                    break
                self._log(f"开始执行：{script}")
                rc = self._run_script_and_stream(script)
                if rc == 0:
                    self._log(f"脚本 {script} 执行成功 (exit {rc})")
                else:
                    self._log(f"脚本 {script} 执行结束，退出码 {rc}")
                # 小缓冲，避免过快启动下一个
                time.sleep(0.5)
        except Exception as e:
            self._log(f"执行序列异常：{e}")
        finally:
            self.running = False
            self.current_proc = None
            self.after(0, lambda: self.btn_start.config(state='normal'))
            self.after(0, lambda: self.btn_stop.config(state='disabled'))
            self._log("所有选中脚本执行完毕或已停止")

    # 执行单个脚本并实时输出到日志，返回退出码
    def _run_script_and_stream(self, script_name):
        script_path = os.path.join(BASE_DIR, script_name)
        if not os.path.exists(script_path):
            self._log(f"找不到脚本：{script_name}")
            return -1

        cmd = [sys.executable, script_path]
        self._log(f"运行命令：{' '.join(cmd)}")

        try:
            # 使用 Popen 直接接收子进程输出，避免子进程把输出写到控制台而丢失。
            p = subprocess.Popen(
                cmd,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            self.current_proc = p
        except Exception as e:
            self._log(f"启动脚本失败：{e}")
            return -1

        # 读取 stdout/stderr 的线程，持续接收子进程输出并写入GUI日志
        def reader(pipe, prefix):
            try:
                for line in iter(pipe.readline, ''):
                    if not line:
                        break
                    # 直接输出子进程原始日志，不附加脚本名前缀
                    self._log(line.rstrip())
                pipe.close()
            except Exception as e:
                self._log(f"读取 {prefix} 出错：{e}")

        t_out = threading.Thread(target=reader, args=(p.stdout, 'OUT'), daemon=True)
        t_err = threading.Thread(target=reader, args=(p.stderr, 'ERR'), daemon=True)
        t_out.start()
        t_err.start()

        # 轮询进程状态，同时响应停止请求
        exit_code = None
        while True:
            if self.stop_flag.is_set():
                # 请求停止：终止进程并等待
                self._log(f"正在终止 {script_name} (PID={p.pid}) ...")
                self._terminate_current_process()
                break
            ret = p.poll()
            if ret is not None:
                exit_code = ret
                break
            time.sleep(0.1)

        # 确保读取线程结束
        try:
            t_out.join(timeout=2)
            t_err.join(timeout=2)
        except Exception:
            pass

        # 如果因为 stop_flag 导致终止，返回非零
        if self.stop_flag.is_set() and (exit_code is None or exit_code == 0):
            # 如果已被停止，视为中断，返回特殊码 124
            exit_code = exit_code if exit_code is not None else 124

        self.current_proc = None
        return exit_code if exit_code is not None else -1

    def _register_hotkeys(self):
        """启动一个专用线程以注册并监听全局热键（在 Windows 上）。"""
        try:
            if getattr(self, '_hotkey_thread', None) and self._hotkey_thread.is_alive():
                return
            self._hotkey_stop_event.clear()
            self._hotkey_thread = threading.Thread(target=self._hotkey_worker, daemon=True)
            self._hotkey_thread.start()
        except Exception as e:
            self._log(f'启动热键线程异常：{e}')

    def _hotkey_worker(self):
        """在独立线程中注册热键并运行 GetMessageW 消息循环（可靠）。"""
        try:
            threading.current_thread().name = 'HotkeyThread'
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            WM_HOTKEY = 0x0312

            # 热键 id
            self._hk_id_start = 1
            self._hk_id_stop = 2
            # 记录该线程 id，以便在注销时通知它
            self._hotkey_thread_id = kernel32.GetCurrentThreadId()

            def _format_error(err):
                buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.kernel32.FormatMessageW(0x00001000, None, err, 0, buf, len(buf), None)
                return f"{err} ({buf.value})"

            # 尝试注册
            if not user32.RegisterHotKey(0, self._hk_id_start, 0, VK_F10):
                err = kernel32.GetLastError()
                self._log(f'注册全局热键 F10 失败: {_format_error(err)}')
            else:
                self._hk_ids.append(self._hk_id_start)
                self._log('已注册全局热键 F10')

            # 注册停止热键：仅尝试 F11
            if user32.RegisterHotKey(0, self._hk_id_stop, 0, VK_F11):
                self._hk_ids.append(self._hk_id_stop)
                self._log('已注册全局热键 F11')
            else:
                err = kernel32.GetLastError()
                self._log(f'注册全局热键 F11 失败: {_format_error(err)}')

            msg = wintypes.MSG()
            while not self._hotkey_stop_event.is_set():
                # 阻塞等待消息，可被 PostThreadMessageW/WM_QUIT 唤醒
                ret = user32.GetMessageW(byref(msg), 0, 0, 0)
                if ret == 0:  # WM_QUIT
                    break
                if ret == -1:
                    break
                if msg.message == WM_HOTKEY:
                    try:
                        hk_id = int(msg.wParam)
                    except Exception:
                        hk_id = None
                    if hk_id == getattr(self, '_hk_id_start', None):
                        self.after(0, self.start)
                    elif hk_id == getattr(self, '_hk_id_stop', None):
                        self.after(0, self.stop)
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))

            # 清理已注册热键
            for hid in list(self._hk_ids):
                try:
                    user32.UnregisterHotKey(0, hid)
                except Exception:
                    pass
            self._hk_ids = []
        except Exception as e:
            self._log(f'热键线程异常：{e}')

    def _unregister_hotkeys(self):
        """通知热键线程退出并等待其清理。"""
        if os.name != 'nt':
            return
        try:
            user32 = ctypes.windll.user32
            # 先尝试注销已注册的热键
            for hid in list(getattr(self, '_hk_ids', [])):
                try:
                    user32.UnregisterHotKey(0, hid)
                except Exception:
                    pass
            self._hk_ids = []

            # 通知热键线程退出并唤醒
            self._hotkey_stop_event.set()
            WM_QUIT = 0x0012
            tid = getattr(self, '_hotkey_thread_id', None)
            if tid:
                try:
                    user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
                except Exception:
                    pass
            # 等待线程退出
            if self._hotkey_thread:
                self._hotkey_thread.join(timeout=1)
                self._hotkey_thread = None
        except Exception as e:
            self._log(f'注销全局热键异常：{e}')

    def _on_close(self):
        # 先通知停止并清理热键监听，等待短暂时长再退出
        if self.running and messagebox.askyesno("确认", "仍有脚本在运行，确认退出并终止所有子进程？"):
            self.stop_flag.set()
            self._terminate_current_process()
            # 等待 worker 清理
            try:
                if getattr(self, 'worker', None) and self.worker.is_alive():
                    self.worker.join(timeout=1)
            except Exception:
                pass
            # 注销全局热键
            try:
                self._unregister_hotkeys()
            except Exception:
                pass
            # 给子进程一点时间退出
            time.sleep(0.2)
            self.destroy()
        elif not self.running:
            try:
                self._unregister_hotkeys()
            except Exception:
                pass
            self.destroy()


if __name__ == '__main__':
    app = App()
    app.mainloop()
