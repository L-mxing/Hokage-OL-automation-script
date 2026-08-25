import pyautogui
import time
import sys

# ---------- 您的原始坐标 ----------

# 图像搜索范围
enter_btn = (835, 543, 108, 40)        # “确认进入”按钮图像范围
arraying_btn = (971, 599, 127, 44)     # “布阵”按钮图像范围（备用）
zd_jiesuan = (1155, 801, 165, 42)      # 游戏结算标识（您已调整）

# 固定点击坐标
entrance_1 = (95, 220)
entrance_2 = (1225, 688)
choice = (751, 445)
No_prompt = (821, 603)
zd_start_btn = (885, 620)              # 开战按钮坐标
return_btn = (1235, 825)               # 返回游戏按钮

WAIT_TIMEOUT = 30
CONFIDENCE = 0.8
CHECK_INTERVAL = 0.5

# ---------- 辅助函数 ----------
def safe_click(pos, duration=0.3):
    """安全点击坐标"""
    pyautogui.click(pos[0], pos[1], duration=duration)
    time.sleep(0.5)

def wait_for_settlement_and_return():
    """
    等待结算标识出现，点击返回并跳出循环。
    返回 True 表示成功返回（可选），False 表示超时未检测到结算。
    """
    start_time = time.time()
    while time.time() - start_time < WAIT_TIMEOUT:
        try:
            location = pyautogui.locateOnScreen(
                'image1/zd_jiesuan.png',
                region=zd_jiesuan,
                confidence=CONFIDENCE
            )
            if location:
                print("检测到结算标识，点击返回")
                time.sleep(1)
                safe_click(return_btn)
                # 若需验证返回，可在这里检测布阵按钮是否出现
                # 此处先跳出循环
                return True
        except pyautogui.ImageNotFoundException:
            # 未找到图片，继续等待
            pass
        time.sleep(CHECK_INTERVAL)
    print("等待结算超时")
    return False

def wait_for_battle_start():
    """
    等待“开战”按钮可用（简单延时，或检测布阵按钮）
    """
    # 方法1：简单等待2秒，确保界面加载
    # time.sleep(2)
    # 方法2（可选）：循环检测布阵图片，若出现则点击开战
    # 此处保留您的直接点击方式，仅增加等待
    # 若您希望更稳健，可启用下方代码：
    start = time.time()
    while time.time() - start < 5:
        try:
            loc = pyautogui.locateOnScreen('image1/zd_start_btn.png', region=arraying_btn, confidence=CONFIDENCE)
            if loc:
                return True
        except:
            pass
        time.sleep(0.5)
    return True

# ---------- 主流程 ----------
def main():
    print("脚本启动，按 Ctrl+C 可停止")
    time.sleep(2)

    # 1. 进入副本（两个入口）
    safe_click(entrance_1)
    time.sleep(1.5)
    safe_click(entrance_2)
    time.sleep(1.5)

    # 2. 选择关卡
    safe_click(choice)
    time.sleep(1.5)

    # 3. 点击“确认进入”（按您的顺序：先 No_prompt，再确认按钮）
    safe_click(No_prompt)
    time.sleep(0.5)
    try:
        location = pyautogui.locateOnScreen('image1/zudui_btn.png', region=enter_btn, confidence=CONFIDENCE)
        if location:
            pyautogui.click(location, duration=0.3)
            time.sleep(0.5)
    except pyautogui.ImageNotFoundException:
        print("未找到确认进入按钮，可能已进入队伍")

    # 4. 等待开战并点击
    wait_for_battle_start()
    safe_click(zd_start_btn)

    # 5. 等待结算并返回（首次）
    if not wait_for_settlement_and_return():
        print("首次战斗结算超时，继续执行")

    # 6. 后续循环（共执行 5 次，含首次）
    for i in range(1, 5):   # 再打 4 次
        print(f"\n===== 第 {i+1} 次战斗 =====")
        time.sleep(2)
        safe_click(choice)
        time.sleep(1.5)
        wait_for_battle_start()
        safe_click(zd_start_btn)
        if not wait_for_settlement_and_return():
            print(f"第 {i+1} 次战斗结算超时，继续下一次")

    print("脚本执行完毕")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n用户中断，脚本退出")
        sys.exit(0)