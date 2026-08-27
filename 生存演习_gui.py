import os  # 寻找文件路径
import subprocess  # 启动另一个py文件；检测子程序是否运行
import sys  # 确保两个py文件使用的是同一个Python解释器(操控 Python解释器)
import threading
import tkinter as tk
from tkinter import scrolledtext  # 带滚动条的“显示板”


class SurvivalGUI:
    def __init__(self, root):
        self.root = root
        root.title("生存演习控制台")
        root.geometry("350x350")
        root.resizable(False, False)

        # 按钮框架，收容按钮
        btn_frame = tk.Frame(root)
        btn_frame.pack(pady=5)

        self.log_text = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Consolas", 10))
        self.log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)

        self.start_btn = tk.Button(btn_frame, text="开始演习", command=self.start_script, width=15)
        self.start_btn.pack(side=tk.LEFT, padx=5)

        self.clear_btn = tk.Button(btn_frame, text="清空日志", command=self.clear_log, width=15)
        self.clear_btn.pack(side=tk.LEFT, padx=5)

        self.process = None
        self.script_dir = os.path.dirname(os.path.abspath(__file__))  # 脚本所在目录
        self.script_path = os.path.join(self.script_dir, "生存演习.py")  # 拼接路径

    def clear_log(self):
        self.log_text.delete(1.0, tk.END)

    def log(self, message):
        """线程安全地追加日志"""
        self.root.after(0, lambda: self._append_log(message))

    def _append_log(self, message):
        self.log_text.insert(tk.END, message)
        self.log_text.see(tk.END)

    def start_script(self):
        if self.process and self.process.poll() is None:  # poll()判断程序是否还在跑
            self.log("⚠️ 上一轮演习尚未结束，请等待完成\n")
            return

        if not os.path.exists(self.script_path):
            self.log(f"❌ 错误：找不到脚本文件 {self.script_path}\n")
            return

        self.start_btn.config(state=tk.DISABLED)
        self.log("🚀 正在启动生存演习脚本...\n")

        def run():
            try:
                # 使用 utf-8 编码，忽略无法解码的字节，避免 gbk 错误
                # Popen作用：在 GUI 程序里面，**启动另外一个 Python 脚本作为子进程**，
                # 并且实时拿到它的打印输出、报错信息，输出到你的 ScrolledText 日志框。
                self.process = subprocess.Popen(
                    [sys.executable, self.script_path],  # 使用当前 Python 解释器运行脚本
                    cwd=self.script_dir,  # 设置工作目录为脚本所在目录(当前工作目录)
                    stdout=subprocess.PIPE,  # 把机器人的“喊话”（标准输出）接过来
                    stderr=subprocess.STDOUT,  # 把机器人的“抱怨”（报错）也当喊话接过来
                    text=True,  # 把听到的内容转成文字（而不是二进制乱码）
                    encoding='utf-8',  # 用“普通话（UTF-8）”来翻译
                    errors='ignore',  # 遇到听不懂的乱码，直接跳过不崩溃
                    bufsize=1  # 听到一句就传回一句，别攒着（行缓冲）
                )

                for line in iter(self.process.stdout.readline, ''):
                    self.log(line)

                self.process.stdout.close()
                return_code = self.process.wait()
                if return_code == 0:
                    self.log("✅ 演习结束，脚本正常退出\n")
                else:
                    self.log(f"⚠️ 脚本非正常退出，返回码：{return_code}\n")

            except Exception as e:
                self.log(f"❌ 运行异常：{e}\n")
            finally:
                self.root.after(0, lambda: self.start_btn.config(state=tk.NORMAL))
                self.process = None

        threading.Thread(target=run, daemon=True).start()

    # `daemon=True`只会杀掉**线程本身**，**不会杀掉 subprocess 子进程！**
    #  GUI 关闭时，你依然要手动调用 `self.process.terminate()`，否则被启动的 py 脚本还在后台运行。
    def on_closing(self):
        """
        作用：点击窗口右上角 × 关闭按钮时执行。\n
        防止：GUI 窗口关掉了，但是 subprocess 启动的脚本还在后台偷偷运行。
        """
        if self.process and self.process.poll() is None:
            self.log("⏹️ 正在终止子进程...\n")
            self.process.terminate()
            try:
                self.process.wait(timeout=2) # 等待2秒，子进程是否结束
            except subprocess.TimeoutExpired: # 超时，抛出异常
                self.process.kill() # 强制杀掉子进程
        self.root.destroy() # 关闭 GUI 窗口


def main():
    root = tk.Tk()
    app = SurvivalGUI(root)

    # 用户点窗口右上角关闭按钮时，不要直接销毁窗口，去执行 app.on_closing 函数。
    root.protocol("WM_DELETE_WINDOW", app.on_closing)
    root.mainloop()


if __name__ == "__main__":
    main()
