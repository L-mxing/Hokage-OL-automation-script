"""
hokage_ol_gui.py
整合脚本为 GUI：生存演习.py、组队副本.py、强者降临.py、八门遁甲.py、排位战.py、忍者测验答题.py、通缉任务.py
要求：不修改原脚本，按固定顺序执行（生存 → 组队 → 强者 → 八门 → 排位 → 忍者测验 → 通缉），支持停止、日志实时显示、关闭时清理子进程。
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

# 脚本所在目录。子进程通过 Popen(cwd=BASE_DIR) 继承该工作目录，
# 这样 image/xxx.png 这类相对路径才有效，同时避免用 os.chdir 改动整个进程的工作目录。
BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Windows VK codes (used for global hotkeys)
VK_F10 = 0x79
VK_F11 = 0x7A

# Windows 消息常量（热键线程用）
WM_HOTKEY = 0x0312
WM_QUIT = 0x0012

# 全局热键 id、主线程轮询间隔、单次日志刷新上限
HK_ID_START = 1
HK_ID_STOP = 2
LOG_POLL_MS = 100
LOG_MAX_LINES_PER_FLUSH = 500

# 被用户中止时的统一退出码（区别于子进程自身的退出码）
EXIT_INTERRUPTED = 124


# 脚本（按固定顺序执行）
SCRIPTS = [
    ("生存演习.py", "生存演习"),
    ("组队副本.py", "组队副本"),
    ("强者降临.py", "强者降临"),
    ("八门遁甲.py", "八门遁甲"),
    ("排位战.py", "排位战"),
    ("忍者测验答题.py", "忍者测验答题"),
    ("通缉任务.py", "通缉任务"),
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
        self._state_lock = threading.Lock()   # 保护 _running（主线程与工作线程都会读写）
        self._running = False
        self.current_proc = None
        self._proc_lock = threading.Lock()   # 保护 current_proc 的读写与终止操作
        self.stop_flag = threading.Event()
        self.log_q = queue.Queue()
        self.cmd_q = queue.Queue()
        self.worker = None                    # 执行序列的工作线程
        self._closing = False                 # 窗口销毁阶段置位，用于停止 after 定时链
        self._log_error_reported = False      # 日志写入异常只报告一次，避免刷屏

        # 全局热键控制结构（注册与注销都在热键线程内部完成）
        self._hotkey_stop_event = threading.Event()
        self._hotkey_ready = threading.Event()
        self._hotkey_thread_id = None
        self._hotkey_thread = None

        # 选择变量（与SCRIPTS对应）
        self.vars = [tk.BooleanVar(value=False) for _ in SCRIPTS]

        self._build_ui()
        # 启动定时器从队列刷新日志（线程安全）
        self.after(LOG_POLL_MS, self._flush_log_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # running 用锁保护：主线程判断/设置，工作线程在执行结束时清零
    @property
    def running(self):
        with self._state_lock:
            return self._running

    @running.setter
    def running(self, value):
        with self._state_lock:
            self._running = bool(value)

    def _build_ui(self):
        # 统一创建复选框：点击后把焦点交回窗口，避免焦点停留在复选框上
        # （原来三处各写一遍相同的 bind，抽出来减少重复）
        def make_checkbutton(parent, text, variable, **kwargs):
            cb = ttk.Checkbutton(parent, text=text, variable=variable, takefocus=False, **kwargs)
            cb.bind("<ButtonRelease-1>", lambda e: self.focus_set())
            return cb

        frame_top = ttk.LabelFrame(self, text="脚本选择")
        frame_top.pack(fill="x", padx=6, pady=6)

        # 全选/反选：放在最上方左侧
        self.var_all = tk.BooleanVar(value=False)
        cb_all = make_checkbutton(frame_top, "全选", self.var_all, command=self._toggle_all)
        cb_all.grid(row=0, column=0, padx=4, pady=2, sticky="w")

        # 布局列配置：将列权重设为 0，避免自动扩展拉开复选框间距
        max_cols = 3
        for c in range(max_cols):
            frame_top.grid_columnconfigure(c, weight=0)

        # 置顶复选放在全选所在行的最右侧，保证不超过窗口边界
        cb_top = make_checkbutton(frame_top, "置顶", self.var_topmost, command=self._toggle_topmost)
        cb_top.grid(row=0, column=max_cols-1, padx=4, pady=2, sticky="e")

        # 脚本选择项，排成每行最多3个的网格，位于全选下方，间距更紧凑
        for idx, (_, name) in enumerate(SCRIPTS):
            row = 1 + (idx // max_cols)
            col = idx % max_cols
            cb = make_checkbutton(frame_top, name, self.vars[idx])
            # 将左右间距缩小为 2 像素，垂直间距为 1 像素，以实现更紧凑的一行布局
            cb.grid(row=row, column=col, padx=2, pady=1, sticky="w")

        # 任意单项勾选状态变化时同步"全选"框，
        # 避免"全选"后手动取消一项、"全选"仍显示勾选的错位状态
        for var in self.vars:
            var.trace_add("write", self._sync_select_all)

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
        # 必须先 pack 垂直滚动条（side="right"），再 pack 文本区。
        # 反过来的话，带 expand=True 的 Text 会一次性吃满容器宽度，
        # 滚动条只能分到 1px 而被完全挤出可视区域（实测 mapped=0）。
        sb = ttk.Scrollbar(frame_log, orient="vertical")
        sb.pack(side="right", fill="y")
        # 不允许用户编辑日志框，但程序可写入（通过切换 state）
        self.txt_log = tk.Text(frame_log, wrap="word", state="disabled",
                               yscrollcommand=sb.set)
        self.txt_log.pack(side="left", fill="both", expand=True)
        sb.config(command=self.txt_log.yview)

        # 全局快捷键：F10 开始，F11 停止（应用内快捷键）
        # 使用 bind_all 以确保在任何子部件拥有焦点时仍能响应
        self.bind_all('<F10>', self.start)
        self.bind_all('<F11>', self.stop)

        # 在 Windows 上尝试注册系统级热键，以便在程序在后台时也能响应
        if os.name == 'nt':
            self._register_hotkeys()
        else:
            self._log('系统不支持全局热键注册（非 Windows）。')

    def _toggle_all(self):
        v = self.var_all.get()
        for var in self.vars:
            var.set(v)

    def _sync_select_all(self, *_args):
        """由单项勾选状态反推"全选"框状态（var_all 自身无 trace，不会递归）"""
        try:
            self.var_all.set(all(var.get() for var in self.vars))
        except Exception:
            pass

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
    def _log(self, text):
        ts = time.strftime("[%H:%M:%S]")
        self.log_q.put(f"{ts} {text}")

    # 定时从队列写入 Text（避免跨线程直接访问）
    def _flush_log_queue(self):
        # 用 try/finally 保证定时链一定被续上：
        # 否则 _drain_log_queue 或命令处理里任何一次异常都会让 after 不再被调度，
        # 日志刷新将永久停止（且没有任何报错提示）。
        try:
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
        finally:
            # 继续调度（窗口已进入销毁流程时不再续期）
            if not self._closing:
                self.after(LOG_POLL_MS, self._flush_log_queue)

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

        # 单次刷新的最大行数：子进程（截图/OCR 类脚本）可能瞬间刷出成千上万行，
        # 若在同一个轮询周期内全部 insert，主线程会被 Tk 调用淹没导致界面卡死。
        max_lines = LOG_MAX_LINES_PER_FLUSH
        truncated = 0
        if len(lines) > max_lines:
            truncated = len(lines) - max_lines
            lines = lines[-max_lines:]

        try:
            # 仅当用户正处于底部时才自动跟随滚动；用户向上翻看历史日志时不打断他。
            at_bottom = self.txt_log.yview()[1] >= 0.999
            self.txt_log.config(state='normal')
            if truncated:
                self.txt_log.insert(tk.END, f"[日志输出过快，本次省略 {truncated} 行]\n")
            # 合并成一次 insert，避免逐行调用 Tk
            self.txt_log.insert(tk.END, "\n".join(lines) + "\n")
            if at_bottom:
                self.txt_log.see(tk.END)
            self.txt_log.config(state='disabled')
        except Exception as e:
            # 这里不能再走 _log()：日志写入本身失败时再入队会形成死循环。
            # 直接写 stderr 并且只报告一次，避免异常被完全静默吞掉。
            if not self._log_error_reported:
                self._log_error_reported = True
                print(f"[GUI] 日志写入失败：{e!r}", file=sys.stderr)

    # 启动任务
    def start(self, event=None):
        if self.running:
            return
        # 按固定顺序过滤选中脚本
        selected = [script for (script, _), var in zip(SCRIPTS, self.vars) if var.get()]
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

    # 停止：只发信号（异步），真正的终止由工作线程的轮询循环完成
    def stop(self, event=None):
        if not self.running:
            return
        self.stop_flag.set()
        self.btn_stop.config(state="disabled")
        self._log("停止信号已发送，正在终止当前脚本...")
        # 注意：这里不能直接调用 _terminate_current_process()。
        # 该函数会 wait() 等待进程退出、必要时还要 taskkill，而 stop() 运行在 Tk 主线程，
        # 直接调用会让界面卡住最长数秒。工作线程每 100ms 会检查 stop_flag 并完成终止。

    def _terminate_current_process(self, wait_timeout=1.5):
        """终止当前子进程（必要时连整棵进程树）。返回是否确实处理了一个存活进程。

        仅在非主线程（工作线程）或退出流程中调用，内部会阻塞等待进程退出。
        """
        # 加锁防止轮询/关闭窗口并发触发重复终止
        with self._proc_lock:
            p = self.current_proc
            if p is None or p.poll() is not None:
                return False
            pid = p.pid
            try:
                # 尝试优雅终止
                p.terminate()
            except Exception as e:
                self._log(f"terminate 失败：{e}")
            try:
                # 等待短暂时间
                p.wait(timeout=wait_timeout)
                return True
            except subprocess.TimeoutExpired:
                pass
            # 强杀：Windows 用 taskkill /T 连带子进程树
            try:
                if os.name == 'nt':
                    subprocess.run(["taskkill", "/PID", str(pid), "/T", "/F"],
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                else:
                    p.kill()
            except Exception as e:
                self._log(f"强制终止进程失败：{e}")
            return True

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
            self.running = False        # running 由 _state_lock 保护，跨线程写安全
            with self._proc_lock:
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
            # 子进程在该目录下运行，保证脚本里 image/xxx.png 这类相对路径有效
            # （替代原先的 os.chdir，不影响 GUI 自身进程的工作目录）
            popen_kwargs["cwd"] = BASE_DIR
            # Windows 下隐藏子进程控制台窗口
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
            # 使用 Popen 直接接收子进程输出，避免子进程把输出写到控制台而丢失。
            p = subprocess.Popen(cmd, **popen_kwargs)
            with self._proc_lock:
                self.current_proc = p
        except Exception as e:
            self._log(f"启动脚本失败：{e}")
            return -1

        # 读取 stdout/stderr 的线程，持续接收子进程输出并写入GUI日志
        def reader(pipe, stream_name):
            try:
                for line in iter(pipe.readline, ''):
                    if not line:
                        break
                    # 直接输出子进程原始日志，不附加脚本名前缀（stdout/stderr 合并展示）
                    self._log(line.rstrip())
                pipe.close()
            except Exception as e:
                self._log(f"读取 {stream_name} 出错：{e}")

        t_out = threading.Thread(target=reader, args=(p.stdout, 'stdout'), daemon=True)
        t_err = threading.Thread(target=reader, args=(p.stderr, 'stderr'), daemon=True)
        t_out.start()
        t_err.start()

        # 等待进程退出，同时响应停止请求。
        # 用 p.wait(timeout) 代替 "poll() + sleep()" 忙等：等待由系统完成，更省 CPU。
        exit_code = -1
        while True:
            if self.stop_flag.is_set():
                # 请求停止：终止进程并等待
                self._log(f"正在终止 {script_name} (PID={p.pid}) ...")
                self._terminate_current_process()
                # 统一用 124 表示"被用户中断"，不沿用 terminate 产生的退出码
                exit_code = EXIT_INTERRUPTED
                break
            try:
                exit_code = p.wait(timeout=0.1)
                break
            except subprocess.TimeoutExpired:
                continue

        # 确保读取线程结束（进程已退出，管道会 EOF，两个线程能正常收尾）
        for t in (t_out, t_err):
            t.join(timeout=2)

        with self._proc_lock:
            self.current_proc = None
        return exit_code

    def _register_hotkeys(self):
        """启动一个专用线程以注册并监听全局热键（在 Windows 上）。"""
        if self._hotkey_thread and self._hotkey_thread.is_alive():
            return
        self._hotkey_stop_event.clear()
        self._hotkey_ready.clear()   # 热键线程完成注册后置位
        self._hotkey_thread = threading.Thread(target=self._hotkey_worker, daemon=True)
        self._hotkey_thread.start()

    def _hotkey_worker(self):
        """在独立线程中注册热键并运行 GetMessageW 消息循环（可靠）。

        注册与注销都在本线程内完成：RegisterHotKey(hWnd=NULL) 注册的热键
        归属于调用线程，从其它线程调用 UnregisterHotKey 是注销不掉的。
        """
        try:
            threading.current_thread().name = 'HotkeyThread'
            user32 = ctypes.windll.user32
            kernel32 = ctypes.windll.kernel32
            # 显式声明返回值类型为无符号 DWORD，避免 thread id 被解释成负数
            kernel32.GetCurrentThreadId.restype = wintypes.DWORD
            self._hotkey_thread_id = kernel32.GetCurrentThreadId()

            def _format_error(err):
                buf = ctypes.create_unicode_buffer(256)
                ctypes.windll.kernel32.FormatMessageW(0x00001000, None, err, 0, buf, len(buf), None)
                return f"{err} ({buf.value})"

            # 注册（失败仅提示，不影响后续交互）
            if user32.RegisterHotKey(0, HK_ID_START, 0, VK_F10):
                self._log('已注册全局热键 F10')
            else:
                self._log(f'注册全局热键 F10 失败: {_format_error(kernel32.GetLastError())}')

            if user32.RegisterHotKey(0, HK_ID_STOP, 0, VK_F11):
                self._log('已注册全局热键 F11')
            else:
                self._log(f'注册全局热键 F11 失败: {_format_error(kernel32.GetLastError())}')

            # 注册动作同时建立了本线程的消息队列，此后再允许主线程投递 WM_QUIT
            self._hotkey_ready.set()

            msg = wintypes.MSG()
            while not self._hotkey_stop_event.is_set():
                # 阻塞等待消息，可被 PostThreadMessageW/WM_QUIT 唤醒
                ret = user32.GetMessageW(byref(msg), 0, 0, 0)
                if ret == 0 or ret == -1:   # 0 = WM_QUIT，-1 = 出错
                    break
                if msg.message == WM_HOTKEY:
                    hk_id = int(msg.wParam)
                    if hk_id == HK_ID_START:
                        self.cmd_q.put("start")
                    elif hk_id == HK_ID_STOP:
                        self.cmd_q.put("stop")
                user32.TranslateMessage(byref(msg))
                user32.DispatchMessageW(byref(msg))

            # 在本线程内注销热键
            for hid in (HK_ID_START, HK_ID_STOP):
                try:
                    user32.UnregisterHotKey(0, hid)
                except Exception:
                    pass
        except Exception as e:
            self._hotkey_ready.set()   # 避免主线程在 _unregister_hotkeys 里白等
            self._log(f'热键线程异常：{e}')

    def _unregister_hotkeys(self):
        """通知热键线程退出并等待其自行注销热键。"""
        if os.name != 'nt':
            return
        t = self._hotkey_thread
        if t is None:
            return
        self._hotkey_stop_event.set()
        # 等注册完成（消息队列已建立）再投递 WM_QUIT，否则线程可能已阻塞在
        # GetMessageW 而无人唤醒，或因队列未建立导致投递失败。
        self._hotkey_ready.wait(1.0)
        tid = self._hotkey_thread_id
        if tid:
            try:
                ctypes.windll.user32.PostThreadMessageW(tid, WM_QUIT, 0, 0)
            except Exception as e:
                self._log(f'通知热键线程退出失败：{e}')
        t.join(timeout=1)
        self._hotkey_thread = None

    def _on_close(self):
        # 先通知停止并清理热键监听，等待短暂时长再退出
        if self.running and messagebox.askyesno("确认", "仍有脚本在运行，确认退出并终止所有子进程？"):
            self.stop_flag.set()
            # 工作线程会检测到 stop_flag 并负责终止子进程，这里等它收尾
            if self.worker and self.worker.is_alive():
                self.worker.join(timeout=3)
            # 兜底：工作线程未能及时收尾时直接终止
            self._terminate_current_process(wait_timeout=1.0)
            self._unregister_hotkeys()
            # 给子进程一点时间退出
            time.sleep(0.2)
            self._destroy()
        elif not self.running:
            self._unregister_hotkeys()
            self._destroy()

    def _destroy(self):
        # 标记进入销毁流程，让 _flush_log_queue 不再续期 after
        self._closing = True
        self.destroy()


if __name__ == '__main__':
    app = App()
    app.mainloop()
