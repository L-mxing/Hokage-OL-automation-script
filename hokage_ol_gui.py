"""
hokage_ol_gui.py
整合脚本为 GUI：生存演习.py、组队副本.py、强者降临.py、八门遁甲.py、排位战.py、忍者测验答题.py
要求：不修改原脚本，按固定顺序执行（生存 → 组队 → 强者 → 八门 → 排位 → 忍者测验），支持停止、日志实时显示、关闭时清理子进程。
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

# 确保工作目录为脚本所在目录（子进程继承该 cwd，image/xxx.png 相对路径才有效）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

# Windows VK codes (used for global hotkeys)
VK_F10 = 0x79
VK_F11 = 0x7A

# Windows 消息常量（热键线程用）
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012


# 脚本（按固定顺序执行）
SCRIPTS = [
    ("生存演习.py", "生存演习"),
    ("组队副本.py", "组队副本"),
    ("强者降临.py", "强者降临"),
    ("八门遁甲.py", "八门遁甲"),
    ("排位战.py", "排位战"),
    ("忍者测验答题.py", "忍者测验答题"),
]

# GUI 主程序
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("火影忍者online自动化脚本")
        self.geometry("300x360")
        # 固定窗口大小，不可调整
        self.resizable(False, False)
        # 默认置顶，可由用户切换
        self.var_topmost = tk.BooleanVar(value=True)
        try:
            self.attributes('-topmost', True)
        except Exception:
            pass

        # 状态
        self.running = False
        self.current_proc = None
        self._proc_lock = threading.Lock()   # 保护 current_proc 的终止操作
        self.stop_flag = threading.Event()
        self.log_q = queue.Queue()
        self.cmd_q = queue.Queue()

        # 选择变量（与SCRIPTS对应）
        self.vars = [tk.BooleanVar(value=False) for _ in SCRIPTS]

        self._build_ui()
        # 启动定时器从队列刷新日志（线程安全）
        self.after(100, self._flush_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _build_ui(self):
        frame_top = ttk.LabelFrame(self, text="脚本选择")
        frame_top.pack(fill="x", padx=6, pady=6)

        # 全选/反选：放在最上方左侧
        self.var_all = tk.BooleanVar(value=False)
        cb_all = ttk.Checkbutton(frame_top, text="全选", variable=self.var_all, command=self._toggle_all, takefocus=False)
        cb_all.grid(row=0, column=0, padx=4, pady=2, sticky="w")
        cb_all.bind("<ButtonRelease-1>", lambda e: self.focus_set())

        # 布局列配置：将列权重设为 0，避免自动扩展拉开复选框间距
        max_cols = 3
        for c in range(max_cols):
            frame_top.grid_columnconfigure(c, weight=0)

        # 置顶复选放在全选所在行的最右侧，保证不超过窗口边界
        cb_top = ttk.Checkbutton(frame_top, text="置顶", variable=self.var_topmost, command=self._toggle_topmost, takefocus=False)
        cb_top.grid(row=0, column=max_cols-1, padx=4, pady=2, sticky="e")
        cb_top.bind("<ButtonRelease-1>", lambda e: self.focus_set())

        # 脚本选择项，排成每行最多3个的网格，位于全选下方，间距更紧凑
        for idx, (_, name) in enumerate(SCRIPTS):
            row = 1 + (idx // max_cols)
            col = idx % max_cols
            cb = ttk.Checkbutton(frame_top, text=name, variable=self.vars[idx], takefocus=False)
            # 将左右间距缩小为 2 像素，垂直间距为 1 像素，以实现更紧凑的一行布局
            cb.grid(row=row, column=col, padx=2, pady=1, sticky="w")
            cb.bind("<ButtonRelease-1>", lambda e: self.focus_set())

        frame_ctrl = ttk.Frame(self)
        frame_ctrl.pack(fill="x", padx=6)
        self.btn_start = ttk.Button(frame_ctrl, text="执行 (F10)", command=self.start)
        self.btn_start.pack(side="left", padx=4, pady=4)
        self.btn_stop = ttk.Button(frame_ctrl, text="停止 (F11)", command=self.stop, state="disabled")
        self.btn_stop.pack(side="left", padx=4, pady=4)
        self.btn_clear = ttk.Button(frame_ctrl, text="清空日志", command=self.clear_log)
        self.btn_clear.pack(side="left", padx=4, pady=4)

        frame_log = ttk.LabelFrame(self, text="执行日志")
        frame_log.pack(fill="both", expand=True, padx=6, pady=6)
        # 不允许用户编辑日志框，但程序可写入（通过切换 state）
        self.txt_log = tk.Text(frame_log, wrap="word", state="disabled")
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

    def _toggle_topmost(self):
        """切换窗口置顶状态"""
        val = bool(self.var_topmost.get())
        try:
            self.attributes('-topmost', val)
            self._log('窗口已置顶' if val else '取消窗口置顶')
        except Exception:
            # 某些平台或环境可能不支持attributes
            pass

    def clear_log(self):
        # 允许程序清空但禁止用户编辑
        try:
            self.txt_log.config(state='normal')
            self.txt_log.delete("1.0", tk.END)
            self.txt_log.config(state='disabled')
        except Exception:
            pass
        # 清空积压日志，避免刚清空后又被旧队列内容补回
        while not self.log_q.empty():
            try:
                self.log_q.get_nowait()
            except queue.Empty:
                break

    # 日志入队
    def _log(self, text, flush_immediately=False):
        ts = time.strftime("[%H:%M:%S]")
        self.log_q.put(f"{ts} {text}")
        if flush_immediately:
            self._drain_log_queue()

    # 定时从队列写入 Text（避免跨线程直接访问）
    def _flush_log_queue(self):
        self._drain_log_queue()
        # 处理热键线程发来的命令（在主线程执行，避免跨线程操作 Tk）
        try:
            while True:
                cmd = self.cmd_q.get_nowait()
                if cmd == "start":
                    self.start()
                elif cmd == "stop":
                    self.stop()
                elif cmd == "ui_done":
                    self.btn_start.config(state="normal")
                    self.btn_stop.config(state="disabled")
                    self._log("所有选中脚本执行完毕或已停止")
        except queue.Empty:
            pass
        # 继续调度
        self.after(100, self._flush_log_queue)

    def _drain_log_queue(self):
        # 一次性批量取出并写入，减少 state 切换与 Tk 调用次数
        lines = []
        try:
            while True:
                lines.append(self.log_q.get_nowait())
        except queue.Empty:
            pass
        if not lines:
            return
        try:
            self.txt_log.config(state='normal')
            for line in lines:
                self.txt_log.insert(tk.END, line + "\n")
            self.txt_log.see(tk.END)
            self.txt_log.config(state='disabled')
        except Exception:
            pass

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
        # 加锁防止 stop()/轮询/关闭窗口并发触发重复终止
        with self._proc_lock:
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
                                subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                               stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                            except Exception as e:
                                self._log(f"taskkill 失败：{e}")
                        else:
                            try:
                                p.kill()
                            except Exception as e:
                                self._log(f"kill 失败：{e}")
                except Exception as e:
                    self._log(f"终止进程失败：{e}")

    # 顺序执行选中脚本
    def _run_sequence(self, selected):
        # selected 已按 SCRIPTS 固定顺序过滤，直接遍历即可
        try:
            for script in selected:
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
            # 通过队列通知主线程恢复按钮状态（避免跨线程调用 Tk）
            self.cmd_q.put("ui_done")

    # 执行单个脚本并实时输出到日志，返回退出码
    def _run_script_and_stream(self, script_name):
        script_path = os.path.join(BASE_DIR, script_name)
        if not os.path.exists(script_path):
            self._log(f"找不到脚本：{script_name}")
            return -1

        cmd = [sys.executable, script_path]
        self._log(f"运行命令：{' '.join(cmd)}")

        try:
            # 编码约定：子进程管道输出统一按 UTF-8 处理。
            # 通过 PYTHONIOENCODING 强制子进程 stdout/stderr 用 UTF-8 输出，
            # 父进程按 UTF-8 解码，避免依赖系统区域设置（中文系统 GBK）导致的乱码。
            popen_kwargs = dict(
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,
                text=True,
                encoding="utf-8",
                errors="replace",
            )
            popen_env = dict(os.environ)
            popen_env["PYTHONIOENCODING"] = "utf-8"
            popen_kwargs["env"] = popen_env
            # Windows 下隐藏子进程控制台窗口
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            # 使用 Popen 直接接收子进程输出，避免子进程把输出写到控制台而丢失。
            p = subprocess.Popen(cmd, **popen_kwargs)
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
            self._hotkey_ready = threading.Event()  # 热键线程设置完 thread_id 后置位
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

            # 热键 id
            self._hk_id_start = 1
            self._hk_id_stop = 2
            # 记录该线程 id，以便在注销时通知它；记录完成后再允许注销方读取
            self._hotkey_thread_id = kernel32.GetCurrentThreadId()
            self._hotkey_ready.set()

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
                        self.cmd_q.put("start")
                    elif hk_id == getattr(self, '_hk_id_stop', None):
                        self.cmd_q.put("stop")
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
            # 等线程设置好 thread_id 再投递 WM_QUIT，避免线程已阻塞在 GetMessageW 而无人唤醒
            self._hotkey_ready.wait(1.0)
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
