import os
import time
import threading
import tkinter as tk
from tkinter import messagebox, ttk

import mss
from pynput import keyboard


class ScreenshotApp:
    def __init__(self, root):
        self.root = root
        self.root.title("后台截图工具")
        self.root.geometry("420x220")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.on_close)

        self.output_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "screenshots")
        os.makedirs(self.output_dir, exist_ok=True)

        self.hotkey_combo = "<ctrl>+<alt>+s"
        self.listener = None
        self._hotkey_ready = False

        title = ttk.Label(root, text="后台截图：Ctrl + Alt + S", font=("Microsoft YaHei", 12))
        title.pack(pady=(18, 8))

        self.capture_btn = ttk.Button(root, text="立即截图", command=self.capture_screen, width=18)
        self.capture_btn.pack(pady=8)

        self.path_var = tk.StringVar(value=self.output_dir)
        self.path_label = ttk.Label(root, textvariable=self.path_var, wraplength=360)
        self.path_label.pack(pady=(4, 10))

        self.status_var = tk.StringVar(value="等待截图")
        self.status_label = ttk.Label(root, textvariable=self.status_var, foreground="darkgreen")
        self.status_label.pack()

        self._start_hotkey_listener()

    def _start_hotkey_listener(self):
        try:
            self.listener = keyboard.GlobalHotKeys({self.hotkey_combo: self.on_hotkey})
            self.listener.start()
            self._hotkey_ready = True
            self.status_var.set("热键已启用：Ctrl + Alt + S")
        except Exception as exc:
            self._hotkey_ready = False
            self.status_var.set("热键启动失败")
            messagebox.showerror("快捷键错误", f"无法注册全局热键：\n{exc}")

    def on_hotkey(self):
        """后台全局热键触发截图，不需要窗口聚焦。"""
        self.capture_screen(show_message=False)

    def capture_screen(self, show_message=True):
        try:
            timestamp = time.strftime("%Y%m%d_%H%M%S")
            filename = os.path.join(self.output_dir, f"screen_{timestamp}.png")

            with mss.MSS() as sct:
                saved_path = sct.shot(output=filename)

            self.path_var.set(saved_path)
            self.status_var.set("截图已保存")
            if show_message:
                messagebox.showinfo("截图成功", f"已保存到：\n{saved_path}")
        except Exception as exc:
            self.status_var.set("截图失败")
            messagebox.showerror("截图失败", f"截图过程中出现错误：\n{exc}")

    def on_close(self):
        if self.listener is not None:
            try:
                self.listener.stop()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    app = ScreenshotApp(root)
    root.mainloop()


if __name__ == "__main__":
    main()
