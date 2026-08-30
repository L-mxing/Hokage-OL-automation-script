import sys
import time

import mss

from screenshot_utils import wait_and_click_image, wait_image, click_pos


# ================= 配置区域 =================
# 图片路径
IMG_SUMMON = "image/BM/BM_01_summon.png"                 # 点击召唤
IMG_LAST_SLOT = "image/BM/BM_02_last_slot.png"           # 最后一个槽位
IMG_CONFIRM_SYNTHESIS = "image/BM/BM_03_confirm_synthesis.png"  # 确认吞噬

# 范围
REGION_SUMMON = (543, 460, 847, 63) # 点击召唤
REGION_LAST_SLOT = (1345, 699, 76, 70)
REGION_CONFIRM_SYNTHESIS = (805, 553, 128, 52)

# 固定坐标
POS_SYNTHESIS = (1369, 541)          # 一键合成

# 点击速度：调用处直接传参（不动 screenshot_utils 默认值）
CLICK_DURATION = 0.05                # 0.05 秒 ≈ 瞬移

# 单点颜色快速检测（mss 抓固定 1x1 区域，媲美按键精灵 IfColor）
PIXEL_BAG_CHECK = (1392, 738)        # 检测点坐标（对应按键精灵的 1392,738）
BAG_FULL_COLOR = (53, 33, 19)        # ★ 占位值！未满时该点的颜色 RGB，必须实测后替换
COLOR_TOLERANCE = 30                 # 容差：颜色在此范围内视为"未满"
_sct = mss.MSS()                     # mss 全局实例，复用


def get_pixel_color(x, y):
    """抓取固定 1x1 区域取色，返回 (R, G, B)；失败返回 None"""
    try:
        img = _sct.grab({"left": x, "top": y, "width": 1, "height": 1})
        return img.pixel(0, 0)[:3]
    except Exception:
        return None

def is_bag_full_by_pixel():
    """快速颜色检测：颜色偏离 BAG_FULL_COLOR 超过容差 → 判定背包已满"""
    rgb = get_pixel_color(*PIXEL_BAG_CHECK)
    if rgb is None:
        return False  # 取色失败 → 走图像识别兜底，不误判
    return not (abs(rgb[0] - BAG_FULL_COLOR[0]) <= COLOR_TOLERANCE and
                abs(rgb[1] - BAG_FULL_COLOR[1]) <= COLOR_TOLERANCE and
                abs(rgb[2] - BAG_FULL_COLOR[2]) <= COLOR_TOLERANCE)

def do_synthesis():
    # 1. 点击召唤（带一次重试；interval=0.2 加快检测；duration 传参提速）
    summon = wait_and_click_image(IMG_SUMMON, region=REGION_SUMMON, desc='点击召唤按钮',interval=0.2,
                                  duration=CLICK_DURATION)
    if not summon:
        print("未检测到点击召唤按钮，尝试重新点击")
        summon = wait_and_click_image(IMG_SUMMON, region=REGION_SUMMON, desc='点击召唤按钮', interval=0.2,
                                      duration=CLICK_DURATION)
        if not summon:
            print("检测召唤按钮失败，退出")
            return False

    # 2. 判断背包是否已满：颜色快速检测 + 图像识别兜底（双保险）
    bag_full = is_bag_full_by_pixel()
    if not bag_full:
        # 颜色说"没满"时，再用图像确认一次：找不到槽位图 = 已满
        if not wait_image(IMG_LAST_SLOT, region=REGION_LAST_SLOT, timeout=3, desc='最后一个槽位', interval=0.2):
            bag_full = True

    # 3. 已满 → 一键合成
    if bag_full:
        print("检测到背包已满，执行一键合成")
        click_pos(*POS_SYNTHESIS,duration=CLICK_DURATION)
        confirm = wait_image(IMG_CONFIRM_SYNTHESIS, region=REGION_CONFIRM_SYNTHESIS,
                             timeout=2, desc='确认吞噬按钮', interval=0.2)
        if confirm:
            print("确认吞噬...")
            click_pos(*confirm,duration=CLICK_DURATION)
    return True

def main():
    while True:
        do_synthesis()
        time.sleep(0.3)   # 轮询间隔，兼顾速度与 UI 响应

if __name__ == "__main__":
    main()
