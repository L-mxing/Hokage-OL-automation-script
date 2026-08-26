import sys
import time

import pyautogui

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


# ---------- 辅助函数 ----------
def safe_click(pos, duration=CLICK_DURATION):
    """
    安全点击坐标
    :param pos: 坐标(元组)
    :param duration: 点击持续时间
    """
    pyautogui.click(pos[0], pos[1], duration=duration)
    time.sleep(CHECK_INTERVAL)


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
            location = pyautogui.locateOnScreen(image_path, region=region, confidence=confidence)
            if location:
                print(f"✅ 检测到 {desc} 并点击")
                click_x, click_y = pyautogui.center(location)
                safe_click((click_x, click_y))  # 传回元组
                return True
        except Exception as e:
            # pyautogui有时会抛出异常，忽略继续
            pass
        time.sleep(CHECK_INTERVAL)
    print(f"⚠️ 超时未找到图片: {image_path}")
    return False


def do_battle_round(round_num):
    """
    执行一次组队副本关卡
    :param round_num: 当前轮次
    :return: True 成功，False 失败
    """
    print(f"\n====== 第 {round_num} 轮开始 ======")

    # 1. 点击“组队副本”入口（使用图像识别，点击图片中心）
    
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
    safe_click(POS_JOIN)  # 参与组队副本

    # 2. 选择关卡
    safe_click(POS_DUNGEON_1)

    # 3. 点击“确认进入”（按您的顺序：先 POS_NO_PROMPT，再确认按钮）
    if round_num == 1:
        safe_click(POS_NO_PROMPT)
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
        safe_click(POS_START_BATTLE)

    # 5. 等待结算并返回
    settlement = wait_and_click_image(IMG_SETTLEMENT, region=REGION_SETTLEMENT, desc='结算标识')
    if not settlement:
        print("尝试点击结算坐标")
        safe_click(POS_SETTLEMENT)

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
        time.sleep(1)  # 轮次间隔

    print("====== 全部轮次执行完毕 ======")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断，脚本退出")
        sys.exit(0)
