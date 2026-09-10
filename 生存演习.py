import time

from screenshot_utils import click_pos, wait_and_click_image,wait_image

# ================= 配置区域 =================
# 图片路径
IMG_ENTRY = 'image/SC/SC_01_Entrance.png'
IMG_START = 'image/SC/SC_02_start_btn.png'
IMG_VICTORY = 'image/SC/SC_03_victory_flag.png'

# 区域限制（加速匹配）
REGION_ENTRY = (1020, 76, 380, 70)  # 生存入口
REGION_START = (878,210,172,63)  # 开始按钮
REGION_VICTORY = (895,641,134,40) # 胜利后确认

# 固定点击坐标（用于确认按钮等）
POS_RESET = (1026, 955)  # 重置
POS_CONFIRM_1 = (958, 661)  # 胜利后确认
POS_COLLECT = (1406, 895)  # 一键领取
POS_CONFIRM_2 = (873, 579)  # 领取确认
POS_CONFIRM_3 = (950, 675)  # 最终确认
POS_EXIT = (1629, 136)  # 退出

# 战斗入口坐标（三次战斗）
BATTLES = [
    (865, 275),  # 第一场
    (1190, 344),  # 第二场（循环2次）
    (815, 499)  # 第三场
]
SECOND_BATTLE_REPEAT = 2  # 第二场重复次数


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
    success = wait_and_click_image(IMG_START, region=REGION_START,desc='开始按钮')
    if not success:
        print("❌ 未找到开始按钮，尝试重点击入口...")
        click_pos(entry_x, entry_y)  # 重新点击入口
        time.sleep(1)
        success = wait_and_click_image(IMG_START, region=REGION_START,desc='开始按钮')
        if not success:
            print("❌ 战斗启动失败，跳过本场")
            return False

    # 3. 等待胜利标志（出现后点击确定按钮）
    center = wait_image(IMG_VICTORY, region=REGION_VICTORY, confidence=0.7,
                        desc='胜利后确认按钮')
    if center:
        print("发现胜利确认按钮")
        # 校验：中心应该接近 (958, 661)，允许 ±40px
        if abs(center[0] - 958) < 40 and abs(center[1] - 661) < 30:
            click_pos(center[0], center[1])
        else:
            print("坐标偏差大，点击固定指标")
            click_pos(POS_CONFIRM_1[0], POS_CONFIRM_1[1])  # 偏差大，用固定坐标兜底
    else:
        print("未检测到胜利确认按钮，尝试点击固定坐标")
        click_pos(POS_CONFIRM_1[0], POS_CONFIRM_1[1])

    return True



def main():
    print("====== 开始生存演习 ======")

    # 1. 点击生存入口（使用图像识别）
    print("寻找生存入口...")
    if not wait_and_click_image(IMG_ENTRY, region=REGION_ENTRY,desc='生存入口'):
        print("❌ 无法找到生存入口，退出")
        return
    time.sleep(1)

    # 2. 重置（回到主界面）
    print("重置界面...")
    click_pos(*POS_RESET)
    time.sleep(1)

    # 3. 第一场战斗
    print("执行第一场战斗...")
    do_battle(*BATTLES[0])

    # 4. 第二场战斗（重复N次）
    for i in range(SECOND_BATTLE_REPEAT):
        print(f"执行第二场战斗 (第{i + 1}次)...")
        do_battle(*BATTLES[1])

    # 5. 第三场战斗
    print("执行第三场战斗...")
    do_battle(*BATTLES[2])

    # 6. 一键领取奖励
    print("领取奖励...")
    time.sleep(1.5)
    click_pos(*POS_COLLECT)
    time.sleep(0.5)
    click_pos(*POS_CONFIRM_2)
    time.sleep(0.5)
    click_pos(*POS_CONFIRM_3)

    # 7. 退出生存演习
    print("退出演习...")
    click_pos(*POS_EXIT)

    print("====== 全部完成 ======")


if __name__ == "__main__":
    main()
