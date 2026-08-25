import pyautogui
# 从坐标(100, 200)开始，截取宽300，高400的区域
img = pyautogui.screenshot(region=(930, 648, 35, 17))
img.save("QZ_clear_btn.png")
print("局部截图已保存为 QZ_clear_btn.png")

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
