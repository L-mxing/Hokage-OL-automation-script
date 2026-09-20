import time

from screenshot_utils import (auto_battle_with_speed, click_pos, wait_and_click_image,
                              ensure_utf8_stdout, check_screen_size)

# ================= 配置区域 =================
# 图片路径
IMG_ENTRY = 'image/QZ/QZ_01_Entrance.png'  # 强者降临入口图标
IMG_START_WAR = 'image/QZ/QZ_02_start_btn.png'  # 开战按钮
IMG_VICTORY = 'image/QZ/QZ_03_victory_btn.png'  # 胜利标志按钮
IMG_CLEAR = 'image/QZ/QZ_04_clear_btn.png'  # 通关奖励确认按钮

# 战斗界面（自动战斗 / 倍速）图标
# 用 *_tight 版：原图把按钮周围的战斗场景背景也框进去了，背景一变分数就被拖垮。
# 对称内缩 8px/10px 后实测（真机帧，同状态/异状态）：自动战斗 0.995 / 0.260，
# 倍速 0.963 / 0.713 —— 阈值两侧余量充足；且裁剪框中心 = 原中心，点击不会偏。
IMG_AUTO_OFF = 'image/game_ui_snapshots/auto_disable.png'  # 自动战斗未开启（绿色“自动”）
IMG_AUTO_ON = 'image/game_ui_snapshots/auto_enable.png'  # 自动战斗已开启（红色“取消”）
IMG_SPEED_SLOW = 'image/game_ui_snapshots/tag_speed_1x.png'  # 倍速 x1（还没加速）
IMG_SPEED_FAST = 'image/game_ui_snapshots/tag_speed_2x.png'  # 倍速 x2（目标档位）

# 搜索区域（加速匹配）
REGION_ENTRY = (1130, 71, 245, 55)  # 强者降临入口搜索区域
REGION_START_WAR = (896, 601, 127, 39)  # 开战按钮搜索区域
REGION_VICTORY = (896, 624, 129, 39)  # 胜利标志按钮搜索区域
REGION_CLEAR = (888, 639, 125, 34)  # 通关奖励确认按钮区域
REGION_BATTLE_UI = (1185, 860, 154, 100)  # 战斗界面图标区域（自动战斗 + 倍速）

# 固定点击坐标
POS_ENTER_BATTLE = (800, 660)  # “进入战斗”按钮
POS_START_WAR = (955, 620)  # 开战按钮（如果图像识别失败，则点击此坐标）
POS_CONFIRM = (956, 643)  # 胜利后“确认”按钮
POS_CLEAR_CONFIRM = (945, 655)  # 通关奖励确认按钮

# 循环次数
ROUNDS = 6

# 战斗界面（自动战斗 + 倍速）只需本进程确认一次：
# 第一次检测确认"自动 + 倍速都已开启"后置 True，后续轮次直接跳过，不再轮询这两个按钮。
# 三个脚本各自记一次（都是独立子进程，互不影响）。
_battle_ui_ready = False


# ===========================================


def do_battle_round(round_num):
    """
    执行一次强者降临关卡（完全依照原脚本逻辑）
    :param round_num: 当前轮次
    :return: True 成功，False 失败
    """
    global _battle_ui_ready
    print(f"\n====== 第 {round_num} 轮开始 ======")

    # 1. 点击“强者降临”入口（使用图像识别，点击图片中心）
    print("寻找强者降临入口...")

    entry = wait_and_click_image(IMG_ENTRY, region=REGION_ENTRY, desc="强者降临入口")
    if not entry:
        print("❌ 未找到强者降临入口，尝试重新识别一次...")
        time.sleep(1)
        entry = wait_and_click_image(IMG_ENTRY, region=REGION_ENTRY, desc="强者降临入口")
        if not entry:
            print("❌ 找不到强者降临入口，跳过本轮")
            return False

    time.sleep(1)  # 等待界面切换

    # 2. 点击“进入战斗”（固定坐标）
    print("点击进入战斗按钮...")
    click_pos(POS_ENTER_BATTLE[0], POS_ENTER_BATTLE[1])
    time.sleep(2)  # 等待加载

    # 3.检测并点击开战按钮
    print("等待并点击开战按钮...")
    start_war = wait_and_click_image(IMG_START_WAR, region=REGION_START_WAR, desc="开战按钮")
    if not start_war:
        print("❌ 未检测到开战按钮，尝试直接点击固定坐标...")
        click_pos(POS_START_WAR[0], POS_START_WAR[1])
    time.sleep(1)  # 等待战斗开始

    # 战斗开始：检测一次自动战斗与倍速。本进程确认过一次就不再重复检测这两个按钮。
    if _battle_ui_ready:
        print("自动战斗与倍速已在本进程确认开启，跳过重复检测")
    else:
        report = auto_battle_with_speed(IMG_AUTO_OFF, IMG_SPEED_SLOW, region=REGION_BATTLE_UI,
                                        auto_on_image=IMG_AUTO_ON, speed_fast_image=IMG_SPEED_FAST,
                                        desc=f"强者降临 第 {round_num} 轮")
        _battle_ui_ready = report.ok

    # 4. 检测胜利标志按钮
    victory_found = wait_and_click_image(IMG_VICTORY, region=REGION_VICTORY, desc="胜利标志按钮")
    if not victory_found:
        # 如果没检测到胜利，也尝试点击确认（防止漏检）
        print("⚠️ 未检测到胜利，尝试直接点击确认")
        click_pos(POS_CONFIRM[0], POS_CONFIRM[1])

    # 5. 检测通关标识,领取奖励
    clear_found = wait_and_click_image(IMG_CLEAR, region=REGION_CLEAR, timeout=10, desc="通关奖励确认")
    if clear_found:
        # 点击通关后的“确认”按钮（固定坐标）
        print("点击通关确认...")
        click_pos(POS_CLEAR_CONFIRM[0], POS_CLEAR_CONFIRM[1])
    else:
        # 未检测到通关标识，可能已自动完成，也尝试点击确认
        print("⚠️ 未检测到通关，尝试点击通关确认")
        click_pos(POS_CLEAR_CONFIRM[0], POS_CLEAR_CONFIRM[1])

    print(f"====== 第 {round_num} 轮完成 ======\n")
    return True


def main():
    print("====== 强者降临自动刷图脚本启动 ======")
    print(f"将执行 {ROUNDS} 轮")
    print("请确保游戏窗口在最前，1秒后开始...")
    time.sleep(1)

    for i in range(1, ROUNDS + 1):
        do_battle_round(i)
        time.sleep(1)  # 轮次间隔

    print("====== 全部轮次执行完毕 ======")


if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    main()
