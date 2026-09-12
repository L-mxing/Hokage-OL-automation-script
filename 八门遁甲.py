import sys
import time

import mss

from screenshot_utils import (wait_and_click_image, wait_image, click_pos,
                              ensure_utf8_stdout, check_screen_size)


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
# ★ 未标定：保持 None 时**跳过颜色判定**，只用图像识别，不会误判。
#   之前这里是一个没实测过的占位值 (53,33,19)，真实屏幕颜色只要偏离它超过容差，
#   就会恒判"背包已满"，把下面的图像识别兜底整个短路掉。
#   标定方法：保持背包"未满"，运行  python 八门遁甲.py --calibrate
#             把打印出来的 RGB 填到这里，例如 BAG_FULL_COLOR = (53, 33, 19)
BAG_FULL_COLOR = None
COLOR_TOLERANCE = 30                 # 容差：颜色在此范围内视为"未满"
_sct = mss.MSS()                     # mss 全局实例，复用

# 轮询与失败上限：原实现失败一次要等默认 60s、重试再 60s，且 while True 永不退出，
# 会一直空转（GUI 里排在其后的脚本也因此永远轮不到）。
FIRST_TIMEOUT = 10                   # 首次等待召唤按钮出现的超时（秒）
RETRY_TIMEOUT = 5                    # 重试等待超时（秒）
LOOP_INTERVAL = 0.3                  # 轮询间隔，兼顾速度与 UI 响应
MAX_CONSECUTIVE_FAILURES = 5         # 连续失败达到该次数即退出，避免无限空转

_calib_warned = False                # 未标定提示只打印一次，避免刷屏


def get_pixel_color(x, y):
    """
    :param x: 色点的 x 坐标
    :param y: 色点的 y 坐标
    抓取固定 1x1 区域取色，返回 (R, G, B)；失败返回 None
    """
    try:
        img = _sct.grab({"left": x, "top": y, "width": 1, "height": 1})
        return img.pixel(0, 0)[:3]
    except Exception:
        return None

def is_bag_full_by_pixel():
    """快速颜色检测：颜色偏离 BAG_FULL_COLOR 超过容差 → 判定背包已满

    :return: True=已满 / False=未满 / None=未标定或取色失败
             返回 None 表示"颜色这条路不可用"，调用方应完全依赖图像识别，
             绝不能把它当成"未满"（原实现未标定时的占位值会导致恒判已满）。
    """
    if BAG_FULL_COLOR is None:
        return None                      # 未标定 → 不参与判定
    rgb = get_pixel_color(*PIXEL_BAG_CHECK)
    if rgb is None:
        return None                      # 取色失败 → 交给图像识别
    return not all(abs(c - b) <= COLOR_TOLERANCE
                   for c, b in zip(rgb, BAG_FULL_COLOR))

def do_synthesis():
    """点一次召唤，并在背包满时执行一键合成。失败返回 False（未找到召唤按钮）。"""
    # 1. 点击召唤（带一次重试；超时收紧到 FIRST_TIMEOUT/RETRY_TIMEOUT，别再用默认 60s）
    summon = wait_and_click_image(IMG_SUMMON, region=REGION_SUMMON,
                                  desc='点击召唤按钮', interval=0.2,
                                  timeout=FIRST_TIMEOUT, duration=CLICK_DURATION)
    if not summon:
        print(f"未检测到点击召唤按钮，{RETRY_TIMEOUT}s 后重试一次")
        summon = wait_and_click_image(IMG_SUMMON, region=REGION_SUMMON,
                                      desc='点击召唤按钮', interval=0.2,
                                      timeout=RETRY_TIMEOUT, duration=CLICK_DURATION)
        if not summon:
            print("检测召唤按钮失败，跳过本轮")
            return False

    # 2. 判断背包是否已满：图像识别为主，颜色快速检测为辅（双保险，两者取"或"）
    #    原实现把颜色判定写成了图像识别的前置条件（if not bag_full:）：
    #    颜色一旦误报"已满"，图像识别就永远不会执行，兜底形同虚设。
    global _calib_warned
    slot_visible = wait_image(IMG_LAST_SLOT, region=REGION_LAST_SLOT, timeout=3,
                              desc='最后一个槽位', interval=0.2)
    by_image = slot_visible is None          # 找不到槽位图 = 已满
    by_pixel = is_bag_full_by_pixel()
    if by_pixel is None and not _calib_warned:
        _calib_warned = True
        print("ℹ️ 颜色检测未标定，当前仅依据图像识别判定（标定方法见 --calibrate）")

    # 3. 已满 → 一键合成
    if by_image or by_pixel is True:
        print(f"检测到背包已满（图像={'满' if by_image else '未满'}, "
              f"颜色={'未标定' if by_pixel is None else '满' if by_pixel else '未满'}），执行一键合成")
        click_pos(*POS_SYNTHESIS, duration=CLICK_DURATION)
        confirm = wait_image(IMG_CONFIRM_SYNTHESIS, region=REGION_CONFIRM_SYNTHESIS,
                             timeout=2, desc='确认吞噬按钮', interval=0.2)
        if confirm:
            print("确认吞噬...")
            click_pos(*confirm, duration=CLICK_DURATION)
    return True


def run_calibration(seconds=5):
    """取色助手：标定 BAG_FULL_COLOR 用。

    用法：把游戏停在**背包未满**的状态，运行 `python 八门遁甲.py --calibrate`，
    按打印出来的颜色填回文件顶部的 BAG_FULL_COLOR 即可。
    """
    print(f"取色点 {PIXEL_BAG_CHECK}，采样 {seconds} 秒（请保持背包处于未满状态）...")
    deadline = time.time() + seconds
    samples = {}
    while time.time() < deadline:
        rgb = get_pixel_color(*PIXEL_BAG_CHECK)
        if rgb is not None:
            samples[rgb] = samples.get(rgb, 0) + 1
        time.sleep(0.1)

    if not samples:
        print("❌ 取色失败：坐标可能越界，或屏幕不可访问")
        return
    print("采样结果（RGB → 出现次数）：")
    for rgb, n in sorted(samples.items(), key=lambda kv: -kv[1]):
        print(f"   {rgb}  ×{n}")
    best = max(samples, key=samples.get)
    print(f"\n出现最多的颜色: {best}")
    print(f"请把文件顶部的 BAG_FULL_COLOR 改成: BAG_FULL_COLOR = {best}")


def main():
    """轮询合成。

    注意：正常挂机时这个循环会一直跑（符合预期）；但连续失败达到
    MAX_CONSECUTIVE_FAILURES 次就退出，而不是无限空转 —— 这样游戏没开、
    界面不对时能快速暴露问题，GUI 里排在其后的脚本也不会被永久卡住。
    """
    failures = 0
    while True:
        if do_synthesis():
            failures = 0
        else:
            failures += 1
            print(f"⚠️ 连续失败 {failures}/{MAX_CONSECUTIVE_FAILURES}")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"❌ 连续 {failures} 次找不到召唤按钮，退出"
                      f"（请确认游戏已启动、当前界面为召唤界面）")
                return
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    if "--calibrate" in sys.argv:
        run_calibration()
    else:
        main()
