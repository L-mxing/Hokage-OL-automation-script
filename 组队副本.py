import time

from screenshot_utils import (auto_battle_with_speed, click_pos, wait_and_click_image,
                              ensure_utf8_stdout, check_screen_size)

# ================= 配置区域 =================

# 图片相对路径
IMG_DUNGEON_ENTRANCE = 'image/ZD/ZD_01_dungeon_entrance.png'  # 组队副本入口
IMG_CONFIRM_ENTER = 'image/ZD/ZD_02_confirm_enter.png'  # 确认进入
IMG_START_BATTLE = 'image/ZD/ZD_03_start_battle.png'  # 开战按钮
IMG_SETTLEMENT = 'image/ZD/ZD_04_settlement.png'  # 结算返回按钮

# 战斗界面（自动战斗 / 倍速）图标
# 用 *_tight 版：原图把按钮周围的战斗场景背景也框进去了，背景一变分数就被拖垮。
# 对称内缩 8px/10px 后实测（真机帧，同状态/异状态）：自动战斗 0.995 / 0.260，
# 倍速 0.963 / 0.713 —— 阈值两侧余量充足；且裁剪框中心 = 原中心，点击不会偏。
IMG_AUTO_OFF = 'image/game_ui_snapshots/auto_disable.png'  # 自动战斗未开启（绿色“自动”）
IMG_AUTO_ON = 'image/game_ui_snapshots/auto_enable.png'  # 自动战斗已开启（红色“取消”）
IMG_SPEED_SLOW = 'image/game_ui_snapshots/tag_speed_1x.png'  # 倍速 x1（还没加速）
IMG_SPEED_FAST = 'image/game_ui_snapshots/tag_speed_2x.png'  # 倍速 x2（目标档位）

# 图像搜索范围
REGION_DUNGEON_ENTRANCE = (20, 186, 200, 192)  # 组队副本入口
REGION_CONFIRM_ENTER = (835, 543, 108, 40)  # “确认进入”按钮
REGION_START_BATTLE = (840, 603, 103, 40)  # “开战”按钮
REGION_SETTLEMENT = (1155, 801, 165, 42)  # 游戏结算，返回游戏
REGION_BATTLE_UI = (1185, 860, 154, 100)  # 战斗界面图标区域（自动战斗 + 倍速）

# 固定点击坐标

POS_JOIN = (1225, 688)  # 参与按钮坐标
POS_DUNGEON_1 = (751, 445)  # 选择副本关卡
POS_NO_PROMPT = (821, 603)# 不再提示
POS_START_BATTLE = (888, 620)  # 开战按钮坐标
POS_SETTLEMENT = (1235, 825)  # 返回游戏按钮
POS_EXIT_DUNGEON = (1420,285) # 退出组队副本页面


# 循环次数
ROUNDS = 5

# 战斗界面（自动战斗 + 倍速）只需本进程确认一次：
# 第一次检测确认"自动 + 倍速都已开启"后置 True，后续轮次直接跳过，不再轮询这两个按钮。
# 三个脚本各自记一次（都是独立子进程，互不影响）。
_battle_ui_ready = False


def do_battle_round(round_num):
    """
    执行一次组队副本关卡
    :param round_num: 当前轮次
    :return: True 成功，False 失败
    """
    global _battle_ui_ready
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
                print("❌ 寻找组队副本入口失败")
                return False

        time.sleep(1.5)
        print("参与副本")
        click_pos(*POS_JOIN)  # 参与组队副本

    # 2. 选择关卡
    time.sleep(1)
    click_pos(*POS_DUNGEON_1)

    # 3. 点击“确认进入”（按您的顺序：先 POS_NO_PROMPT，再确认按钮）
    if round_num == 1:
        time.sleep(1)
        click_pos(*POS_NO_PROMPT)  # 点击“不再提示”
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
        click_pos(*POS_START_BATTLE)

    # 战斗开始：检测一次自动战斗与倍速。本进程确认过一次就不再重复检测这两个按钮。
    time.sleep(1)  # 等战斗界面出现
    if _battle_ui_ready:
        print("自动战斗与倍速已在本进程确认开启，跳过重复检测")
    else:
        report = auto_battle_with_speed(IMG_AUTO_OFF, IMG_SPEED_SLOW, region=REGION_BATTLE_UI,
                                        auto_on_image=IMG_AUTO_ON, speed_fast_image=IMG_SPEED_FAST,
                                        desc=f"组队副本 第 {round_num} 轮")
        _battle_ui_ready = report.ok

    # 5. 等待结算并返回
    settlement = wait_and_click_image(IMG_SETTLEMENT, region=REGION_SETTLEMENT, desc='结算标识')
    if not settlement:
        print("尝试点击结算坐标")
        click_pos(*POS_SETTLEMENT)

    print(f"====== 第 {round_num} 轮完成 ======\n")
    return True


# ---------- 主流程 ----------
def main():
    print("====== 组队副本自动刷图脚本启动 ======")
    print(f"将执行 {ROUNDS} 轮")
    print("请确保游戏窗口在最前，1秒后开始...")
    time.sleep(1)

    for i in range(1, ROUNDS + 1):
        do_battle_round(i)
        time.sleep(2)  # 轮次间隔

    click_pos(*POS_EXIT_DUNGEON)

    print("====== 全部轮次执行完毕 ======")


if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    main()
