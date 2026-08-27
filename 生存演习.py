import pyautogui
import time

# ================= 配置区域 =================
# 图片路径
IMG_ENTRY = 'image1/SC_Entrance.png'
IMG_START = 'image1/SC_start_btn.png'
IMG_VICTORY = 'image1/SC_victory_flag.png'

# 区域限制（加速匹配）
REGION_ENTRY = (1130, 76, 270, 70)          # 生存入口
REGION_START = (923, 225, 92, 35)           # 开始按钮
REGION_VICTORY = (900, 500, 100, 50)        # 胜利标志

# 固定点击坐标（用于确认按钮等）
POS_RESET = (1026, 955)                     # 重置
POS_CONFIRM_1 = (958, 661)                  # 胜利后确认
POS_COLLECT = (1406, 895)                   # 一键领取
POS_CONFIRM_2 = (873, 579)                  # 领取确认
POS_CONFIRM_3 = (950, 675)                  # 最终确认
POS_EXIT = (1629, 136)                      # 退出

# 战斗入口坐标（三次战斗）
BATTLES = [
    (865, 275),      # 第一场
    (1190, 344),     # 第二场（循环2次）
    (815, 499)       # 第三场
]
SECOND_BATTLE_REPEAT = 2                    # 第二场重复次数

# 全局参数
CONFIDENCE = 0.8
CLICK_DURATION = 0.3
WAIT_TIMEOUT = 15                           # 等待图片出现的最长时间（秒）
RETRY_INTERVAL = 0.5                        # 每次检查间隔
# ===========================================

def click_pos(x, y, duration=CLICK_DURATION):
    """移动并点击指定坐标"""
    pyautogui.moveTo(x, y, duration=duration)
    pyautogui.click()

def wait_and_click_image(image_path, region=None, confidence=CONFIDENCE,
                         timeout=WAIT_TIMEOUT, click_offset=None):
    """
    等待图片出现并点击其中心（或偏移位置）
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :param timeout: 超时秒数
    :param click_offset: (dx, dy) 相对中心偏移点击，为None则点击中心
    :return: True 点击成功，False 超时未找到
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            location = pyautogui.locateOnScreen(image_path, region=region, confidence=confidence)
            if location:
                center = pyautogui.center(location)
                if click_offset:
                    click_x = center.x + click_offset[0]
                    click_y = center.y + click_offset[1]
                else:
                    click_x, click_y = center.x, center.y
                click_pos(click_x, click_y)
                return True
        except Exception as e:
            # pyautogui有时会抛出异常，忽略继续
            pass
        time.sleep(RETRY_INTERVAL)
    print(f"⚠️ 超时未找到图片: {image_path}")
    return False

def do_battle(entry_x, entry_y):
    """
    执行一场完整的战斗
    :param entry_x: 战斗入口点击X坐标（无需图像匹配，直接点击）
    :param entry_y: 战斗入口点击坐标（无需图像匹配，直接点击）
    :return: True 成功，False 失败
    """
    # 1. 点击入口
    time.sleep(1)
    click_pos(entry_x, entry_y)
    time.sleep(0.5)

    # 2. 等待并点击“开始战斗”
    success = wait_and_click_image(IMG_START, region=REGION_START)
    if not success:
        print("❌ 未找到开始按钮，尝试重点击入口...")
        click_pos(entry_x, entry_y)          # 重新点击入口
        time.sleep(1)
        success = wait_and_click_image(IMG_START, region=REGION_START)
        if not success:
            print("❌ 战斗启动失败，跳过本场")
            return False

    # 3. 等待胜利标志（出现后点击确定按钮）
    victory_found = wait_and_click_image(IMG_VICTORY, region=REGION_VICTORY,
                                         click_offset=None)  # 这里只是等待出现，不点击胜利图
    if victory_found:
        # 点击确认按钮（固定坐标）
        click_pos(POS_CONFIRM_1[0], POS_CONFIRM_1[1])
        print("✅ 战斗完成")
        return True
    else:
        print("⚠️ 未检测到胜利标志，但仍尝试点击确认")
        click_pos(POS_CONFIRM_1[0], POS_CONFIRM_1[1])
        return False

def main():
    print("====== 开始生存演习 ======")

    # 1. 点击生存入口（使用图像识别）
    print("寻找生存入口...")
    if not wait_and_click_image(IMG_ENTRY, region=REGION_ENTRY):
        print("❌ 无法找到生存入口，退出")
        return
    time.sleep(1)

    # 2. 重置（回到主界面）
    print("重置界面...")
    click_pos(POS_RESET[0], POS_RESET[1])
    time.sleep(1)

    # 3. 第一场战斗
    print("执行第一场战斗...")
    do_battle(BATTLES[0][0], BATTLES[0][1])

    # 4. 第二场战斗（重复N次）
    for i in range(SECOND_BATTLE_REPEAT):
        print(f"执行第二场战斗 (第{i+1}次)...")
        do_battle(BATTLES[1][0], BATTLES[1][1])

    # 5. 第三场战斗
    print("执行第三场战斗...")
    do_battle(BATTLES[2][0], BATTLES[2][1])

    # 6. 一键领取奖励
    print("领取奖励...")
    time.sleep(1.5)
    click_pos(POS_COLLECT[0], POS_COLLECT[1])
    time.sleep(0.5)
    click_pos(POS_CONFIRM_2[0], POS_CONFIRM_2[1])
    time.sleep(0.5)
    click_pos(POS_CONFIRM_3[0], POS_CONFIRM_3[1])

    # 7. 退出生存演习
    print("退出演习...")
    click_pos(POS_EXIT[0], POS_EXIT[1])

    print("====== 全部完成 ======")

if __name__ == "__main__":
    main()