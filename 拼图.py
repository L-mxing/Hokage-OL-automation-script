# -*- coding: utf-8 -*-
"""火影忍者OL 拼图小游戏（4x4）自动脚本

游戏规则
    点「开始游戏」→ 3 秒倒计时 → 左板被压暗成引导底图，右托盘出现 16 块打乱碎片。
    把碎片拖到左板对应格子。**拖错位置游戏会自动把碎片弹回右侧**，这是一个免费的
    成功/失败信号；90 秒倒计时。

实现思路
    1. 开局前左板显示的是「明亮完整目标图」、右托盘是「空的」，所以开局前先截两张底图：
       puzzle_ref.png（标准答案）与 puzzle_tray_empty.png（托盘背景）。
       之后用「碎片 vs 参考图」做带掩码模板匹配，两者亮度天然一致，
       不需要任何亮度归一化 —— 这是本方案最省力的地方。
    2. 拖错弹回 = 免费的反馈信号。主循环据此做闭环：
       匹配 → 拖动 → 判定 → 失败就给该格加惩罚分，换下一个候选
       （不需要识别"是哪一块弹回来了"，避开了碎片重识别的难题）。
    3. 坐标全自动标定：先定位游戏弹层（黑底上的最大非黑连通域），左右板位置按**比例**推导，
       再用「金色开始按钮必须落在弹层正中」做自校验。分辨率、浏览器缩放变化都能自适应。

用法
    python 拼图.py --calibrate   # 标定（全自动，打印推导出的坐标与自校验结果）
    python 拼图.py --selftest    # 自检：弹层/板/托盘坐标、网格线、单格尺寸、托盘碎片数
    python 拼图.py --dryrun      # 演习：点开始后完整跑「拎起碎片+识别+匹配」，但一律放回托盘、
                                 #       不往目标格落子（用来单独验证识别链路，仍会消耗一次机会）
    python 拼图.py               # 正式运行（停在"开始游戏"界面时启动）
紧急中止：把鼠标快速甩到屏幕左上角（pyautogui FAILSAFE）。
"""
from __future__ import annotations

import os
import sys
import time

import cv2
import mss
import numpy as np
import pyautogui

from screenshot_utils import check_screen_size, click_pos, ensure_utf8_stdout

# ====================== 配置区 ======================
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REF_PATH = os.path.join(BASE_DIR, "puzzle_ref.png")
TRAY_EMPTY_PATH = os.path.join(BASE_DIR, "puzzle_tray_empty.png")

GRID_ROWS = 4
GRID_COLS = 4

# 弹层搜索带：排除浏览器顶栏 / Windows 任务栏
POPUP_BAND_TOP = 85
POPUP_BAND_BOTTOM = 1035

# 板/托盘相对弹层的比例（2026-09-12 在 1920x1080 实拍图上标定）
# 左板推导依据：网格线投影在 rel 132/263/394 三处命中 → 单格 131.5px、板宽 526px（正方形）
BOARD_RATIO = (0.0976, 0.1573, 0.4208, 0.7013)
TRAY_RATIO = (0.6512, 0.1573, 0.2504, 0.7013)
POPUP_ASPECT = 1250 / 750          # 弹层宽高比，用于校验定位是否跑偏

TIME_LIMIT = 90.0                  # 游戏倒计时（秒）
SAFETY_MARGIN = 10.0               # 内部提前收尾余量

TOPHAT_THRESH = 45                 # 碎片浅色描边的顶帽阈值
BLACKHAT_THRESH = 45               # 深色描边的黑帽阈值（碎片描边常是深色外沿 + 浅色内线）
MIN_FRAG_AREA_RATIO = 0.12         # 有效碎片最小面积 / 单格面积
MAX_FRAG_AREA_RATIO = 1.6          # 超过单格 1.6 倍 → 判为背景或粘连，丢弃
MAX_FRAG_BBOX_RATIO = 1.4          # 可见框超过单格 1.4 倍 → 同上

NO_EFFECT_CELL_DIFF = 1.0          # 目标格几乎没变化
NO_EFFECT_TRAY_DELTA = 0.005       # 且托盘占比也几乎没变 → 判定"输入根本没到游戏"
MAX_NO_EFFECT = 2                  # 连续这么多次"没反应"就停（多半是窗口盖住了游戏）
PROBE_MARGIN = 155                 # 拎起来后取景窗口的半边长（仅需容纳一块碎片）
PROBE_DIFF_THRESH = 32             # 探针位"拎起来前后"两帧的差分阈值
PROBE_MIN_AREA = 2500              # 拎起来那块的最小面积，低于此认为没抓稳

MATCH_SCALES = (0.85, 0.925, 1.0, 1.075, 1.15)   # 多尺度模板匹配
MATCH_MIN_SCORE = 0.60             # TM_CCORR_NORMED 最低分

SNAP_BY_PIECE_CENTER = True        # True=按碎片中心吸附（常见）；False=按光标位置吸附
DRAG_STEPS = 18
STABLE_MAX_WAIT = 0.8              # 等吸附动画稳定的上限（秒）

CELL_DIFF_MIN = 12.0               # 目标格"变了"的灰度均差阈值
TRAY_DROP_MIN = 0.30               # 托盘前景占比下降阈值（交叉验证用）

FAIL_CELL_PENALTY = 0.18           # 某格拖失败的惩罚分（叠加，成功后衰减）
MAX_ATTEMPTS = 60                  # 单局最大投放次数
MAX_CONSECUTIVE_FAILURES = 4       # 连续被弹回这么多次就判定为系统性问题，提前停止并给排查建议

pyautogui.FAILSAFE = True          # 甩到屏幕左上角可紧急中止
_sct = mss.MSS()                   # mss 全局实例复用


# ====================== 底层工具 ======================

def grab(region) -> np.ndarray:
    """抓取屏幕区域 (left, top, width, height)，返回 BGR"""
    l, t, w, h = region
    frame = np.asarray(_sct.grab({"left": int(l), "top": int(t),
                                  "width": int(w), "height": int(h)}))
    return frame[:, :, :3].copy()


def grab_full() -> np.ndarray:
    """抓取主显示器全屏，返回 BGR"""
    return np.asarray(_sct.grab(_sct.monitors[1]))[:, :, :3].copy()


def crop(full: np.ndarray, rect) -> np.ndarray:
    """按 (left, top, width, height) 裁剪"""
    l, t, w, h = rect
    return full[int(t):int(t + h), int(l):int(l + w)].copy()


def imwrite_u(path: str, img: np.ndarray) -> str:
    """兼容中文路径的写图（np.tofile + imencode）"""
    ok, buf = cv2.imencode(os.path.splitext(path)[1], img)
    if ok:
        buf.tofile(path)
    return path


def imread_u(path: str) -> np.ndarray | None:
    """兼容中文路径的读图"""
    if not os.path.exists(path):
        return None
    return cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)


# ====================== 标定 ======================

def detect_popup(img: np.ndarray) -> tuple[int, int, int, int] | None:
    """定位游戏弹层：黑底上的最大非黑连通域（跳过浏览器顶栏与任务栏）"""
    h, w = img.shape[:2]
    band = np.zeros((h, w), np.uint8)
    band[POPUP_BAND_TOP:min(POPUP_BAND_BOTTOM, h), :] = 255
    nz = ((img.max(axis=2) > 50).astype(np.uint8) * 255) & band
    nz = cv2.morphologyEx(nz, cv2.MORPH_CLOSE, np.ones((21, 21), np.uint8))
    cnts, _ = cv2.findContours(nz, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    x, y, w_, h_ = cv2.boundingRect(max(cnts, key=cv2.contourArea))
    if w_ < 200 or h_ < 150:
        return None
    return (x, y, w_, h_)


def detect_start_button(img: np.ndarray) -> tuple[int, int] | None:
    """定位金黄色「开始游戏」按钮中心（HSV 分割 + 取最大轮廓）"""
    hsv = cv2.cvtColor(img, cv2.COLOR_BGR2HSV)
    gold = cv2.inRange(hsv, (18, 120, 170), (38, 255, 255))
    keep = np.zeros_like(gold)
    keep[POPUP_BAND_TOP:POPUP_BAND_BOTTOM, :] = 255
    gold &= keep
    gold = cv2.morphologyEx(gold, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    cnts, _ = cv2.findContours(gold, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not cnts:
        return None
    x, y, w, h = cv2.boundingRect(max(cnts, key=cv2.contourArea))
    if w < 60 or h < 25:
        return None
    return (x + w // 2, y + h // 2)


def derive_calib(img: np.ndarray, verbose: bool = True) -> dict | None:
    """从当前屏幕推导全部坐标：弹层 → 比例 → 左右板；并用金色按钮居中做自校验"""
    popup = detect_popup(img)
    if popup is None:
        print("⚠️ 未定位到游戏弹层（黑底上没找到大的非黑区域），请确认拼图窗口已打开")
        return None
    px, py, pw, ph = popup

    ratio_err = abs(pw / ph - POPUP_ASPECT) / POPUP_ASPECT
    if ratio_err > 0.06:
        print(f"⚠️ 弹层宽高比 {pw / ph:.3f} 与标定值 {POPUP_ASPECT:.3f} 偏差 "
              f"{ratio_err * 100:.1f}%，可能截到了别的东西")

    btn = detect_start_button(img)
    if btn:
        rx = (btn[0] - px) / pw
        ry = (btn[1] - py) / ph
        if abs(rx - 0.5) > 0.05 or abs(ry - 0.5) > 0.05:
            print(f"⚠️ 开始按钮相对弹层 ({rx:.3f}, {ry:.3f}) 偏离正中 (>5%)，"
                  f"弹层定位可能不准，请用 --selftest 复核")
        elif verbose:
            print(f"✅ 自校验通过：开始按钮位于弹层 ({rx:.3f}, {ry:.3f})")

    def by_ratio(r):
        return [int(round(px + r[0] * pw)), int(round(py + r[1] * ph)),
                int(round(r[2] * pw)), int(round(r[3] * ph))]

    return {
        "popup": [px, py, pw, ph],
        "board": by_ratio(BOARD_RATIO),
        "tray": by_ratio(TRAY_RATIO),
        "start_btn": list(btn) if btn else [int(px + pw * 0.5), int(py + ph * 0.5)],
        "grid": [GRID_ROWS, GRID_COLS],
        "screen": [_sct.monitors[1]["width"], _sct.monitors[1]["height"]],
    }


def run_calibrate() -> None:
    print("=== 拼图 4x4 自动标定 ===")
    print("请把游戏停在「开始游戏」界面（左板是完整明亮目标图、右板是空的）\n")
    data = derive_calib(grab_full())
    if data is None:
        return

    # 坐标不落盘：每次运行都由 derive_calib() 从当前屏幕实时推导
    print("\n✅ 标定完成（坐标仅本次运行有效，不写文件）")
    print(f"   弹层 {data['popup']}   左板 {data['board']}   托盘 {data['tray']}")
    print(f"   开始按钮 {data['start_btn']}")
    print("   要确认三个框是否贴合内部区域，运行 --selftest")


def load_calib() -> dict:
    """实时推导标定坐标（不缓存、不落盘，屏幕尺寸/浏览器缩放变化都能自适应）"""
    data = derive_calib(grab_full())
    if data is None:
        raise RuntimeError("自动标定失败，请把游戏停在拼图界面后重试")
    return data


def cell_rects(board) -> list[list[tuple[int, int, int, int]]]:
    """把左板切成 GRID_ROWS x GRID_COLS 个格子的屏幕矩形"""
    l, t, w, h = board
    cw, ch = w / GRID_COLS, h / GRID_ROWS
    return [[(int(l + c * cw), int(t + r * ch), int(cw), int(ch))
             for c in range(GRID_COLS)] for r in range(GRID_ROWS)]


def check_grid_lines(gray: np.ndarray) -> list[str]:
    """在**预期位置**附近检查网格线强度，返回诊断文本。

    不用"盲找峰值再数个数"——人物轮廓会产生大量假峰，把 4 列误判成 5~8 列。
    改成：只看 1/4、2/4、3/4 这三个应出现分割线的位置有没有强边缘。
    """
    gh, gw = gray.shape[:2]
    gx = np.abs(cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3)).mean(axis=0)
    gy = np.abs(cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3)).mean(axis=1)

    def check(proj, label, span, n):
        thr = float(proj.mean() + proj.std())
        hits, detail = 0, []
        for k in range(1, n):
            pos = span * k / n
            lo, hi = max(0, int(pos) - 5), min(len(proj), int(pos) + 6)
            seg = proj[lo:hi]
            peak = float(seg.max()) if seg.size else 0.0
            ok = peak >= thr
            hits += int(ok)
            detail.append(f"{label}{k}@{pos:.0f}px={'命中' if ok else '未命中'}"
                          f"({peak:.0f}/{thr:.0f})")
        return hits, detail

    hx, dx = check(gx, "竖", gw, GRID_COLS)
    hy, dy = check(gy, "横", gh, GRID_ROWS)
    return [f"  竖线 {hx}/{GRID_COLS - 1} 命中: " + "  ".join(dx),
            f"  横线 {hy}/{GRID_ROWS - 1} 命中: " + "  ".join(dy)]


# ====================== 碎片检测 ======================

def piece_zone(tray_now: np.ndarray) -> np.ndarray:
    """判定「哪些像素属于碎片」：碎片一定被自己的描边围起来，于是把
    「不是描边的像素」按连通域切开后，碎片就是尺寸≈单格的块，
    背景是横跨整个托盘的大块，会被尺寸上限滤掉。

    **不再依赖「开局前空托盘」做差分** —— 真机 + 离线复算实测那张底图会失效：
    托盘背后是游戏里会变化的场景画，隔一段时间再比，有 **94.8%** 的像素超过
    阈值，整块托盘都会被判成碎片（2026-09-12 实测）。
    """
    gray = cv2.cvtColor(tray_now, cv2.COLOR_BGR2GRAY)
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (9, 9))
    bright = cv2.morphologyEx(gray, cv2.MORPH_TOPHAT, k) > TOPHAT_THRESH
    dark = cv2.morphologyEx(gray, cv2.MORPH_BLACKHAT, k) > BLACKHAT_THRESH
    sep = (bright | dark).astype(np.uint8) * 255
    sep = cv2.dilate(sep, np.ones((3, 3), np.uint8))
    return cv2.bitwise_not(sep)


def detect_fragments(tray_now: np.ndarray, cell_w: float, cell_h: float):
    """找托盘里可抓的碎片，按可见面积降序返回。

    注意：**正式运行的主流程已经不用这个函数了**（真机上碎片互相重叠、
    紫色压紫色，轮廓闭不上，分不出来）。主流程改成先抓起来再识别，
    见 pile_grab_point / pick_up。这里保留实现，供 --selftest 与
    .workbuddy/verify_puzzle.py 做离线分割实验。

    丢弃两类区域：
    - 横跨托盘的大块＝背景场景画；
    - 明显大于单格的块＝多块粘连（离线实测误用粘连块会让首中率从 85% 掉到 50%）。
    小块不丢：碎片被压住时可见部分本来就小，掩码只要"确实属于这一块"就还能匹配，
    真机第 3 次落位成功用的就是一块只露 33% 的碎片（匹配分 0.995）。
    """
    cell_area = cell_w * cell_h
    zone = piece_zone(tray_now)
    n, labels, stats, _ = cv2.connectedComponentsWithStats(zone, 4)
    out = []
    for i in range(1, n):
        x, y, w, h, area = stats[i]
        if area < MIN_FRAG_AREA_RATIO * cell_area or w < 25 or h < 25:
            continue
        if area > MAX_FRAG_AREA_RATIO * cell_area:
            continue
        if w > MAX_FRAG_BBOX_RATIO * cell_w or h > MAX_FRAG_BBOX_RATIO * cell_h:
            continue
        comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255
        dt = cv2.distanceTransform(comp, cv2.DIST_L2, 5)
        _, _, _, pk = cv2.minMaxLoc(dt)
        out.append({
            "bbox": (int(x), int(y), int(w), int(h)),
            "area": int(area),
            "mask": comp,
            "patch": tray_now[y:y + h, x:x + w].copy(),
            "grab": (int(x + pk[0]), int(y + pk[1])),
        })
    out.sort(key=lambda f: -f["area"])
    return out


# ====================== 匹配 ======================

def match_fragment(frag: dict, board_ref: np.ndarray, filled, penalty):
    """碎片 vs 参考板 的多尺度带掩码模板匹配。

    返回 (扣惩罚后的分, row, col, 缩放, 原始分)；不达标返回 None。
    """
    bg = cv2.cvtColor(board_ref, cv2.COLOR_BGR2GRAY)
    fg = cv2.cvtColor(frag["patch"], cv2.COLOR_BGR2GRAY)
    mask = frag["mask"]
    bh, bw = bg.shape[:2]
    cw, ch = bw / GRID_COLS, bh / GRID_ROWS
    best = None

    for s in MATCH_SCALES:
        g = cv2.resize(fg, None, fx=s, fy=s, interpolation=cv2.INTER_AREA)
        m = cv2.resize(mask, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
        gh, gw = g.shape[:2]
        if gh >= bh or gw >= bw:
            continue
        try:
            res = cv2.matchTemplate(bg, g, cv2.TM_CCORR_NORMED, mask=m)
        except cv2.error:
            continue
        res = np.nan_to_num(res, nan=-1.0, posinf=-1.0, neginf=-1.0)

        # 逐格取峰值：碎片中心落在某格时，模板左上角必然落在
        # [格左上 - 模板/2, 格右下 - 模板/2] 这个区间里
        for r in range(GRID_ROWS):
            for c in range(GRID_COLS):
                if filled[r][c]:
                    continue
                x0 = max(0, int(round(c * cw - gw / 2.0)))
                y0 = max(0, int(round(r * ch - gh / 2.0)))
                x1 = min(res.shape[1], int(round((c + 1) * cw - gw / 2.0)))
                y1 = min(res.shape[0], int(round((r + 1) * ch - gh / 2.0)))
                if x1 <= x0 or y1 <= y0:
                    continue
                sub = res[y0:y1, x0:x1]
                raw = float(sub.max()) if sub.size else -1.0
                sc = raw - penalty[r][c]
                if best is None or sc > best[0]:
                    best = (sc, r, c, s, raw)

    if best and best[0] >= MATCH_MIN_SCORE:
        return best
    return None


# ====================== 抓取 / 拖动 ======================

def _smooth_move(src, dst, steps=DRAG_STEPS) -> None:
    """缓动分段移动：H5 游戏监听的是 mousemove 序列，瞬移不会触发拖动"""
    for i in range(1, steps + 1):
        t = i / steps
        e = t * t * (3 - 2 * t)          # smoothstep
        x = src[0] + (dst[0] - src[0]) * e + (0.8 if i % 2 else -0.8)
        y = src[1] + (dst[1] - src[1]) * e
        pyautogui.moveTo(x, y)
        time.sleep(0.012)


def pile_grab_point(tray_now: np.ndarray) -> tuple[int, int] | None:
    """在托盘里找一个「一定落在某块碎片内部」的抓点。

    用颜色取碎片整体轮廓：碎片是饱和紫或深色（人物黑影），背景是低饱和浅色
    场景画 —— 这个判据**不受背景变化影响**，比"和开局前底图做差分"稳得多
    （那张底图真机实测会失效，94.8% 像素超过阈值）。
    再取距离变换峰值：离所有边界最远的点，必然落在最上层那块碎片内部。

    注意这一步**只求抓得住，不求知道是哪一块** —— 是哪一块等拎起来再识别。
    """
    hsv = cv2.cvtColor(tray_now, cv2.COLOR_BGR2HSV)
    fg = cv2.bitwise_or(cv2.inRange(hsv, (110, 60, 40), (175, 255, 255)),
                        (hsv[:, :, 2] < 70).astype(np.uint8) * 255)
    fg = cv2.morphologyEx(fg, cv2.MORPH_CLOSE, np.ones((7, 7), np.uint8))
    n, labels, stats, _ = cv2.connectedComponentsWithStats(fg, 8)
    if n <= 1:
        return None
    i = max(range(1, n), key=lambda k: stats[k][4])
    x, y, w, h, area = stats[i]
    if area < 4000:
        return None
    comp = (labels[y:y + h, x:x + w] == i).astype(np.uint8) * 255
    dt = cv2.distanceTransform(comp, cv2.DIST_L2, 5)
    _, _, _, pk = cv2.minMaxLoc(dt)
    return (int(x + pk[0]), int(y + pk[1]))


def pile_ratio(tray_now: np.ndarray) -> float:
    """托盘里「碎片色」像素占比，用来交叉验证碎片有没有真的被拿走"""
    hsv = cv2.cvtColor(tray_now, cv2.COLOR_BGR2HSV)
    fg = cv2.bitwise_or(cv2.inRange(hsv, (110, 60, 40), (175, 255, 255)),
                        (hsv[:, :, 2] < 70).astype(np.uint8) * 255)
    return float(fg.mean() / 255.0)


def _probe_rect(center, margin=PROBE_MARGIN):
    return (int(center[0] - margin), int(center[1] - margin), int(margin * 2), int(margin * 2))


def pick_up(grab_pt, probe_pt):
    """按住托盘里的一点，把碎片拎到探针位（左板中央的空白处），并识别出是哪一块。

    拎起来前后在探针位各拍一帧做差分，取**包含光标**的那个连通域 ——
    光标必然在碎片内部（抓的就是它内部的一点），所以那块就是被拎起的碎片。
    再与颜色掩码取交集，排除"悬停高亮"这类跟着鼠标变色的干扰。

    返回 None 表示没分离出轮廓（调用方负责松手）。
    """
    rect = _probe_rect(probe_pt)
    before = grab(rect)
    pyautogui.moveTo(*grab_pt, duration=0.12)
    pyautogui.mouseDown()
    time.sleep(0.04)
    _smooth_move(grab_pt, probe_pt)
    time.sleep(0.20)
    cursor = pyautogui.position()
    after = grab(rect)

    d = cv2.absdiff(after, before).max(axis=2)
    mask = (d > PROBE_DIFF_THRESH).astype(np.uint8) * 255
    mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, np.ones((9, 9), np.uint8))
    mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, np.ones((5, 5), np.uint8))
    hsv = cv2.cvtColor(after, cv2.COLOR_BGR2HSV)
    color = cv2.bitwise_or(cv2.inRange(hsv, (110, 60, 40), (175, 255, 255)),
                           (hsv[:, :, 2] < 70).astype(np.uint8) * 255)
    mask = cv2.bitwise_and(mask, color)

    n, labels, stats, _ = cv2.connectedComponentsWithStats(mask, 8)
    if n <= 1:
        return None
    px, py = int(cursor[0] - rect[0]), int(cursor[1] - rect[1])
    idx = 0
    if 0 <= px < labels.shape[1] and 0 <= py < labels.shape[0]:
        idx = int(labels[py, px])
    if idx == 0:
        idx = max(range(1, n), key=lambda k: stats[k][4])
    x, y, w, h, area = stats[idx]
    if area < PROBE_MIN_AREA:
        return None
    comp = (labels[y:y + h, x:x + w] == idx).astype(np.uint8) * 255
    return {
        "bbox": (int(x), int(y), int(w), int(h)),
        "area": int(area),
        "mask": comp,
        "patch": after[y:y + h, x:x + w].copy(),
        "center": (rect[0] + x + w / 2.0, rect[1] + y + h / 2.0),
        "cursor": cursor,
    }


def drop_at(held, target_pt):
    """把碎片中心对到 target_pt 后松手。

    碎片跟随光标是带固定偏移的，这里用"碎片中心 - 光标"实测出偏移再补偿，
    所以不需要事先知道抓的是哪一块的哪个位置。
    """
    vx = held["center"][0] - held["cursor"][0]
    vy = held["center"][1] - held["cursor"][1]
    final = (target_pt[0] - vx, target_pt[1] - vy)
    pyautogui.moveTo(*final, duration=0.14)
    time.sleep(0.12)                     # 松手前停顿，等吸附提示
    pyautogui.mouseUp()
    return final


# ====================== 判定 ======================

def wait_stable(region, max_wait=STABLE_MAX_WAIT) -> np.ndarray:
    """等区域画面稳定（连续两帧差异很小），返回稳定后的画面"""
    prev = grab(region)
    deadline = time.time() + max_wait
    while time.time() < deadline:
        time.sleep(0.1)
        cur = grab(region)
        if cv2.absdiff(cur, prev).mean() < 3.0:
            return cur
        prev = cur
    return prev


def wait_for_puzzle(tray, timeout=15.0) -> bool:
    """等倒计时结束进入拼图。

    判据用**碎片色占比**（碎片是饱和紫/深色，背景是低饱和浅色场景画），
    不用旧的描边分割 detect_fragments —— 那个真机上恒返回 0，会让这里白等满 15s。
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        if pile_ratio(grab(tray)) > 0.25:
            return True
        time.sleep(0.15)
    return False


# ====================== 主流程 ======================

def _prepare_reference(calib, cell_w, cell_h) -> tuple[np.ndarray, bool]:
    """采集/载入「开局前左板答案图」，返回 (参考图, 是否需要点开始)。

    只在「托盘里没有碎片」（还没开局）时才允许重新采集。开局后左板会被压暗，
    若此时把压暗图当参考存下来，后面所有匹配都会废掉 —— 这个坑必须靠
    「托盘有没有碎片」来区分，不能只比左板差异。
    """
    board, tray = calib["board"], calib["tray"]
    full = grab_full()
    board_now, tray_now = crop(full, board), crop(full, tray)

    ref = imread_u(REF_PATH)
    # 判据用碎片色占比，不用描边分割（真机恒返回 0）
    started = pile_ratio(tray_now) > 0.25

    if started:
        if ref is not None and ref.shape == board_now.shape:
            print("ℹ️ 托盘里已有碎片 → 游戏进行中，沿用缓存的参考图")
            return ref, False
        raise RuntimeError("游戏已在进行中，但缺少开局前采的 puzzle_ref.png，"
                           "无法匹配；请等这一局结束后重新运行")

    if ref is not None and ref.shape == board_now.shape:
        same = float(cv2.absdiff(board_now, ref).mean())
        if same < 12.0:
            print(f"ℹ️ 沿用缓存的参考图（与当前左板差异 {same:.1f}）")
        else:
            print(f"→ 左板与缓存参考差异 {same:.1f}，判定为新的一局，重新采集参考图")
            imwrite_u(REF_PATH, board_now)
            ref = board_now
    else:
        imwrite_u(REF_PATH, board_now)
        print(f"✅ 已保存参考图 {os.path.basename(REF_PATH)}")
        ref = board_now

    imwrite_u(TRAY_EMPTY_PATH, tray_now)      # 只给离线验证脚本用，主流程不再依赖
    return ref, True


def run_main(dry_run: bool = False) -> None:
    calib = load_calib()
    board, tray = calib["board"], calib["tray"]
    cells = cell_rects(board)
    cell_w, cell_h = board[2] / GRID_COLS, board[3] / GRID_ROWS
    print(f"左板 {board}  托盘 {tray}  单格 {cell_w:.0f}x{cell_h:.0f}px")
    # 探针位：左板 (2,2) 格中心 —— 完全被左板包住、离托盘足够远
    probe_pt = (cells[2][2][0] + cell_w / 2.0, cells[2][2][1] + cell_h / 2.0)

    ref, need_start = _prepare_reference(calib, cell_w, cell_h)

    if need_start:
        bx, by = calib["start_btn"]
        print(f"→ 点击「开始游戏」 ({bx}, {by})")
        click_pos(bx, by, duration=0.15)
        print("→ 等待倒计时结束...")
        if not wait_for_puzzle(tray):
            print("⚠️ 15s 内托盘未出现碎片，继续尝试（倒计时可能更长）")
        time.sleep(0.6)
    else:
        print("ℹ️ 游戏已在进行中，直接接管")

    t0 = time.time()
    deadline = t0 + TIME_LIMIT - SAFETY_MARGIN
    filled = [[False] * GRID_COLS for _ in range(GRID_ROWS)]
    penalty = [[0.0] * GRID_COLS for _ in range(GRID_ROWS)]
    done = 0
    attempt = 0
    consec_fail = 0

    while done < GRID_ROWS * GRID_COLS and attempt < MAX_ATTEMPTS:
        if time.time() > deadline:
            print(f"⏰ 到内部截止时间，停止投放（已拼 {done}/{GRID_ROWS * GRID_COLS}）")
            break
        attempt += 1

        tray_now = grab(tray)
        grab_pt = pile_grab_point(tray_now)
        if grab_pt is None:
            print("⚠️ 托盘里找不到碎片（可能已完成或界面已变化）")
            break

        held = pick_up(grab_pt, probe_pt)
        if held is None:
            pyautogui.mouseUp()
            print(f"[{attempt}] 拎起来后没能分离出碎片轮廓，跳过")
            penalty = [[p * 0.7 for p in row] for row in penalty]
            time.sleep(0.2)
            continue

        m = match_fragment(held, ref, filled, penalty)
        if m is None:
            drop_at(held, probe_pt)      # 放回探针位；错格会被游戏弹回托盘
            print(f"[{attempt}] 拎起的碎片 {held['bbox'][2]}x{held['bbox'][3]} "
                  f"({held['area']}px) 匹配分不足，已放回")
            penalty = [[p * 0.7 for p in row] for row in penalty]
            time.sleep(0.25)
            continue

        score, row, col, scale, raw = m
        cx = cells[row][col][0] + cell_w / 2.0
        cy = cells[row][col][1] + cell_h / 2.0
        print(f"[{attempt}] 碎片 {held['bbox'][2]}x{held['bbox'][3]} ({held['area']}px) "
              f"→ ({row},{col}) 分{raw:.3f} 缩放{scale} "
              f"剩余{deadline - time.time():.1f}s")

        if dry_run:
            drop_at(held, probe_pt)
            time.sleep(0.5)
            continue

        before = grab(cells[row][col])
        ratio_before = pile_ratio(tray_now)
        drop_at(held, (cx, cy))
        after = wait_stable(cells[row][col])

        cell_diff = float(cv2.absdiff(after, before).mean())
        ratio_after = pile_ratio(grab(tray))
        drop = ratio_before - ratio_after

        if cell_diff > CELL_DIFF_MIN:
            filled[row][col] = True
            done += 1
            consec_fail = 0
            penalty = [[p * 0.6 for p in r] for r in penalty]
            print(f"   ✅ 落位成功 ({row},{col})  格变化{cell_diff:.1f} 托盘-{drop:.3f}  "
                  f"进度 {done}/{GRID_ROWS * GRID_COLS}")
        else:
            consec_fail += 1
            penalty[row][col] = min(1.0, penalty[row][col] + FAIL_CELL_PENALTY)
            print(f"   ❌ 被弹回（格变化{cell_diff:.1f}，托盘-{drop:.3f}），"
                  f"({row},{col}) 惩罚升至 {penalty[row][col]:.2f}，换候选")
            if drop > TRAY_DROP_MIN:
                print("   ⚠️ 托盘面积明显下降但目标格未变，判据冲突")
            if consec_fail >= MAX_CONSECUTIVE_FAILURES:
                print(f"\n🛑 连续 {consec_fail} 次被弹回，属于系统性问题而不是运气差，提前停止。")
                print("   最可能的原因（按概率排序）：")
                print("   1) 吸附参考点不对：把 SNAP_BY_PIECE_CENTER 改成 False 再试")
                print("   2) 拖动偏移校正没生效：看日志里有没有「未能重新定位碎片」")
                print("   3) 坐标标定偏了：重跑 --calibrate 复核坐标")
                return

    print(f"\n===== 结束：拼对 {done}/{GRID_ROWS * GRID_COLS}，投放 {attempt} 次，"
          f"用时 {time.time() - t0:.1f}s =====")


# ====================== 自检 ======================

def run_selftest() -> None:
    """抓图 + 校验标定 + 检测网格线与托盘碎片，只在控制台输出结论（不落盘任何图片）"""
    img = grab_full()
    data = derive_calib(img)
    if data is None:
        return

    board, tray = data["board"], data["tray"]
    b_img, t_img = crop(img, board), crop(img, tray)
    print(f"左板 {board}  尺寸 {b_img.shape[1]}x{b_img.shape[0]}")
    print(f"托盘 {tray}  尺寸 {t_img.shape[1]}x{t_img.shape[0]}")

    cw, ch = board[2] / GRID_COLS, board[3] / GRID_ROWS
    print(f"单格 {cw:.1f}x{ch:.1f}px（面积 {cw * ch:.0f}）")

    grid_lines = check_grid_lines(cv2.cvtColor(b_img, cv2.COLOR_BGR2GRAY))
    print("网格线校验（仅在未开始状态下可见）:")
    for line in grid_lines:          # 注意：check_grid_lines 返回的是"行列表"，
        print(line)                  # 不要解包成两个变量，否则 cols+rows 会变成字符串拼接
    if any("未命中" in s for s in grid_lines):
        print("  ℹ️ 有未命中：若此时游戏已开始（左板被压暗），网格线会消失，属正常现象")

    gp = pile_grab_point(t_img)
    print(f"托盘碎片色占比 {pile_ratio(t_img):.3f}  抓点 {gp}")
    if gp is None:
        print("  ⚠️ 没抓到碎片色区域：确认游戏已进入拼图、托盘里确实有碎片")


# ====================== 入口 ======================

if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    try:
        if "--calibrate" in sys.argv:
            run_calibrate()
        elif "--selftest" in sys.argv:
            run_selftest()
        elif "--dryrun" in sys.argv:
            run_main(dry_run=True)
        else:
            run_main()
    except KeyboardInterrupt:
        print("\n🛑 已手动中止")
    except Exception as e:
        print(f"\n⚠️ 异常退出：{type(e).__name__}: {e}")
        raise
