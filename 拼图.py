import pyautogui
import cv2
import numpy as np
import time
import keyboard
import random
from PIL import Image

# ====================== 配置区 ======================
GRID_ROWS = 6
GRID_COLS = 8
PIECE_W = 110
PIECE_H = 110

# 需要用pick_coordinate_tool工具获取你的屏幕真实坐标
CANVAS_X1, CANVAS_Y1 = 62, 126
CANVAS_W = PIECE_W * GRID_COLS
CANVAS_H = PIECE_H * GRID_ROWS

POOL_X1, POOL_Y1 = 698, 160
POOL_W = 760
POOL_H = 770

FULL_PUZZLE_PATH = "full_puzzle.png"
MATCH_THRESHOLD = 16       # ORB匹配阈值，越小越严格
MAX_RETRY = 3              # 碎片识别失败最大重试次数
MIN_DRAG_SPEED = 0.18
MAX_DRAG_SPEED = 0.32
MIN_WAIT = 0.25
MAX_WAIT = 0.45

DEBUG = False  # 打开会打印更多调试信息
# ====================================================

def pick_coordinate_tool():
    """坐标拾取工具：鼠标移动到目标位置，按【空格】输出坐标，按【ESC】退出拾取"""
    print("\n=====坐标拾取工具=====")
    print("移动鼠标到目标点，按【空格】输出坐标，按【ESC】结束拾取\n")
    while True:
        if keyboard.is_pressed("esc"):
            print("退出拾取工具")
            break
        if keyboard.is_pressed("space"):
            x, y = pyautogui.position()
            print(f"X = {x}, Y = {y}")
            time.sleep(0.3)


def split_full_puzzle(full_img_path):
    """切分完整拼图，生成每一块碎片的ORB特征"""
    img = cv2.imread(full_img_path)
    if img is None:
        raise FileNotFoundError(f"图片 {full_img_path} 未找到，请放到脚本同目录！")
    piece_data = []
    orb = cv2.ORB_create(500)
    for r in range(GRID_ROWS):
        row_data = []
        for c in range(GRID_COLS):
            x0 = c * PIECE_W
            y0 = r * PIECE_H
            patch = img[y0:y0 + PIECE_H, x0:x0 + PIECE_W]
            kp, des = orb.detectAndCompute(patch, None)
            row_data.append({
                "patch": patch,
                "kp": kp,
                "des": des,
                "row": r,
                "col": c
            })
        piece_data.append(row_data)
    return piece_data, orb


def is_piece_already_placed(screen_bgr, row, col):
    """检测画布上该网格位置是否已经拼好碎片，直接跳过"""
    x_canvas = CANVAS_X1 + col * PIECE_W
    y_canvas = CANVAS_Y1 + row * PIECE_H
    roi = screen_bgr[y_canvas:y_canvas+PIECE_H, x_canvas:x_canvas+PIECE_W]
    gray = cv2.cvtColor(roi, cv2.COLOR_BGR2GRAY)
    # 检测拼图缝隙黑线，如果已经填充，边缘会很少
    edges = cv2.Canny(gray, 40, 120)
    total_edge = np.sum(edges > 0)
    # 空白格子缝隙线条多，已经拼好的碎片线条少
    return total_edge < 1200


def search_piece_in_pool(screen_bgr, orb, target_piece_info):
    """ORB特征匹配，在碎片池寻找碎片，返回屏幕坐标，找不到返回None"""
    pool_img = screen_bgr[POOL_Y1:POOL_Y1 + POOL_H, POOL_X1:POOL_X1 + POOL_W]
    kp_pool, des_pool = orb.detectAndCompute(pool_img, None)
    if des_pool is None or target_piece_info["des"] is None:
        return None

    bf = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)
    matches = bf.match(target_piece_info["des"], des_pool)
    matches = sorted(matches, key=lambda x: x.distance)

    good_matches = [m for m in matches if m.distance < MATCH_THRESHOLD]
    if len(good_matches) < 8:
        if DEBUG:
            print(f"匹配点数量不足:{len(good_matches)}")
        return None

    src_pts = np.float32([target_piece_info["kp"][m.queryIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    dst_pts = np.float32([kp_pool[m.trainIdx].pt for m in good_matches]).reshape(-1, 1, 2)
    M, mask = cv2.findHomography(src_pts, dst_pts, cv2.RANSAC, 5.0)
    if M is None:
        return None

    h, w = target_piece_info["patch"].shape[:2]
    pts = np.float32([[0, 0], [0, h - 1], [w - 1, h - 1], [w - 1, 0]]).reshape(-1, 1, 2)
    dst = cv2.perspectiveTransform(pts, M)
    center_offset = np.mean(dst, axis=0)[0]
    screen_x = POOL_X1 + center_offset[0]
    screen_y = POOL_Y1 + center_offset[1]
    return (screen_x, screen_y)


def get_canvas_target_pos(row, col):
    target_x = CANVAS_X1 + col * PIECE_W + PIECE_W // 2
    target_y = CANVAS_Y1 + row * PIECE_H + PIECE_H // 2
    return (target_x, target_y)


def drag(from_pos, to_pos):
    """随机拖拽速度模拟真人，ESC随时中断"""
    if keyboard.is_pressed("esc"):
        raise KeyboardInterrupt("用户按下ESC终止脚本")
    sx, sy = from_pos
    tx, ty = to_pos
    drag_speed = random.uniform(MIN_DRAG_SPEED, MAX_DRAG_SPEED)
    wait_time = random.uniform(MIN_WAIT, MAX_WAIT)

    pyautogui.moveTo(sx, sy, duration=random.uniform(0.08,0.14))
    pyautogui.mouseDown()
    pyautogui.moveTo(tx, ty, duration=drag_speed)
    pyautogui.mouseUp()
    time.sleep(wait_time)


def main():
    print("===火影OL拼图自动脚本【增强版】===")
    print("1. 先使用 pick_coordinate_tool() 获取屏幕坐标")
    print("2. full_puzzle.png完整拼图放在脚本同目录")
    print("3. 游戏窗口前置，按回车启动，3秒倒计时")
    print("4. 任意时刻按键盘【ESC】强制停止脚本\n")

    input("按下回车开始倒计时：")
    for i in range(3, 0, -1):
        print(f"{i}...")
        time.sleep(1)

    piece_2dlist, orb_engine = split_full_puzzle(FULL_PUZZLE_PATH)
    total = GRID_ROWS * GRID_COLS
    done_count = 0
    skip_count = 0

    try:
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                if keyboard.is_pressed("esc"):
                    raise KeyboardInterrupt("ESC终止脚本")

                scr = pyautogui.screenshot()
                scr_bgr = cv2.cvtColor(np.array(scr), cv2.COLOR_RGB2BGR)

                # 如果这块已经拼好，直接跳过，不做处理
                if is_piece_already_placed(scr_bgr, r, c):
                    print(f"【{done_count+1}/{total}】row{r},col{c} -> 已拼好，跳过")
                    done_count +=1
                    continue

                info = piece_2dlist[r][c]
                print(f"\n【{done_count+1}/{total}】处理碎片 row:{r}, col:{c}")

                piece_pos = None
                # 重试逻辑
                for retry in range(MAX_RETRY):
                    if keyboard.is_pressed("esc"):
                        raise KeyboardInterrupt()
                    scr = pyautogui.screenshot()
                    scr_bgr = cv2.cvtColor(np.array(scr), cv2.COLOR_RGB2BGR)
                    piece_pos = search_piece_in_pool(scr_bgr, orb_engine, info)
                    if piece_pos is not None:
                        break
                    print(f"  >第{retry+1}次识别失败，重新截图...")
                    time.sleep(0.2)

                if piece_pos is None:
                    print(f"  >>>多次识别失败，跳过该碎片！")
                    skip_count += 1
                    continue

                target_pos = get_canvas_target_pos(r, c)
                drag(piece_pos, target_pos)
                done_count += 1

        print(f"\n✅脚本执行结束！完成:{done_count} 跳过:{skip_count}")

    except KeyboardInterrupt:
        print("\n🛑 用户手动终止脚本")
    except Exception as e:
        print(f"\n⚠️程序异常退出：{e}")


if __name__ == "__main__":
    # 取消注释运行坐标拾取工具
    # pick_coordinate_tool()

    main()
