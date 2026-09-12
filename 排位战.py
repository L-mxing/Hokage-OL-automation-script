import time

from screenshot_utils import (click_pos, wait_and_click_image, wait_image,
                              ensure_utf8_stdout, check_screen_size)

# ================= 配置区域 =================

# 图片相对路径
IMG_RANK_ENTRANCE = 'image/PW/PW_01_Entrance.png'  # 排位入口
IMG_CONFIRM = 'image/PW/PW_02_confirm.png'  # 确认

# 图像搜索范围
REGION_RANK_ENTRANCE = (890, 73, 239, 63)
REGION_CONFIRM = (900, 630, 130, 80)

# 固定位置坐标参数
POS_CHALLENGE = (609, 885)  # 挑战
POS_END_BATTLE = (1481, 925)  # 结束
POS_CONFIRM = (960, 673)  # 确认
POS_EXIT_RANK_UI = (1635, 121)  # 退出排位界面

# 循环次数
ROUNDS = 5


def do_battle_round(round_num):
    """
    执行一次排位战斗
    :param round_num: 当前轮次
    :return: True 成功，False 失败
    """
    print(f"\n====== 第 {round_num} 轮开始 ======")

    # 1. 点击 排位战 入口
    if round_num == 1:
        print("寻找排位战入口并进入排位...")

        entry = wait_and_click_image(IMG_RANK_ENTRANCE, region=REGION_RANK_ENTRANCE, desc='排位战入口')
        if not entry:
            print("❌ 未找到排位战入口，尝试重点击入口...")
            entry = wait_and_click_image(IMG_RANK_ENTRANCE, region=REGION_RANK_ENTRANCE, desc='排位战入口')
            if not entry:
                print("❌ 寻找排位战入口失败")
                return False

    # 2. 点击'挑战'按钮
    print("点击'挑战'按钮")
    time.sleep(1)
    click_pos(POS_CHALLENGE[0], POS_CHALLENGE[1])

    # 3. 点击 '结束' 按钮
    print("点击 '结束' 按钮")
    time.sleep(1)
    click_pos(POS_END_BATTLE[0], POS_END_BATTLE[1])

    # 4. 查找'确认' 按钮
    confirm_btn = wait_image(IMG_CONFIRM, region=REGION_CONFIRM, confidence=0.8,timeout=60,
                        desc='战斗结束确认按钮')
    if confirm_btn:
        print("发现确认按钮")
        # 校验：中心应该接近 (958, 661)，允许 ±40px
        if abs(confirm_btn[0] - 960) < 40 and abs(confirm_btn[1] - 673) < 30:
            click_pos(confirm_btn[0], confirm_btn[1])
        else:
            print("坐标偏差大，点击固定指标")
            click_pos(POS_CONFIRM[0], POS_CONFIRM[1])  # 偏差大，用固定坐标兜底
    else:
        print("未检测到确认按钮，尝试点击固定坐标")
        click_pos(POS_CONFIRM[0], POS_CONFIRM[1])

    print(f"====== 第 {round_num} 轮排位战完成 ======\n")

    return True


def main():
    print("====== 排位战自动刷图脚本启动 ======")
    print("脚本启动，按 Ctrl + C 可停止")
    print(f"将执行 {ROUNDS} 轮")
    print("请确保游戏窗口在最前，1秒后开始...")
    time.sleep(1)

    for i in range(1, ROUNDS + 1):
        do_battle_round(i)
        time.sleep(1)  # 轮次间隔

    time.sleep(1)
    click_pos(POS_EXIT_RANK_UI[0], POS_EXIT_RANK_UI[1])

    print("====== 排位战全部轮次执行完毕 ======")


if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    main()
