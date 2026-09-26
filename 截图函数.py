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

import os

import mss
import mss.tools


def transform_pos(x_1, y_1, x_2, y_2):
    left = min(x_1, x_2)
    top = min(y_1, y_2)
    width = abs(x_2 - x_1)
    height = abs(y_2 - y_1)
    return {"left": left, "top": top, "width": width, "height": height}


def capture_region(x_1, y_1, x_2, y_2, output_path="screenshot.png"):
    region = transform_pos(x_1, y_1, x_2, y_2)
    if region["width"] <= 0 or region["height"] <= 0:
        raise ValueError("截图区域宽度和高度必须大于 0。")

    output_path = os.path.abspath(output_path)
    output_dir = os.path.dirname(output_path)
    if output_dir:
        os.makedirs(output_dir, exist_ok=True)

    with mss.MSS() as sct:
        shot = sct.grab(region)
        mss.tools.to_png(shot.rgb, shot.size, output=output_path)

    return output_path


if __name__ == "__main__":
    save_path = capture_region(1200, 878, 1270, 949, "screenshots/SC_04_start_btn.png")
    print(f"截图已保存到：{save_path}")










