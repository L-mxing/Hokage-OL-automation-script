import os
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk
import importlib.util


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
MODULE_PATH = os.path.join(BASE_DIR, "截图函数.py")

spec = importlib.util.spec_from_file_location("screenshot_func", MODULE_PATH)
if spec is None or spec.loader is None:
    raise ImportError(f"无法加载截图模块: {MODULE_PATH}")

screenshot_module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(screenshot_module)
capture_region = screenshot_module.capture_region


class ScreenshotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("截图工具")
        self.geometry("300x420")
        self.resizable(False, False)

        self.output_dir_var = tk.StringVar(value=os.path.join(BASE_DIR, "screenshots"))
        self.filename_var = tk.StringVar(value="capture.png")
        self.status_var = tk.StringVar(value="准备就绪")
        self.topmost_var = tk.BooleanVar(value=False)

        self._build_ui()

    def _build_ui(self):
        main = ttk.Frame(self, padding=12)
        main.pack(fill="both", expand=True)

        ttk.Label(main, text="起点 X1 / Y1").grid(row=0, column=0, sticky="w", pady=(0, 4))
        ttk.Label(main, text="终点 X2 / Y2").grid(row=1, column=0, sticky="w", pady=(0, 4))

        self.x1_entry = ttk.Entry(main, width=10)
        self.y1_entry = ttk.Entry(main, width=10)
        self.x2_entry = ttk.Entry(main, width=10)
        self.y2_entry = ttk.Entry(main, width=10)

        self.x1_entry.grid(row=0, column=1, padx=(8, 4), pady=(0, 4), sticky="w")
        self.y1_entry.grid(row=0, column=2, padx=(4, 8), pady=(0, 4), sticky="w")
        self.x2_entry.grid(row=1, column=1, padx=(8, 4), pady=(0, 4), sticky="w")
        self.y2_entry.grid(row=1, column=2, padx=(4, 8), pady=(0, 4), sticky="w")

        ttk.Label(main, text="保存目录").grid(row=2, column=0, sticky="w", pady=(8, 4))
        self.output_dir_entry = ttk.Entry(main, textvariable=self.output_dir_var)
        self.output_dir_entry.grid(row=2, column=1, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Button(main, text="选择目录", command=self.choose_output_dir).grid(row=2, column=3, padx=(8, 0), pady=(8, 4), sticky="ew")

        ttk.Label(main, text="文件名").grid(row=3, column=0, sticky="w", pady=(8, 4))
        self.filename_entry = ttk.Entry(main, textvariable=self.filename_var)
        self.filename_entry.grid(row=3, column=1, columnspan=2, sticky="ew", pady=(8, 4))
        ttk.Label(main, text="(默认 .png)", foreground="#666666").grid(row=3, column=3, sticky="w", padx=(8, 0), pady=(8, 4))

        ttk.Button(main, text="截图", command=self.take_screenshot).grid(row=4, column=0, columnspan=4, sticky="ew", pady=(12, 8))

        ttk.Checkbutton(main, text="窗口置顶", variable=self.topmost_var, command=self.toggle_topmost).grid(row=5, column=0, columnspan=4, sticky="w", pady=(0, 4))
        ttk.Label(main, textvariable=self.status_var, foreground="#0066cc").grid(row=6, column=0, columnspan=4, sticky="w")

        log_frame = ttk.LabelFrame(main, text="输出日志")
        log_frame.grid(row=7, column=0, columnspan=4, sticky="nsew", pady=(8, 0))
        log_frame.grid_columnconfigure(0, weight=1)
        log_frame.grid_rowconfigure(0, weight=1)

        self.log_text = scrolledtext.ScrolledText(log_frame, state="disabled", wrap=tk.WORD, height=10)
        self.log_text.grid(row=0, column=0, sticky="nsew", padx=8, pady=(8, 4))

        ttk.Button(log_frame, text="清空日志", command=self.clear_log).grid(row=1, column=0, sticky="e", padx=(0, 8), pady=(0, 8))

        main.grid_columnconfigure(1, weight=1)
        main.grid_columnconfigure(2, weight=1)
        main.grid_columnconfigure(3, weight=0)
        main.grid_rowconfigure(7, weight=1)

    def toggle_topmost(self):
        try:
            self.attributes("-topmost", self.topmost_var.get())
            self.log(f"窗口置顶已{'开启' if self.topmost_var.get() else '关闭'}")
        except Exception as exc:
            self.log(f"置顶设置失败：{exc}")

    def log(self, message):
        timestamp = datetime.now().strftime("[%H:%M:%S]")
        self.log_text.configure(state="normal")
        self.log_text.insert(tk.END, f"{timestamp} {message}\n")
        self.log_text.see(tk.END)
        self.log_text.configure(state="disabled")

    def clear_log(self):
        self.log_text.configure(state="normal")
        self.log_text.delete("1.0", tk.END)
        self.log_text.configure(state="disabled")
        self.log("日志已清空")

    def choose_output_dir(self):
        directory = filedialog.askdirectory(initialdir=self.output_dir_var.get() or BASE_DIR)
        if directory:
            self.output_dir_var.set(directory)

    def _build_output_path(self):
        output_dir = self.output_dir_var.get().strip()
        file_name = self.filename_var.get().strip()
        if not output_dir:
            raise ValueError("保存目录不能为空。")
        if not file_name:
            raise ValueError("文件名不能为空。")

        if not os.path.splitext(file_name)[1]:
            file_name += ".png"

        os.makedirs(output_dir, exist_ok=True)
        return os.path.join(output_dir, file_name)

    def take_screenshot(self):
        try:
            x1 = int(self.x1_entry.get())
            y1 = int(self.y1_entry.get())
            x2 = int(self.x2_entry.get())
            y2 = int(self.y2_entry.get())
            output_path = self._build_output_path()

            self.log(f"开始截图：区域 ({x1}, {y1}) -> ({x2}, {y2})")
            saved_path = capture_region(x1, y1, x2, y2, output_path)
            self.status_var.set(f"截图已保存：{saved_path}")
            self.log(f"截图成功：{saved_path}")
        except ValueError as exc:
            self.status_var.set(f"参数错误：{exc}")
            self.log(f"参数错误：{exc}")
            messagebox.showerror("参数错误", str(exc))
        except Exception as exc:
            self.status_var.set(f"截图失败：{exc}")
            self.log(f"截图失败：{exc}")
            messagebox.showerror("截图失败", str(exc))


def main():
    app = ScreenshotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
