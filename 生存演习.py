import time

from screenshot_utils import (auto_battle_with_speed, click_pos, wait_and_click_image, wait_image,
                              ensure_utf8_stdout, check_screen_size)

# ================= 配置区域 =================
# 图片路径
IMG_ENTRY = 'image/SC/SC_01_Entrance.png'# 入口
IMG_RESET1 = 'image/SC/SC_02_reset1.png' # 重置(剩余1次)
IMG_RESET0 = 'image/SC/SC_03_reset0.png' # 重置(剩余0次)
IMG_START = 'image/SC/SC_04_start_btn.png' # 开战
IMG_VICTORY = 'image/SC/SC_05_victory_flag.png'# 确认胜利


# 战斗界面（自动战斗 / 倍速）图标
# 用 *_tight 版：原图把按钮周围的战斗场景背景也框进去了，背景一变分数就被拖垮。
# 对称内缩 8px/10px 后实测（真机帧，同状态/异状态）：自动战斗 0.995 / 0.260，
# 倍速 0.963 / 0.713 —— 阈值两侧余量充足；且裁剪框中心 = 原中心，点击不会偏。
IMG_AUTO_OFF = 'image/game_ui_snapshots/auto_disable.png'  # 自动战斗未开启（绿色“自动”）
IMG_AUTO_ON = 'image/game_ui_snapshots/auto_enable.png'  # 自动战斗已开启（红色“取消”）
IMG_SPEED_SLOW = 'image/game_ui_snapshots/tag_speed_1x.png'  # 倍速 x1（还没加速）
IMG_SPEED_FAST = 'image/game_ui_snapshots/tag_speed_2x.png'  # 倍速 x2（目标档位）

# 区域限制（加速匹配）
REGION_ENTRY = (1020, 76, 380, 70)  # 生存入口
REGION_RESET = (949,926,1115,979)  # 重置
REGION_START = (878, 210, 172, 63)  # 开始按钮
REGION_VICTORY = (895, 641, 134, 40)  # 胜利后确认
REGION_BATTLE_UI = (1185, 860, 154, 100)  # 战斗界面图标区域（自动战斗 + 倍速）

# 固定点击坐标（用于确认按钮等）
POS_RESET = (1026, 955)  # 重置
POS_START = (960, 250)  # 开战
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

# 战斗界面（自动战斗 + 倍速）只需本进程确认一次：
# 第一次检测确认"自动 + 倍速都已开启"后置 True，后续场次直接跳过，不再轮询这两个按钮。
# 三个脚本各自记一次（都是独立子进程，互不影响）。
_battle_ui_ready = False


def do_battle(entry_x, entry_y):
    """
    执行一场完整的战斗
    :param entry_x: 战斗入口点击X坐标（无需图像匹配，直接点击）
    :param entry_y: 战斗入口点击坐标（无需图像匹配，直接点击）
    :return: True 成功，False 失败
    """
    global _battle_ui_ready
    # 1. 点击入口
    time.sleep(1)
    click_pos(entry_x, entry_y)
    time.sleep(0.5)

    # 2. 等待并点击“开始战斗”
    success = wait_image(IMG_START, region=REGION_START, desc='开始按钮')
    time.sleep(0.5)
    click_pos(*POS_START)
    if not success:
        print("❌ 未找到开始按钮，尝试重点击入口...")
        click_pos(entry_x, entry_y)  # 重新点击入口
        time.sleep(1)
        success = wait_image(IMG_START, region=REGION_START, desc='开始按钮')
        time.sleep(0.5)
        click_pos(*POS_START)
        if not success:
            print("❌ 战斗启动失败，跳过本场")
            return False

    # 战斗开始：检测一次自动战斗与倍速。本进程确认过一次就不再重复检测这两个按钮。
    time.sleep(1)  # 等战斗界面出现
    if _battle_ui_ready:
        print("自动战斗与倍速已在本进程确认开启，跳过重复检测")
    else:
        report = auto_battle_with_speed(IMG_AUTO_OFF, IMG_SPEED_SLOW, region=REGION_BATTLE_UI,
                                        auto_on_image=IMG_AUTO_ON, speed_fast_image=IMG_SPEED_FAST,
                                        desc="生存演习")
        _battle_ui_ready = report.ok

    # 3. 等待胜利确认标志（出现后点击确定按钮）
    center = wait_image(IMG_VICTORY, region=REGION_VICTORY, confidence=0.7,
                        desc='胜利后确认按钮')
    if center:
        print("发现胜利确认按钮")
        # 校验：中心应该接近 (958, 661)，允许 ±40px
        if abs(center[0] - 958) < 40 and abs(center[1] - 661) < 30:
            click_pos(center[0], center[1])
        else:
            print(f"坐标偏差大，点击固定指标{POS_CONFIRM_1}")
            click_pos(*POS_CONFIRM_1)  # 偏差大，用固定坐标兜底
    else:
        print(f"未检测到胜利确认按钮，尝试点击固定坐标{POS_CONFIRM_1}")
        click_pos(*POS_CONFIRM_1)

    return True


def main():
    print("====== 开始生存演习 ======")

    # 1. 点击生存入口（使用图像识别）
    print("寻找生存入口...")
    if not wait_and_click_image(IMG_ENTRY, region=REGION_ENTRY, desc='生存入口'):
        print("❌ 无法找到生存入口，退出")
        return
    time.sleep(0.75)

    # 2. 重置（回到主界面）
    print("重置演习一次...")
    wait_and_click_image(IMG_RESET1,region=REGION_RESET)
    time.sleep(1)

    # 3~5. 三场战斗。do_battle 返回 False 表示这场没打起来（找不到开始按钮），
    # 此时继续往下点「领取奖励」会点到错误的界面，所以直接中止本轮。
    battles = [(BATTLES[0], "第一场")]
    battles += [(BATTLES[1], f"第二场(第{i + 1}次)") for i in range(SECOND_BATTLE_REPEAT)]
    battles.append((BATTLES[2], "第三场"))

    for pos, label in battles:
        print(f"执行{label}战斗...")
        if not do_battle(*pos):
            print(f"❌ {label}战斗未能启动，中止本轮（避免在错误界面继续点击）")
            return

    # 6. 一键领取奖励
    print("领取奖励...")
    time.sleep(1.5)
    click_pos(*POS_COLLECT)
    time.sleep(0.75)
    click_pos(*POS_CONFIRM_2)
    time.sleep(0.5)
    click_pos(*POS_CONFIRM_3)

    # 7. 退出生存演习
    print("退出演习...")
    click_pos(*POS_EXIT)

    print("====== 全部完成 ======")


if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    main()
