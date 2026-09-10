# import pyautogui
# # 从坐标(100, 200)开始，截取宽300，高400的区域
#


# def shot(x, y,x2,y2,desc=''):
#     left=x
#     top=y
#     width = x2 - x
#     height = y2 - y
#     img = pyautogui.screenshot(region=(left, top, width, height))
#     img.save(desc)
#     print(f"局部截图已保存为 {desc}")
#
#
#
# shot(841,569,910,593,desc='BM_03_confirm_synthesis.png')



# a= pyautogui.size()


# print(a)
# # Size(width=1920, height=1080)
#
# x,y=pyautogui.position() # 解包
# print(x,y)
# print(pyautogui.position())
# # Point(x=1286, y=482)
#
# location = pyautogui.locateOnScreen('image/SC_01_Entrance.png', region=(1130,76,270,70), confidence=0.8)
# print(location)
# # Box(left=np.int64(1149), top=np.int64(83), width=39, height=35)

import mss
import mss.tools


def transform_pos(location):
    x_1,y_1,x_2,y_2 = location
    left=x_1
    top=y_1
    width=x_2-x_1
    height=y_2-y_1
    return {"left":left,"top":top,"width":width,"height":height}

with mss.MSS() as sct:
    region = transform_pos((920,226,1030,255))
    shot = sct.grab(region)
    mss.tools.to_png(shot.rgb,shot.size,output="SC_02_start_btn.png")










