# -*- coding: utf-8 -*-
"""截图工具的**界面层**（tkinter）。

只管三件事：控件与布局、收集用户输入、把结果显示出来。
所有规则都在 `screenshot_ui_core.py` 里（命名、路径、坐标解析、后端加载）——
要改行为改那边，要改外观改这里。

启动方式不变：`python screenshot_ui.py`
"""

import os
import sys
import traceback
import tkinter as tk
from datetime import datetime
from tkinter import filedialog, messagebox, scrolledtext, ttk

# 把脚本所在目录放进 sys.path：这样无论从哪个工作目录启动，都能 import 同目录的逻辑层。
# （拆成两个模块后，普通 import 需要这一句；实测缺它时从别处启动会 ModuleNotFoundError）
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import screenshot_ui_core as core  # noqa: E402  （必须在 sys.path 处理之后）
import cv2
import numpy as np
import mss
import screenshot_utils as utils

# 启动即检查后端是否就位：缺「截图函数.py」时立刻报错，而不是等到第一次截图才发现
core.ensure_backend()


class ScreenshotApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("截图工具")
        self.geometry("280x450")
        self.resizable(False, True)

        self.output_dir_var = tk.StringVar(value=os.path.join(_HERE, "screenshots"))
        # 记住「上一次自动生成的文件名」：用户手改过就不再自动覆盖他的输入
        self._auto_name = core.generate_default_filename()
        self.filename_var = tk.StringVar(value=self._auto_name)
        self.status_var = tk.StringVar(value="准备就绪")
        self.topmost_var = tk.BooleanVar(value=False)

        self._build_ui()

    # ---------------------------------------------------------------- 界面搭建
    def _build_ui(self):
        # 视觉样式
        style = ttk.Style(self)
        try:
            style.theme_use('clam')
        except Exception:
            pass
        default_font = (None, 10)
        style.configure('.', font=default_font)
        style.configure('TButton', padding=6)

        self.geometry("280x450")
        main = ttk.Frame(self, padding=10)
        main.pack(fill="both", expand=True)

        # ---------- 区域选择 ----------
        region_frame = ttk.LabelFrame(main, text='截取区域（留空为全屏）')
        region_frame.pack(fill='x', pady=(0, 6))

        ttk.Label(region_frame, text='X1').grid(row=0, column=0, sticky='w', padx=(6, 0))
        ttk.Label(region_frame, text='Y1').grid(row=0, column=1, sticky='w', padx=(6, 0))
        ttk.Label(region_frame, text='X2').grid(row=0, column=2, sticky='w', padx=(6, 0))
        ttk.Label(region_frame, text='Y2').grid(row=0, column=3, sticky='w', padx=(6, 0))

        self.x1_entry = ttk.Entry(region_frame, width=6)
        self.y1_entry = ttk.Entry(region_frame, width=6)
        self.x2_entry = ttk.Entry(region_frame, width=6)
        self.y2_entry = ttk.Entry(region_frame, width=6)

        self.x1_entry.grid(row=1, column=0, padx=(6, 4), pady=(4, 6), sticky='ew')
        self.y1_entry.grid(row=1, column=1, padx=4, pady=(4, 6), sticky='ew')
        self.x2_entry.grid(row=1, column=2, padx=4, pady=(4, 6), sticky='ew')
        self.y2_entry.grid(row=1, column=3, padx=(4, 6), pady=(4, 6), sticky='ew')
        for column_index in (0, 1, 2, 3):
            region_frame.grid_columnconfigure(column_index, weight=1)

        # ---------- 保存设置 ----------
        save_frame = ttk.LabelFrame(main, text='保存')
        save_frame.pack(fill='x', pady=(0, 8))

        ttk.Label(save_frame, text='保存目录').grid(row=0, column=0, sticky='w', pady=(6, 4))
        self.output_dir_entry = ttk.Entry(save_frame, textvariable=self.output_dir_var)
        self.output_dir_entry.grid(row=0, column=1, sticky='ew', padx=(6, 4), pady=(6, 4))
        ttk.Button(save_frame, text='选择目录', command=self.choose_output_dir).grid(row=0, column=2, padx=(4, 0), pady=(6, 4))

        ttk.Label(save_frame, text='文件名').grid(row=1, column=0, sticky='w', pady=(0, 6))
        self.filename_entry = ttk.Entry(save_frame, textvariable=self.filename_var)
        self.filename_entry.grid(row=1, column=1, sticky='ew', padx=(6, 4), pady=(0, 6))
        ttk.Label(save_frame, text='(默认 .png)', foreground='#666666').grid(row=1, column=2, sticky='w')

        save_frame.grid_columnconfigure(1, weight=1)

        # 大按钮区
        btn_frame = ttk.Frame(main)
        btn_frame.pack(fill='x', pady=(0, 8))
        ttk.Button(btn_frame, text='截图', command=self.take_screenshot).pack(side='left', fill='x', expand=True, padx=(0, 6))
        ttk.Button(btn_frame, text='打开目录', command=lambda: os.startfile(self.output_dir_var.get())).pack(side='left')

        # ---------- 模板匹配诊断 ----------
        tpl_frame = ttk.LabelFrame(main, text='模板匹配诊断')
        tpl_frame.pack(fill='x', pady=(0, 8))

        self.template_var = tk.StringVar(value=os.path.join(_HERE, 'image', 'game_ui_snapshots', 'auto_disable.png'))
        self.template_entry = ttk.Entry(tpl_frame, textvariable=self.template_var)
        self.template_entry.grid(row=0, column=0, sticky='ew', padx=(6, 4), pady=6)
        ttk.Button(tpl_frame, text='选择模板', command=self.choose_template).grid(row=0, column=1, padx=(4, 0), pady=6)
        ttk.Button(tpl_frame, text='查看匹配分数', command=self.check_match).grid(row=1, column=0, columnspan=2, sticky='ew', padx=6, pady=(0, 6))
        tpl_frame.grid_columnconfigure(0, weight=1)

        # ---------- 选项与状态 ----------
        opt_frame = ttk.Frame(main)
        opt_frame.pack(fill='x', pady=(0, 8))
        ttk.Checkbutton(opt_frame, text='窗口置顶', variable=self.topmost_var, command=self.toggle_topmost).pack(side='left')
        ttk.Label(opt_frame, textvariable=self.status_var, foreground='#0066cc').pack(side='right')

        # ---------- 日志 ----------
        log_frame = ttk.LabelFrame(main, text='输出日志')
        log_frame.pack(fill='both', expand=True)
        self.log_text = scrolledtext.ScrolledText(log_frame, state='disabled', wrap=tk.WORD)
        self.log_text.pack(fill='both', expand=True, padx=8, pady=(8, 4))
        clear_btn = ttk.Button(log_frame, text='清空日志', command=self.clear_log)
        clear_btn.pack(anchor='e', padx=8, pady=(0, 8))

        # 快捷键
        self.bind('<Return>', lambda _event: self.take_screenshot())

    # ---------------------------------------------------------------- 界面交互
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
        directory = filedialog.askdirectory(initialdir=self.output_dir_var.get() or _HERE)
        if directory:
            self.output_dir_var.set(directory)

    def choose_template(self):
        path = filedialog.askopenfilename(initialdir=_HERE, filetypes=[("Image files", "*.png *.jpg *.jpeg *.bmp"), ("All files", "*")])
        if path:
            self.template_var.set(path)

    def check_match(self):
        """计算并显示模板在全屏与指定区域内的匹配最高分（调试用）。"""
        tpl_path = self.template_var.get().strip()
        if not tpl_path:
            messagebox.showerror("模板未指定", "请先选择或填写模板路径。")
            return
        try:
            tpl = utils._load_template(tpl_path)
        except Exception as exc:
            self.log(f"模板加载失败：{exc}")
            messagebox.showerror("模板加载失败", str(exc))
            return
        try:
            # 抓取主屏幕一帧（与 screenshot_utils 保持一致）
            mon = utils._sct.monitors[1]
            frame = cv2.cvtColor(np.array(utils._sct.grab(mon))[:, :, :3], cv2.COLOR_BGR2GRAY)

            # 全屏匹配
            res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
            _, max_val, _, max_loc = cv2.minMaxLoc(res)
            self.log(f"全屏最高分: {max_val:.3f} @ {max_loc}")

            # 区域匹配：复用界面上的四个坐标输入框（若全空表示未设置）
            region = core.parse_coordinates(
                self.x1_entry.get(), self.y1_entry.get(), self.x2_entry.get(), self.y2_entry.get()
            )
            if region is None:
                self.log("未指定区域坐标，跳过区域内匹配（可在上方填写 X1/Y1/X2/Y2）")
            else:
                x1, y1, x2, y2 = region
                if x2 <= x1 or y2 <= y1:
                    raise ValueError("区域坐标不合法：X2 必须大于 X1，Y2 必须大于 Y1。")
                sub = frame[y1:y2, x1:x2]
                if sub.size == 0:
                    raise ValueError("指定区域超出屏幕范围或为空。")
                res2 = cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED)
                _, max2, _, loc2 = cv2.minMaxLoc(res2)
                abs_pos = (loc2[0] + x1, loc2[1] + y1)
                self.log(f"region 内最高分: {max2:.3f} -> 绝对位置 {abs_pos}")
                self.log(f"阈值0.8: {'通过' if max2 >= 0.8 else '不通过'} | 阈值0.7: {'通过' if max2 >= 0.7 else '不通过'}")

            self.status_var.set(f"模板匹配完成：{os.path.basename(tpl_path)}")
        except Exception as exc:
            self.log(f"匹配失败：{exc}")
            self.log("详细错误（排查用）：\n" + traceback.format_exc())
            messagebox.showerror("匹配失败", str(exc))

    def _advance_auto_name(self):
        """截图成功后推进自动文件名（用户手输过的名字不动）。"""
        if core.should_advance_auto_name(self.filename_var.get(), self._auto_name):
            self._auto_name = core.generate_default_filename()
            self.filename_var.set(self._auto_name)

    # ---------------------------------------------------------------- 主流程（只做编排）
    def take_screenshot(self):
        try:
            region = core.parse_coordinates(
                self.x1_entry.get(),
                self.y1_entry.get(),
                self.x2_entry.get(),
                self.y2_entry.get(),
            )
            if region is None:
                # 四个坐标全空 -> 全屏（屏幕尺寸只有界面层知道，所以在这里取）
                x1, y1 = 0, 0
                x2, y2 = self.winfo_screenwidth(), self.winfo_screenheight()
                self.log(f"开始截图：全屏 ({x1}, {y1}) -> ({x2}, {y2})")
            else:
                x1, y1, x2, y2 = region
                self.log(f"开始截图：区域 ({x1}, {y1}) -> ({x2}, {y2})")

            output_path = core.build_output_path(
                self.output_dir_var.get(),
                self.filename_var.get(),
                self._auto_name,
            )
            saved_path = core.capture_region(x1, y1, x2, y2, output_path)

            # 关键：截图成功后推进文件名，否则连点两次会静默覆盖上一张
            self._advance_auto_name()

            # 状态栏只有 300px 宽，放完整路径会被裁掉右半段（文件名正好在那），所以只显示文件名
            self.status_var.set(f"截图已保存：{os.path.basename(saved_path)}")
            self.log(f"截图成功：{saved_path}")
        except ValueError as exc:
            self.status_var.set(f"参数错误：{exc}")
            self.log(f"参数错误：{exc}")
            messagebox.showerror("参数错误", str(exc))
        except Exception as exc:
            self.status_var.set(f"截图失败：{exc}")
            self.log(f"截图失败：{exc}")
            # 弹窗只给一行短消息，完整堆栈写进日志区（可滚动），方便排查
            self.log("详细错误（排查用）：\n" + traceback.format_exc())
            messagebox.showerror("截图失败", str(exc))


def main():
    app = ScreenshotApp()
    app.mainloop()


if __name__ == "__main__":
    main()
