# import pyautogui
#
# # # 从坐标(100, 200)开始，截取宽300，高400的区域
# # img = pyautogui.screenshot(region=(1000, 613, 69, 20))
# # img.save("zd_start_btn.png")
# # print("局部截图已保存为 zd_start_btn.png")
#
# a= pyautogui.size()
# print(a)
# # Size(width=1920, height=1080)
#
# x,y=pyautogui.position() # 解包
# print(x,y)
# print(pyautogui.position())
# # Point(x=1286, y=482)
#
# location = pyautogui.locateOnScreen('image1/SC_Entrance.png', region=(1130,76,270,70), confidence=0.8)
# print(location)
# # Box(left=np.int64(1149), top=np.int64(83), width=39, height=35)

import tkinter as tk
from tkinter import scrolledtext # 带滚动条的“显示板”
root = tk.Tk()
root.title("生存演习控制台")
root.geometry("350x450")
root.resizable(True, True)

btn_frame = tk.Frame(root)
btn_frame.pack(pady=5)


log_text = scrolledtext.ScrolledText(root, wrap=tk.WORD, font=("Consolas", 10))
log_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)


start_btn = tk.Button(btn_frame, text="开始演习", width=15)
start_btn.pack(side=tk.LEFT, padx=5)

clear_btn = tk.Button(btn_frame, text="清空日志", width=15)
clear_btn.pack(side=tk.LEFT, padx=5)

root.mainloop()