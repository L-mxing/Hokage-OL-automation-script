# -*- coding: utf-8 -*-
"""
通缉任务自动脚本 v1 (2026-09-23)
=========================================================================
把右侧任务列表里的「[通缉]通缉任务　自动」点掉并确认弹框，直到该条目消失。

流程（2026-09-23 用户澄清游戏机制后定稿）:
    1) 在右侧「任务列表」面板里识别文字「通缉任务」
       —— 实测 OCR 会把它读成「通缉通缉任务 自动」（方括号丢失、与「自动」连成一框）
    2) 在同一行右侧定位绿色「自动」按钮 → 点击它
    3) 弹出「你现在为杀人状态……是否继续操作?」→ 点击橙色「是」
    4) **只点这一次**。点完「是」之后游戏会自己把通缉任务全部做完，脚本不需要再点、
       也不需要反复触发；它只剩一件事：盯着任务列表，等「通缉任务」相关文字与「自动」
       按钮**全部消失**，然后退出。
       （用户确认：通缉任务全部完成后，相关文字会全部消失）

    ⚠️ 这里踩过一次坑：初版按"点一次 → 等「自动」再出现 → 再点"的多轮循环写，
       与游戏实际机制不符（点一次就会全部做完），结果是把"还没消失"的同一批条目
       反复当成"又有新的可领"去点击。现在改成「单次点击 + 等待全部消失」。

坐标与阈值全部来自 2026-09-23 真机帧实测
（screenshots/20260923_214545.png 有通缉任务 / 20260923_214610.png 弹出了确认框）:
    · 「通缉通缉任务 自动」OCR 中心 y=578，x 范围 1730~1849
    · 「自动」灰度模板 52x30 匹配 1.000 @ 屏幕 (1835,576)
    · 「是」模板 124x48 匹配 1.000 @ 屏幕 (876,587)；
      对照帧（未弹框）同一区域最高只有 0.32~0.38 → 0.8 阈值余量充足
    · 模板里亮绿像素 BGR≈(45,255,123)、HSV=(49,210,255)，是纯色渲染
    · ⚠️ **「自动」的 x 会随任务名长短移动**：任务名为「通缉任务」时中心 x=1835，
      任务名为「击败佩恩·天道」时 x=1863（同日真机自检实测，差 28px）
      ⇒ 绝不能硬编码坐标，必须靠模板定位。任务名一变灰度判据就从 1.000 掉到
      **0.809**（只比阈值 0.80 高 0.009，几乎贴线），那次是绿掩码判据（仍 1.000）
      兜住的 —— 两判据互补不是纸面推演，是被这条真机数据救回来的。
    · 通缉任务的标题形如「[通缉]XXXX」，XXXX 每次可能不同（实测见过「通缉任务」
      「击败佩恩·天道」），所以文字判定只认"通"+"缉/辑"，不比对完整标题。

为什么「自动」要两条判据取最大值（灰度 + 绿掩码）—— 合成帧实测：
    | 场景                   | 灰度模板 | 绿掩码 |
    | 正常（背景一致）        |  1.000  | 1.000 |
    | 确认框把屏幕压暗        |  1.000  | 0.000 |
    | 「自动」落在别的背景上  |  0.352  | 1.000 |
    灰度模板（TM_CCOEFF_NORMED 零均值归一化）对整体亮度缩放不敏感，但要求背景一致；
    绿掩码只认绿色笔画的形状、完全无视背景，但屏幕被压暗到绿阈值以下就全丢。
    两者互补 → 取最大分，任一达标即认为「自动」在。
    误检余量：绿掩码在面板内的次高分只有 0.433（另一处绿色任务文字），与主峰差 0.567。

命令行:
    python 通缉任务.py           # 全流程（点一次「自动」+「是」，等全部完成后退出）
    python 通缉任务.py --check   # 只读自检：打印各项分数/OCR 文字/面板可见性，不点击

⚠️ 运行前：游戏窗口必须在最前，且右侧「任务列表」要能看见 ——
   被别的窗口压住时模板分数会掉，脚本会当成「没有通缉任务」。

依赖: opencv-python / numpy / mss / pyautogui / rapidocr_onnxruntime（见 requirements.txt）
"""
import ctypes
import os
import sys
import time
from difflib import SequenceMatcher

import cv2
import mss
import numpy as np
from rapidocr_onnxruntime import RapidOCR

from screenshot_utils import click_pos, ensure_utf8_stdout, check_screen_size

# ================= 素材 =================
IMG_AUTO = "image/TJ/TJ_01_auto.png"     # 条目里的绿色「自动」
IMG_YES = "image/TJ/TJ_02_yes.png"       # 确认框里的橙色「是」

# ================= 搜索区域（1920x1080 实测）=================
TASK_LIST_REGION = (1640, 380, 280, 560)   # 右侧任务列表面板（「任务列表」标题 + 其下条目）
CONFIRM_REGION = (760, 480, 400, 240)      # 屏幕中央的确认框（模板 124x48 放得下）

# ================= 识别参数 =================
AUTO_CONFIDENCE = 0.80        # 「自动」命中阈值（实测正常态 1.000，余量 0.20）
YES_CONFIDENCE = 0.80         # 「是」命中阈值（实测 1.000；没弹框时最高只有 0.38）
AUTO_SURE = 0.95              # 高到这个程度说明模板与画面几乎一致，无需再靠 OCR 交叉确认
ROW_Y_TOLERANCE = 16          # 「自动」与「通缉」文字算作"同一行"的 y 容差
                              # （实测相邻两行文字 y 只差 19，所以这里必须小于 19）
TITLE_TEXT = "任务列表"        # 面板标题，用来确认"面板还在、只是没有通缉任务了"
TITLE_SIM = 0.6               # 标题相似度下限（实测 OCR 稳定读到 0.71~0.75）
OCR_SCALE = 1.3               # OCR 放大倍数：280x560 放大到长边 ≈728，正好不触发引擎内部
                              # det_limit_side_len(736) 的二次缩放 —— 零浪费的最大清晰度
OCR_TEXT_SCORE = 0.25         # 引擎内部置信度门槛（默认 0.5 会丢掉低分短文本）

# ================= 节奏参数 =================
PROBE_RETRY = 3               # 开局探测"有没有通缉任务"的次数（抗 OCR 单次抖动）
PROBE_RETRY_INTERVAL = 2.0    # 开局探测的间隔（秒）
POLL_INTERVAL = 3.0           # 等完成期间的轮询间隔（每次含一次 OCR，不必更密）
DONE_CONFIRM_SEC = 6.0        # 相关文字消失后，还要连续观察这么久才判定"真的完成了"
                              # （抗 OCR 抖动：单次没读到 ≠ 已经消失）
MAX_WAIT = 1800.0             # 等通缉任务全部完成的上限（秒），超过就退出并提示
CONFIRM_APPEAR_DELAY = 0.35   # 点「自动」后等弹框动画
CONFIRM_PROBE_TIMEOUT = 2.0   # 等「是」按钮出现的窗口（没出现说明这次没弹框）
CONFIRM_GONE_TIMEOUT = 3.0    # 点「是」后等确认框消失
CLICK_SETTLE_DELAY = 0.5      # 点击后等界面响应

# ---- 窗口 ----
GAME_WINDOW_TITLE = "火影忍者ol"

# ================= 内部状态 =================
_sct = mss.MSS()               # 复用实例，避免反复初始化
_ocr_engine = None
_cache: dict = {}              # {(类型, 路径): (图像, mtime)}


# ================= OCR 引擎 =================

def _create_ocr_engine(**extra) -> RapidOCR:
    """创建 OCR 引擎。

    关键参数 det_limit_type: RapidOCR 默认 "min"，含义是"把短边放大到 736"。
    面板区域是 280x560 这种竖长条，短边 280 会被放大 2.6 倍，实际送进检测模型的是
    1920x736(141 万像素) —— 白白多算。截图场景改用 "max"（长边不超过 736）后，
    同一块内容按原始清晰度送进去即可。

    ⚠️ rapidocr_onnxruntime 1.2.3 的 update_det_params 会无条件读
    det_dict['model_path']，只传 det_limit_* 会 KeyError，必须补 det_model_path=""。
    """
    options = {"det_limit_type": "max", "det_limit_side_len": 736,
               "text_score": OCR_TEXT_SCORE}
    options.update(extra)
    try:
        return RapidOCR(**options)
    except KeyError:
        options["det_model_path"] = ""
        return RapidOCR(**options)


def get_ocr_engine() -> RapidOCR:
    """惰性初始化 OCR 引擎（单例）"""
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = _create_ocr_engine()
    return _ocr_engine


# ================= 截屏 / OCR / 匹配 =================

def grab(region):
    """截取屏幕区域，返回 (BGR 帧, 左上角 x, 左上角 y)"""
    left, top, width, height = region
    raw = _sct.grab({"left": left, "top": top, "width": width, "height": height})
    return np.array(raw)[:, :, :3].copy(), left, top


def ocr_items(img, scale=1.0):
    """OCR 一帧，返回 [(cx, cy, 文字, 置信度)]，坐标已还原到原帧尺度。

    ⚠️ 不向 engine(...) 传任何 kwargs —— rapidocr 1.2.3 里传 kwargs 会把引擎参数
    重置回默认值（text_score 又变回 0.5），这是踩过的坑。
    """
    if scale != 1.0:
        h, w = img.shape[:2]
        img = cv2.resize(img, (int(w * scale), int(h * scale)),
                         interpolation=cv2.INTER_CUBIC)
    result, _ = get_ocr_engine()(img)
    items = []
    if not result:
        return items
    for box, text, score in result:
        pts = np.asarray(box, dtype=float)
        items.append((float(pts[:, 0].mean()) / scale, float(pts[:, 1].mean()) / scale,
                      str(text).strip(), float(score)))
    return items


def text_similarity(a, b):
    """两个字符串的相似度（0~1），容忍 OCR 错字"""
    return SequenceMatcher(None, a, b).ratio()


def _load_gray(path):
    """灰度模板（带 mtime 缓存：开发期换掉 PNG 会自动重载）"""
    key = ("gray", path)
    mtime = os.path.getmtime(path)
    hit = _cache.get(key)
    if hit is None or hit[1] != mtime:
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_GRAYSCALE)
        if img is None:
            raise FileNotFoundError(f"模板图片加载失败: {path}")
        _cache[key] = (img, mtime)
    return _cache[key][0]


def green_mask(bgr):
    """亮绿色像素掩码（「自动」二字的笔画）。

    阈值来自实测：笔画 BGR≈(45,255,123)，背景 BGR≈(61,66,60)。
    只留"够亮 + 绿明显多过红和蓝"的像素。
    """
    b = bgr[:, :, 0].astype(int)
    g = bgr[:, :, 1].astype(int)
    r = bgr[:, :, 2].astype(int)
    return np.where((g > 120) & (g - r > 50) & (g - b > 50), 255, 0).astype(np.uint8)


def _load_mask(path):
    """「自动」模板的绿色笔画掩码（带 mtime 缓存）"""
    key = ("mask", path)
    mtime = os.path.getmtime(path)
    hit = _cache.get(key)
    if hit is None or hit[1] != mtime:
        bgr = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if bgr is None:
            raise FileNotFoundError(f"模板图片加载失败: {path}")
        _cache[key] = (green_mask(bgr), mtime)
    return _cache[key][0]


def _peak(result):
    """取匹配结果里的最高分与位置"""
    _, score, _, loc = cv2.minMaxLoc(result)
    return float(score), loc


def match_auto(frame):
    """在任务列表区域的帧里找绿色「自动」。

    两条判据取最高分（互补关系见模块开头 docstring 的实测表）。

    :return: {"center": 区域内中心坐标, "score": 命中分, "method": 判据名,
              "gray": 灰度分, "mask": 绿掩码分}；模板放不进区域时返回 None
    """
    gray_tpl = _load_gray(IMG_AUTO)
    th, tw = gray_tpl.shape[:2]
    fh, fw = frame.shape[:2]
    if th > fh or tw > fw:
        return None

    s_gray, loc_gray = _peak(cv2.matchTemplate(
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), gray_tpl, cv2.TM_CCOEFF_NORMED))
    s_mask, loc_mask = _peak(cv2.matchTemplate(
        green_mask(frame), _load_mask(IMG_AUTO), cv2.TM_CCOEFF_NORMED))

    if s_mask > s_gray:
        score, loc, method = s_mask, loc_mask, "绿掩码"
    else:
        score, loc, method = s_gray, loc_gray, "灰度"
    return {"center": (loc[0] + tw // 2, loc[1] + th // 2),
            "score": score, "method": method, "gray": s_gray, "mask": s_mask}


def _is_wanted_text(text):
    """是不是「通缉任务」那一行。

    OCR 常把「缉」读成「辑」（实测两张真机帧都是「通辑」），方括号也会丢，
    所以只要求"含'通' + 含'缉'/'辑'"。
    """
    return "通" in text and ("缉" in text or "辑" in text)


def inspect(full_frame=None):
    """看一眼任务列表面板，返回判定所需的全部证据（一次截图 + 一次 OCR + 两次匹配）。

    :param full_frame: 离线回归用 —— 传入整屏帧替代实时截图（坐标系与真机一致）
    :return: {"auto": match_auto 的结果(坐标已转屏幕坐标系) 或 None,
              "rows": 含「通缉」的 OCR 行 [{"y","text","score"}]（屏幕坐标）,
              "title": 是否看到「任务列表」标题,
              "texts": 面板内 OCR 原文（诊断用）}
    """
    x, y, w, h = TASK_LIST_REGION
    if full_frame is None:
        frame, off_x, off_y = grab(TASK_LIST_REGION)
    else:
        frame = full_frame[y:y + h, x:x + w]
        off_x, off_y = x, y

    hit = match_auto(frame)
    if hit:
        cx, cy = hit["center"]
        hit["center"] = (cx + off_x, cy + off_y)

    rows, texts, title = [], [], False
    for cx, cy, text, score in ocr_items(frame, OCR_SCALE):
        texts.append(f"「{text}」{score:.2f}@({cx + off_x:.0f},{cy + off_y:.0f})")
        if _is_wanted_text(text):
            rows.append({"y": cy + off_y, "text": text, "score": score})
        if text_similarity(text, TITLE_TEXT) >= TITLE_SIM:
            title = True
    return {"auto": hit, "rows": rows, "title": title, "texts": texts}


def decide_auto(hit, rows):
    """按证据决定这次要不要点「自动」。

    两条放行路径：
      a) 匹配分高到 0.95 以上 —— 说明模板与画面几乎逐像素一致，就是那个按钮，直接放行；
      b) 分数过阈值 0.80，且 OCR 读到「通缉」文字与它同一行 —— 用户要求的那条路径。

    :return: (能否点击, 说明文字)
    """
    if not hit or hit["score"] < AUTO_CONFIDENCE:
        return False, ""
    cy = hit["center"][1]
    same_row = next((r for r in rows if abs(r["y"] - cy) <= ROW_Y_TOLERANCE), None)

    if hit["score"] >= AUTO_SURE:
        note = f"{hit['method']}满分命中"
        note += (f"，同行文字「{same_row['text']}」" if same_row
                 else "，OCR 未读到同行「通缉」文字（按高分放行）")
        return True, note
    if same_row:
        return True, f"{hit['method']} {hit['score']:.2f} + 同行文字「{same_row['text']}」"
    return False, ""


def locate_yes(full_frame=None):
    """在屏幕中部找确认框的橙色「是」，返回 (cx, cy, score) 或 None"""
    x, y, w, h = CONFIRM_REGION
    if full_frame is None:
        frame, off_x, off_y = grab(CONFIRM_REGION)
    else:
        frame = full_frame[y:y + h, x:x + w]
        off_x, off_y = x, y
    tpl = _load_gray(IMG_YES)
    th, tw = tpl.shape[:2]
    if th > frame.shape[0] or tw > frame.shape[1]:
        return None
    score, loc = _peak(cv2.matchTemplate(
        cv2.cvtColor(frame, cv2.COLOR_BGR2GRAY), tpl, cv2.TM_CCOEFF_NORMED))
    if score < YES_CONFIDENCE:
        return None
    return (loc[0] + tw // 2 + off_x, loc[1] + th // 2 + off_y, score)


# ================= 窗口激活 =================

def _find_window_hwnd(title_fragment):
    """按标题子串枚举可见窗口，返回 hwnd（找不到返回 0）"""
    user32 = ctypes.windll.user32
    found = []

    def _cb(hwnd, _lp):
        if user32.IsWindowVisible(hwnd):
            n = user32.GetWindowTextLengthW(hwnd)
            if n:
                buf = ctypes.create_unicode_buffer(n + 1)
                user32.GetWindowTextW(hwnd, buf, n + 1)
                if title_fragment in buf.value:
                    found.append(hwnd)
        return True

    EnumProc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
    user32.EnumWindows(EnumProc(_cb), 0)
    return found[0] if found else 0


def activate_game_window(title=GAME_WINDOW_TITLE):
    """把游戏窗口置前（被遮挡会让截图分数掉、点击也会落到别的窗口上）"""
    try:
        user32 = ctypes.windll.user32
        hwnd = _find_window_hwnd(title)
        if not hwnd:
            return False
        if user32.IsIconic(hwnd):
            user32.ShowWindow(hwnd, 9)
        user32.SetForegroundWindow(hwnd)
        time.sleep(0.2)
        return True
    except Exception:
        return False


# ================= 流程 =================

def handle_confirm(full_frame=None):
    """点完「自动」后处理确认框：弹了就点「是」，没弹就跳过。

    没弹框是正常情况 —— 只有"杀人状态"下才提示双倍经验的事，普通状态点了就直接开始。

    :return: True 表示确实点了「是」
    """
    if full_frame is None:
        time.sleep(CONFIRM_APPEAR_DELAY)      # 弹框有动画，别一帧内就问
    deadline = time.time() + CONFIRM_PROBE_TIMEOUT
    while True:
        loc = locate_yes(full_frame)
        if loc:
            click_pos(loc[0], loc[1], duration=0.05)
            print(f"   ✅ 点击确认框「是」({loc[0]},{loc[1]}) 匹配 {loc[2]:.3f}")
            return True
        if full_frame is not None or time.time() >= deadline:
            print("   · 没出现确认框（当前可能不是杀人状态）→ 跳过")
            return False
        time.sleep(0.25)


def wait_confirm_gone(timeout=CONFIRM_GONE_TIMEOUT):
    """等确认框消失（以「是」按钮不再出现为准），免得下一轮探测还在弹框状态里打转"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if locate_yes() is None:
            return True
        time.sleep(0.25)
    print(f"   ⚠️ {timeout:.0f}s 内确认框还在，继续往下走（下一轮会重新判）")
    return False


def wait_all_gone(timeout=MAX_WAIT):
    """轮询等待通缉任务全部完成：「通缉任务」相关文字与「自动」按钮**全部消失**。

    用户确认的游戏机制：点一次「自动」+「是」之后游戏会自己把通缉任务做完，
    做完后相关文字全部消失 —— 脚本不需要再点任何东西，只需等这一幕出现。

    两条防误判：
      · 「消失」要**连续保持** DONE_CONFIRM_SEC 秒才作数（OCR 单次没读到 ≠ 已经消失）；
      · 要求「任务列表」标题仍读得到（窗口真的在、没被压住），否则一律不计入，
        免得"窗口被遮住"被当成"任务做完了"。

    :return: True 表示确认已全部完成；False 表示等到超时
    """
    started = time.time()
    deadline = started + timeout
    gone_since = None
    last_report = started

    while time.time() < deadline:
        spec = inspect()
        has_auto = bool(spec["auto"]) and spec["auto"]["score"] >= AUTO_CONFIDENCE
        has_text = len(spec["rows"]) > 0

        if has_auto or has_text:
            if gone_since is not None:
                print("   · 又看到通缉任务相关文字 → 完成计时重置")
            gone_since = None
            if time.time() - last_report >= 30:   # 每 30s 报一次进度，免得看着像卡死
                last_report = time.time()
                who = ("「自动」仍在" if has_auto
                       else f"OCR 仍读到「{spec['rows'][0]['text']}」")
                print(f"   · 通缉任务进行中（{who}）已等 {int(time.time() - started)}s"
                      f"／上限 {int(timeout)}s")
        elif not spec["title"]:
            if gone_since is not None:
                print("   ⚠️ 相关文字已消失，但连「任务列表」标题也读不到"
                      "（窗口被遮挡 / 界面切走）→ 不计入完成判定")
            gone_since = None
        else:
            if gone_since is None:
                gone_since = time.time()
                print(f"   · 通缉任务相关文字已消失 → 连续确认 "
                      f"{DONE_CONFIRM_SEC:.0f}s 后判定完成")
            elif time.time() - gone_since >= DONE_CONFIRM_SEC:
                return True
        time.sleep(POLL_INTERVAL)
    return False


def run():
    """主流程：找到「自动」→ 点一次 → 确认「是」→ 等相关文字全部消失 → 退出。"""
    print("=" * 60)
    print("🔍 通缉任务脚本 —— 点一次「自动」，等全部做完后自动退出")
    print("=" * 60)
    print(f"   面板区域 {TASK_LIST_REGION}｜「自动」阈值 {AUTO_CONFIDENCE}"
          f"｜「是」阈值 {YES_CONFIDENCE}｜完成确认 {DONE_CONFIRM_SEC:.0f}s")
    if activate_game_window():
        print("✅ 游戏窗口已置前")
    else:
        print("⚠️ 未找到游戏窗口，请确认游戏已启动（继续执行）")

    # ---- 1) 检查有没有可领取的通缉任务 ----
    print()
    print(f"▶ [1/4] 检查任务列表里有没有可领取的通缉任务（最多试 {PROBE_RETRY} 次）...")
    target, note, spec = None, "", None
    for attempt in range(1, PROBE_RETRY + 1):
        spec = inspect()
        ok, note = decide_auto(spec["auto"], spec["rows"])
        if ok:
            target = spec
            break
        detail = (f"「自动」最高分 {spec['auto']['score']:.3f}"
                  if spec["auto"] else "面板内没匹配到「自动」")
        print(f"   第 {attempt}/{PROBE_RETRY} 次：未发现通缉任务（{detail}）")
        if attempt < PROBE_RETRY:
            time.sleep(PROBE_RETRY_INTERVAL)

    if target is None:
        print("ℹ️ 当前没有可领取的通缉任务 → 无需执行，退出")
        if spec and spec["texts"]:
            shown = "；".join(spec["texts"][:6])
            print(f"   （面板内 OCR 读到 {len(spec['texts'])} 条：{shown}"
                  f"{'…' if len(spec['texts']) > 6 else ''}）")
        return

    cx, cy = target["auto"]["center"]
    print(f"   ✅ 发现「自动」({cx},{cy})｜{note}")

    # ---- 2) 点击「自动」（整个流程只点这一次）----
    print()
    print("▶ [2/4] 点击「自动」...")
    click_pos(cx, cy, duration=0.05)
    time.sleep(CLICK_SETTLE_DELAY)

    # ---- 3) 处理确认框 ----
    print()
    print("▶ [3/4] 处理确认框 ...")
    if handle_confirm():
        print("   ⏳ 等确认框关闭 ...")
        wait_confirm_gone()

    # ---- 4) 等通缉任务全部完成 ----
    print()
    print(f"▶ [4/4] 等通缉任务全部完成（相关文字全部消失，上限 "
          f"{MAX_WAIT / 60:.0f} 分钟）...")
    if wait_all_gone():
        print()
        print("✅ 通缉任务已全部完成，脚本退出")
    else:
        print()
        print(f"⚠️ 到 {MAX_WAIT / 60:.0f} 分钟上限仍未看到相关文字全部消失，脚本退出"
              f"（可加大 MAX_WAIT 后重跑）")


# ================= 只读自检 =================

def do_check():
    """只读自检：把判定依据全打出来，不点击任何东西。

    ⚠️ 必须让游戏窗口在最前、并且能看见右侧「任务列表」——
       被别的窗口压住时模板分数会掉，脚本会当成"没有通缉任务"。
    （分辨率自检在入口处已打，这里不再重复。）
    """
    print("=" * 60)
    print("🔍 通缉任务 —— 只读自检（不会点击任何东西）")
    print("=" * 60)
    print(f"   游戏窗口已置前  : {activate_game_window()}")

    spec = inspect()
    a = spec["auto"]
    if a:
        print(f"   「自动」最佳匹配: {a['score']:.3f} @ {a['center']}"
              f"（灰度 {a['gray']:.3f} / 绿掩码 {a['mask']:.3f}，采用「{a['method']}」）")
    else:
        print("   「自动」最佳匹配: 无（模板比面板区域还大？）")
    print(f"   「任务列表」标题: {'可见' if spec['title'] else '读不到'}")
    if spec["rows"]:
        for r in spec["rows"]:
            print(f"   含「通缉」的文字行: y={r['y']:.0f} 「{r['text']}」 置信 {r['score']:.2f}")
    else:
        print("   含「通缉」的文字行: 无")

    ok, note = decide_auto(a, spec["rows"])
    print(f"   结论           : {'✅ 可以点击 —— ' + note if ok else '❌ 当前不该点（分数不足 / 无通缉行）'}")

    print("   ---- 面板区域内 OCR 原文 ----")
    for t in spec["texts"]:
        print(f"     {t}")

    y = locate_yes()
    print(f"   ---- 确认框「是」: "
          + (f"{y[2]:.3f} @ ({y[0]},{y[1]})" if y
             else f"未出现（< {YES_CONFIDENCE}，说明当前没弹确认框）"))


# ================= 入口 =================

def main():
    argv = sys.argv[1:]
    if "--check" in argv:
        do_check()
        return
    run()


if __name__ == "__main__":
    ensure_utf8_stdout()
    check_screen_size()
    main()
