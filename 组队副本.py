import sys
import time

import cv2
import numpy as np
import pyautogui
from PIL import ImageGrab

# ================= 配置区域 =================

# 图片相对路径
IMG_DUNGEON_ENTRANCE = 'image1/ZD_dungeon_entrance.png'  # 组队副本入口
IMG_CONFIRM_ENTER = 'image1/ZD_confirm_enter.png'  # 确认进入
IMG_START_BATTLE = 'image1/ZD_start_battle.png'  # 开战按钮
IMG_SETTLEMENT = 'image1/ZD_settlement.png'  # 结算返回按钮

# 图像搜索范围
REGION_DUNGEON_ENTRANCE = (20, 186, 200, 192)  # 组队副本入口
REGION_CONFIRM_ENTER = (835, 543, 108, 40)  # “确认进入”按钮
REGION_START_BATTLE = (840, 603, 103, 40)  # “开战”按钮
REGION_SETTLEMENT = (1155, 801, 165, 42)  # 游戏结算，返回游戏

# 固定点击坐标

POS_JOIN = (1225, 688)  # 参与按钮坐标
POS_DUNGEON_1 = (751, 445)  # 选择副本关卡
POS_NO_PROMPT = (821, 603)
POS_START_BATTLE = (888, 620)  # 开战按钮坐标
POS_SETTLEMENT = (1235, 825)  # 返回游戏按钮

# 全局参数
CONFIDENCE = 0.8  # 置信度
CLICK_DURATION = 0.3  # 点击持续时间
WAIT_TIMEOUT = 30  # 响应时长
CHECK_INTERVAL = 0.5  # 检测间隔

# 循环次数
ROUNDS = 5


# ---------- OpenCV 图像识别（替换 pyautogui 默认实现） ----------
_template_cache = {}


def _load_template(image_path):
    """
    读取模板并转灰度（带缓存）；np.fromfile + imdecode 兼容中文路径
    :param image_path:  模板图片路径
    :return: 灰度模板图像
    """
    if image_path not in _template_cache:
        data = np.fromfile(image_path, dtype=np.uint8)
        template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(f"模板图片加载失败: {image_path}")
        _template_cache[image_path] = template
    return _template_cache[image_path]


def locate_on_screen(image_path, region=None, confidence=CONFIDENCE):
    """
    OpenCV 版图像定位（替代 pyautogui.locateOnScreen）
    返回 (left, top, width, height) 屏幕绝对坐标；未找到返回 None
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :return: 返回灰度图坐标(left, top, width, height) 或 None
    """
    template = _load_template(image_path)
    th, tw = template.shape[:2]

    offset_x = offset_y = 0
    if region:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
        offset_x, offset_y = left, top
        if tw > width or th > height:
            return None
    else:
        bbox = None

    frame = cv2.cvtColor(np.array(ImageGrab.grab(bbox=bbox)), cv2.COLOR_RGB2GRAY)

    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= confidence:
        return (max_loc[0] + offset_x, max_loc[1] + offset_y, tw, th)
    return None


# ---------- 辅助函数 ----------
def safe_click(pos_x,pos_y, duration=CLICK_DURATION):
    """
    安全点击坐标
    :param pos_x: 模版图像 x 坐标
    :param pos_y: 模版图像 y 坐标
    :param duration: 鼠标移动持续时间
    """
    pyautogui.moveTo(pos_x, pos_y, duration=duration)
    pyautogui.click()


def wait_and_click_image(image_path, region=None, confidence=CONFIDENCE,
                         timeout=WAIT_TIMEOUT, desc=""):
    """
    等待图片出现并点击其中心（或偏移位置）
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :param timeout: 超时秒数
    :param desc: 描述文字（用于日志）
    :return: True 点击成功，False 超时未找到
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            location = locate_on_screen(image_path, region=region, confidence=confidence)
            if location:
                print(f"✅ 检测到 {desc} 并点击")
                left, top, width, height = location
                safe_click(left + width // 2, top + height // 2)
                return True
        except FileNotFoundError:
            raise
        except Exception:
            pass
        time.sleep(CHECK_INTERVAL)
    print(f"⚠️ 超时未找到 {desc} 图片: {image_path}")
    return False


def do_battle_round(round_num):
    """
    执行一次组队副本关卡
    :param round_num: 当前轮次
    :return: True 成功，False 失败
    """
    print(f"\n====== 第 {round_num} 轮开始 ======")

    # 1. 点击“组队副本”入口（使用图像识别，点击图片中心）
    if round_num == 1:
        print("寻找组队副本入口并进入副本...")

        entry = wait_and_click_image(IMG_DUNGEON_ENTRANCE, region=REGION_DUNGEON_ENTRANCE,
                                     desc='组队副本入口')
        if not entry:
            print("❌ 未找到组队副本入口，尝试重点击入口...")
            time.sleep(1)
            entry = wait_and_click_image(IMG_DUNGEON_ENTRANCE, region=REGION_DUNGEON_ENTRANCE,
                                         desc='组队副本入口')
            if not entry:
                print("❌ 战斗启动失败，跳过本场")
                return False

        time.sleep(1.5)
        print("参与副本")
        safe_click(POS_JOIN[0],POS_JOIN[1])  # 参与组队副本

    # 2. 选择关卡
    safe_click(POS_DUNGEON_1[0],POS_DUNGEON_1[1])

    # 3. 点击“确认进入”（按您的顺序：先 POS_NO_PROMPT，再确认按钮）
    if round_num == 1:
        safe_click(POS_NO_PROMPT[0],POS_NO_PROMPT[1])  # 点击“不再提示”
        time.sleep(0.5)
        confirm_enter = wait_and_click_image(IMG_CONFIRM_ENTER, region=REGION_CONFIRM_ENTER,
                                             desc='确认进入按钮')
        if not confirm_enter:
            print("❌ 未找到确认进入按钮，尝试重新点击...")
            time.sleep(1)
            wait_and_click_image(IMG_CONFIRM_ENTER, region=REGION_CONFIRM_ENTER, desc='确认进入按钮')

    # 4. 等待开战并点击
    time.sleep(2)
    start_battle = wait_and_click_image(IMG_START_BATTLE, region=REGION_START_BATTLE, timeout=5,
                                        desc='开战按钮')
    if not start_battle:
        print("未找到开战按钮，尝试点击固定坐标")
        safe_click(POS_START_BATTLE[0],POS_START_BATTLE[1])

    # 5. 等待结算并返回
    settlement = wait_and_click_image(IMG_SETTLEMENT, region=REGION_SETTLEMENT, desc='结算标识')
    if not settlement:
        print("尝试点击结算坐标")
        safe_click(POS_SETTLEMENT[0],POS_SETTLEMENT[1])

    print(f"====== 第 {round_num} 轮完成 ======\n")
    return True


# ---------- 主流程 ----------
def main():
    print("====== 组队副本自动刷图脚本启动 ======")
    print("脚本启动，按 Ctrl + C 可停止")
    print(f"将执行 {ROUNDS} 轮")
    print("请确保游戏窗口在最前，1秒后开始...")
    time.sleep(1)

    for i in range(1, ROUNDS + 1):
        do_battle_round(i)
        time.sleep(2)  # 轮次间隔

    print("====== 全部轮次执行完毕 ======")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断，脚本退出")
        sys.exit(0)
