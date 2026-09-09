# -*- coding: utf-8 -*-
"""
忍者测验答题 v5 —— 一键自动化答题脚本(2026-09-05 重构)
========================================================

按用户指定流程拆分为三个明确阶段。所有坐标基于 1920x1080 全屏实测。

流程:
    阶段1: 进入答题界面
        1) 区域 (0, 233, 206, 486) 识别文字「可以进行忍者测验答题」
           → 命中即点击(返回屏幕真实坐标) → 游戏自动寻路到 NPC
        2) 区域 (816, 550, 144, 106) 识别文字「开始测验」
           → 命中即点击 → 进入答题界面
        3) 倒计时 3 秒 → 开始答题

    阶段2: 答题主循环(共 10 题,每题限时 30 秒)
        1) 题目区域 (653, 443, 502, 72) 识别题目
        2) 选项区域 (753, 511, 332, 199) 识别 4 个选项
        3) 查 quiz_db.json:
           - 命中 → 选择相似度最高的选项
           - 未命中 → 随机选择一个(用于建库,首次运行积累数据)
        4) 点击所选选项 → 点击提交按钮 (918, 750)
        5) 区域 (1128, 404, 186, 56) 识别「正确率 x/10」判定对错
           - 答对 → 写入 quiz_db.json(题目 + 正确答案)
           - 答错 → 写入 quiz_unknown.json(题目 + 全部 4 个选项)
        6) 点「提交」后游戏自动进入下一题(无需点击);最后一题提交后进入结算界面

    阶段3: 结算退出
        点「领取奖励」按钮 (935, 689) → 点「退出」按钮 (1315, 370) → 回主城

题库文件:
    quiz_db.json       正确题目库
                       {归一化题目: {answer, seen, correct, wrong, last}}
    quiz_unknown.json  未收录/存疑题目(含全部 4 个选项)
                       {归一化题目: {question, options, first, times, last}}

命令行:
    python 忍者测验答题.py                        # 一键全流程(默认)
    python 忍者测验答题.py --auto-answer          # 仅答题(已在答题界面时)
    python 忍者测验答题.py --quiz                 # 只识别当前题目/选项(不点击)
    python 忍者测验答题.py --click --target "文字" --region x,y,w,h  # 点击指定文字
"""
from __future__ import annotations

import ctypes
import json
import os
import random
import re
import sys
import time
from datetime import date
from difflib import SequenceMatcher

import numpy as np
import cv2
import mss
from rapidocr_onnxruntime import RapidOCR

from screenshot_utils import click_pos

# ================= 配置区域 =================

# ---- 阶段1: 进入答题界面 ----
NAV_PROMPT_REGION = (0, 233, 206, 486)        # 「可以进行忍者测验答题」区域
NAV_PROMPT_TEXT = "可以进行忍者测验答题"
START_TEXT_REGION = (816, 550, 144, 106)       # 「开始测验」文字区域
START_TEXT = "开始测验"
ENTRY_COUNTDOWN_SEC = 3                        # 进入答题后倒计时(秒)

# ---- 阶段2: 答题界面坐标(1920x1080 全屏实测) ----
QUESTION_REGION = (653, 443, 502, 72)           # 题目所在屏幕范围
OPTIONS_REGION = (753, 511, 332, 199)           # 四个选项所在屏幕范围
# 注:用户原描述 (918, 75) 应为 (918, 750),根据提交按钮在面板底部的布局推断
SUBMIT_BTN_POS = (918, 750)                    # 提交按钮坐标(固定点)
ACCURACY_REGION = (1110, 395, 230, 75)         # 「剩余题目 / 正确率 x/10」区域(稍扩大容错)
COUNTDOWN_REGION = (600, 230, 120, 60)         # 倒计时橙圆区域(超时防护参考)

# ---- 阶段3: 结算退出 ----
CLAIM_BTN_POS = (935, 689)                     # 领取奖励按钮坐标(固定)
EXIT_BTN_POS = (1315, 370)                     # 领取奖励后退出按钮坐标(固定)

# ---- 答题参数 ----
MAX_QUESTIONS = 10                             # 每轮题目数
QUESTION_TIMEOUT = 30                          # 每题限时 30 秒(与游戏一致)
POST_SUBMIT_WAIT = 0.2                         # 提交后等界面切换的秒数(游戏自动进下一题,无需久等)

# ---- 题库文件 ----
QUIZ_DB_PATH = "quiz_db.json"
QUIZ_UNKNOWN_PATH = "quiz_unknown.json"

# ---- OCR 容错 ----
SIMILARITY_THRESHOLD = 0.55                    # 通用文字相似度下限
MIN_CONFIDENCE = 0.3                           # OCR 置信度下限(题目等常规文字)
OPTION_MIN_CONFIDENCE = 0.15                   # 选项置信度下限(单数字/短短语天然低)
OPTION_SCALE = 2                               # 选项区域放大倍数(提升小文字识别率)
DB_KEY_SIM = 0.80                              # 题库查题相似度
ANSWER_TEXT_SIM = 0.65                         # 答案与选项相似度

# ---- 窗口 ----
GAME_WINDOW_TITLE = "火影忍者ol"               # 用于答题前激活窗口

# ================= 引擎单例 =================

_ocr_engine = None
_sct = mss.MSS()


def get_ocr_engine() -> RapidOCR:
    """惰性初始化 OCR 引擎(单例)"""
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = RapidOCR()
    return _ocr_engine


# ================= 截图 + OCR =================

def grab_screen(region=None):
    """
    截图(整屏或指定区域)
    :param region: (left, top, width, height);None = 主屏全屏
    :return: (BGR ndarray, offset_x, offset_y)
    """
    if region:
        left, top, width, height = region
        mon = {"left": left, "top": top, "width": width, "height": height}
    else:
        left = top = 0
        mon = _sct.monitors[1]
    raw = _sct.grab(mon)
    img = np.array(raw)[:, :, :3].copy()  # BGRA -> BGR
    return img, left, top


def ocr_image(img):
    """对图像做 OCR,返回 [(box, text, score), ...]"""
    engine = get_ocr_engine()
    result, _ = engine(img)
    if not result:
        return []
    items = []
    for box, text, score in result:
        box = [[float(px) for px in pt] for pt in box]
        try:
            sc = float(score)
        except (TypeError, ValueError):
            sc = 0.0
        items.append((box, str(text).strip(), sc))
    return items


def recognize_screen(region=None):
    """
    截屏 + OCR,一步到位
    :return: [(box, text, score), ...], box 已是屏幕绝对坐标(已加偏移)
    """
    img, off_x, off_y = grab_screen(region)
    items = ocr_image(img)
    if off_x or off_y:
        items = [([[p[0] + off_x, p[1] + off_y] for p in box], text, sc)
                 for box, text, sc in items]
    return items


# ================= 匹配定位 =================

def text_similarity(a, b):
    """两个字符串的相似度(0~1),容忍 OCR 错字"""
    return SequenceMatcher(None, a, b).ratio()


def box_center(box):
    """4 顶点框 -> 中心点(均值)"""
    pts = np.array(box, dtype=np.float64)
    return int(round(pts[:, 0].mean())), int(round(pts[:, 1].mean()))


def find_target(items, target, sim_thresh=SIMILARITY_THRESHOLD,
                min_conf=MIN_CONFIDENCE):
    """在识别结果里找目标文字,按相似度降序"""
    hits = []
    for box, text, score in items:
        if score < min_conf:
            continue
        sim = text_similarity(text, target)
        if text == target or sim >= sim_thresh:
            hits.append((box, text, score, sim))
    hits.sort(key=lambda x: x[3], reverse=True)
    return hits


def locate_text(target, region=None):
    """单次定位目标文字(不点击)"""
    items = recognize_screen(region)
    hits = find_target(items, target)
    if not hits:
        return None
    box, text, score, sim = hits[0]
    cx, cy = box_center(box)
    return {"box": box, "text": text, "score": score, "sim": sim,
            "center": (cx, cy)}


# ================= 轮询 + 点击 =================

def wait_and_click_text(target, region=None, timeout=60, interval=1.5,
                        duration=0.05, desc=""):
    """
    轮询等待目标文字出现,出现后点击其中心点
    :param desc: 日志描述
    :return: 点击的屏幕坐标 (cx, cy);超时返回 None
    """
    if not desc:
        desc = target
    print(f"⏳ 等待「{desc}」出现 (最长 {timeout:.0f}s) ...")
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            loc = locate_text(target, region=region)
        except Exception as e:  # OCR 偶发异常不中断
            print(f"⚠️ 识别异常(可忽略): {type(e).__name__}: {e}")
            loc = None
        if loc:
            cx, cy = loc["center"]
            print(f"✅ 检测到「{loc['text']}」 真实坐标 ({cx},{cy})"
                  f" 置信{loc['score']:.2f},点击")
            click_pos(cx, cy, duration=duration)
            return (cx, cy)
        time.sleep(interval)
    print(f"⚠️ 超时,未出现「{desc}」")
    return None


# ================= 游戏窗口激活 =================

def _find_window_hwnd(title_fragment):
    """按标题子串枚举可见窗口,返回 hwnd(找不到返回 0)"""
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
    """把游戏窗口置前(防被其它窗口遮挡导致 OCR 截错/点击落空)"""
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


# ================= 题库管理 =================

def today_str():
    """今天的日期串 YYYY-MM-DD"""
    return date.today().isoformat()


def load_json(path, default):
    """读 JSON:文件缺失/损坏都回退 default,不中断主流程"""
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else default
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


def save_json(path, data):
    """写 JSON:UTF-8 + 缩进,方便人工打开编辑补题"""
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def load_quiz_db():
    """题库:{归一化题目: {answer, seen, correct, wrong, last}}"""
    return load_json(QUIZ_DB_PATH, {})


def save_quiz_db(db):
    save_json(QUIZ_DB_PATH, db)


def load_unknown():
    """未收录题:{归一化题目: {question, options, first, times, last}}"""
    return load_json(QUIZ_UNKNOWN_PATH, {})


def save_unknown(data):
    save_json(QUIZ_UNKNOWN_PATH, data)


# ---- 归一化 ----

_NUM_PREFIX_RE = re.compile(r"^\s*\d{1,2}\s*[.、．)）:：]\s*")
_PUNCT_RE = re.compile(r"[\s\u3000，。！？、；：,.!?~～\-—_—()（）\[\]【】<>《》\"'“”‘’/\\]+")


def normalize_question(text):
    """题目归一化 → 题库 key:去题号 + 去标点/空白"""
    t = _NUM_PREFIX_RE.sub("", text)
    t = _PUNCT_RE.sub("", t)
    return t


def lookup_in_db(db, question):
    """查库:先精确匹配,再按相似度模糊匹配"""
    key = normalize_question(question)
    if key in db:
        return key, db[key]
    best_key, best_sim = None, 0.0
    for k in db:
        s = text_similarity(key, k)
        if s > best_sim:
            best_key, best_sim = k, s
    if best_sim >= DB_KEY_SIM:
        return best_key, db[best_key]
    return None, None


def record_to_db(question, answer, blind_idx=None, answer_known=True):
    """题目答对,写入/更新 quiz_db.json

    :param blind_idx: 正确答案所在行(0~3)。任何答对都记录,下次可复用位置,
                      即使选项 OCR 漏检也能靠位置命中。
    :param answer_known: answer 是否为真实答案文字(False=盲选占位,标 pending)
    """
    db = load_quiz_db()
    key = normalize_question(question)
    entry = db.setdefault(key, {"answer": "", "seen": 0, "correct": 0,
                                 "wrong": 0, "last": ""})
    entry["answer"] = answer
    entry["seen"] = int(entry.get("seen", 0)) + 1
    entry["correct"] = int(entry.get("correct", 0)) + 1
    entry["wrong"] = 0
    entry["last"] = today_str()
    if blind_idx is not None:
        entry["blind_idx"] = blind_idx
    if answer_known:
        entry.pop("pending", None)
        entry["source"] = entry.get("source", "自动答题答对")
        tag = f" [位置第{blind_idx + 1}行]" if blind_idx is not None else ""
        print(f"   ✅ 写入 quiz_db.json: [{key}] → 「{answer}」{tag}")
    else:
        entry["pending"] = True
        entry["source"] = "盲选答对(答案文字待人工确认)"
        print(f"   ✅ 写入 quiz_db.json: [{key}] → 「{answer}」"
              f" [盲选第{blind_idx + 1}行,待人工确认]")
    save_quiz_db(db)
    return key


def record_to_unknown(question, options):
    """
    题目答错,写入/更新 quiz_unknown.json(含全部 4 个选项)
    用于人工补答或后续手动修复题库。
    """
    data = load_unknown()
    key = normalize_question(question)
    entry = data.setdefault(key, {
        "question": question,
        "options": [],
        "first": today_str(),
        "times": 0,
    })
    entry["question"] = question
    entry["options"] = list(options)        # 全部 4 个选项
    entry["first"] = entry.get("first") or today_str()
    entry["times"] = int(entry.get("times", 0)) + 1
    entry["last"] = today_str()
    save_unknown(data)
    print(f"   ❌ 写入 quiz_unknown.json: [{key}] → 选项: {options}")
    return key


# ================= 识别助手 =================

def _ocr_region_scaled(region, scale=1):
    """对指定区域截图(可选放大)后 OCR,返回屏幕绝对坐标。放大能提升小文字/单数字识别率。"""
    img, off_x, off_y = grab_screen(region)
    if scale > 1:
        h, w = img.shape[:2]
        img = cv2.resize(img, (w * scale, h * scale),
                         interpolation=cv2.INTER_CUBIC)
    items = ocr_image(img)
    result = []
    for box, text, sc in items:
        # 坐标从放大图还原到原图,再加偏移
        box = [[p[0] / scale + off_x, p[1] / scale + off_y] for p in box]
        result.append((box, text, sc))
    return result


def recognize_options():
    """识别选项区域(放大提升小文字/单数字识别率),返回屏幕绝对坐标"""
    return _ocr_region_scaled(OPTIONS_REGION, OPTION_SCALE)


def recognize_question_and_options():
    """识别当前题目 + 4 个选项

    :return: (题目文字 or None,
              [(选项文字, 中心点, 置信度), ...])  # 至多 4 个
    """
    # ---- 题目 ----
    q_items = recognize_screen(QUESTION_REGION)
    question_text = None
    best_score = 0.0
    for _box, text, sc in q_items:
        if sc < MIN_CONFIDENCE:
            continue
        if len(text) < 4:  # 题目通常较长
            continue
        # 选最长且分数最高
        if (question_text is None
                or len(text) > len(question_text)
                or (len(text) == len(question_text) and sc > best_score)):
            question_text = text
            best_score = sc

    # ---- 选项(放大识别 + 降低置信度阈值,提升单数字/短短语识别) ----
    o_items = recognize_options()
    cands = []
    for box, text, sc in o_items:
        if sc < OPTION_MIN_CONFIDENCE:
            continue
        if text in ("提交", ""):
            continue
        if not (1 <= len(text) <= 12):  # 选项文字 1-12 字
            continue
        cx, cy = box_center(box)
        cands.append((cy, cx, text, sc))
    cands.sort()
    options = [(t, (cx, cy), sc) for cy, cx, t, sc in cands[:4]]

    return question_text, options


def _normalize_digits(text):
    """把 OCR 常见误读字符归一化,便于解析正确率里的数字/斜杠"""
    t = text
    for a, b in (("丨", "/"), ("|", "/"), ("／", "/"), ("Ｉ", "1"), ("I", "1"),
                 ("l", "1"), ("i", "1"),
                 ("O", "0"), ("o", "0"), ("D", "0"), ("Q", "0"),
                 ("S", "5"), ("s", "5"), ("B", "8"), ("Z", "2"), ("g", "9")):
        t = t.replace(a, b)
    return t


_accuracy_debug_printed = False  # 诊断信息只打印一次,避免刷屏


def read_accuracy():
    """读顶栏正确率 (正确数, 总数);识别不到返回 None。

    正确率区域显示「剩余题目:N 正确率:x/10」,总数固定 10。
    数字归一化 + 多级兜底,容忍 OCR 对斜杠/数字的误读。
    """
    global _accuracy_debug_printed
    # 放大识别正确率区域(正确率文字较小,放大提升识别率)
    items = _ocr_region_scaled(ACCURACY_REGION, 2)
    row = "".join(t for _b, t, _s in sorted(items,
                                             key=lambda it: box_center(it[0])[0]))
    t = _normalize_digits(row)
    # 1) 标准 x/y(y 接近总题数 10)
    m = re.search(r"(\d{1,2})\s*/\s*(\d{1,2})", t)
    if m:
        x, y = int(m.group(1)), int(m.group(2))
        if 8 <= y <= 12 and 0 <= x <= y:
            return x, y
    # 2) 找「正确」后的数字作为正确数 x,总数默认 10
    idx = t.find("正确")
    if idx >= 0:
        sub = t[idx:idx + 15]
        m = re.search(r"(\d+)", sub)
        if m:
            d = m.group(1)
            # 斜杠被吃/误读,数字串末尾是总数 10(即「x10」形态),前面是正确数 x
            if len(d) >= 3 and d.endswith("10"):
                x = int(d[:-2])
                if 0 <= x <= 10:
                    return x, 10
            # 单独数字 x(0~10)
            if len(d) <= 2 and 0 <= int(d) <= 10:
                return int(d), 10
    # 解析失败:打印一次原始 OCR 结果,便于定位坐标/文字问题
    if not _accuracy_debug_printed:
        _accuracy_debug_printed = True
        raw = [(tx, round(s, 2)) for _b, tx, s in items]
        print(f"   🔍 [诊断] 正确率区域 {ACCURACY_REGION} OCR 结果: {raw}")
    return None


def read_accuracy_stable(attempts=3):
    """多次读取正确率,取出现次数最多的稳定值(抗单次 OCR 抖动)。

    :return: (正确数, 总数) 或 None
    """
    vals = []
    for _ in range(attempts):
        acc = read_accuracy()
        if acc:
            vals.append(acc)
    if not vals:
        return None
    # 取众数(按出现次数,其次按最后一次)
    from collections import Counter
    cnt = Counter(vals)
    return cnt.most_common(1)[0][0]


def find_matching_option(options, answer):
    """在识别到的选项中找与题库答案最匹配的一项"""
    best = None
    for text, center, _sc in options:
        sim = text_similarity(text, answer)
        if best is None or sim > best[2]:
            best = (text, center, sim)
    if best and best[2] >= ANSWER_TEXT_SIM:
        return best
    return None


# 盲选:选项区域横向中心 x(4 个选项纵向均分,取每行中心点)
BLIND_OPT_X = OPTIONS_REGION[0] + OPTIONS_REGION[2] // 2   # ≈ 919


def blind_option_center(idx):
    """估算第 idx(0~3) 个选项的中心点,用于 OCR 漏检(如单个数字选项)时的盲选"""
    y = OPTIONS_REGION[1] + OPTIONS_REGION[3] * (2 * idx + 1) // 8
    return BLIND_OPT_X, y


def missing_option_indices(options):
    """根据识别到的选项 y 坐标,推断哪几个选项位置(0~3)漏检了。

    4 个选项位置纵向均分、固定;把每个识别到的选项按 y 就近归入某一行,
    剩下的行就是漏检的候选位置(正确答案大概率落在这些位置)。
    """
    fixed_ys = [blind_option_center(i)[1] for i in range(4)]
    occupied = set()
    for _text, center, _sc in options:
        cy = center[1]
        nearest = min(range(4), key=lambda i: abs(fixed_ys[i] - cy))
        occupied.add(nearest)
    return [i for i in range(4) if i not in occupied]


def option_row_index(center):
    """根据选项中心点,判断属于第几行(0~3)。用于答对后记录正确答案位置。"""
    fixed_ys = [blind_option_center(i)[1] for i in range(4)]
    return min(range(4), key=lambda i: abs(fixed_ys[i] - center[1]))


# ================= 阶段1: 进入答题界面 =================

def phase1_enter_quiz():
    """
    阶段1: 进入答题界面
        1.1 区域 (0, 233, 206, 486) 识别「可以进行忍者测验答题」并点击
        1.2 区域 (816, 550, 144, 106) 识别「开始测验」并点击
        1.3 倒计时 3 秒
    """
    print()
    print("=" * 60)
    print("📌 阶段1: 进入答题界面")
    print("=" * 60)

    # 激活游戏窗口(防被其它窗口遮挡)
    if activate_game_window():
        print("✅ 游戏窗口已置前")
    else:
        print("⚠️ 未找到游戏窗口,请确认游戏已启动")

    # 1.1 等待并点击活动提示
    print()
    print(f"▶ [1/3] 等待活动提示「{NAV_PROMPT_TEXT}」"
          f" (区域 {NAV_PROMPT_REGION}) ...")
    pos = wait_and_click_text(
        NAV_PROMPT_TEXT, region=NAV_PROMPT_REGION,
        timeout=120, desc="活动提示「可以进行忍者测验答题」")
    if not pos:
        print("❌ 未找到活动提示,流程终止")
        return False
    print(f"   真实点击坐标: {pos}")

    # 1.2 等待寻路,识别文字「开始测验」并点击
    print()
    print(f"▶ [2/3] 等待自动寻路完成,识别文字「{START_TEXT}」"
          f" (区域 {START_TEXT_REGION}) ...")
    pos = wait_and_click_text(
        START_TEXT, region=START_TEXT_REGION,
        timeout=60, desc="文字「开始测验」")
    if not pos:
        print("❌ 未找到文字「开始测验」,流程终止")
        return False
    print(f"   真实点击坐标: {pos}")

    # 1.3 倒计时 3 秒
    print()
    print(f"▶ [3/3] 进入答题界面,倒计时 {ENTRY_COUNTDOWN_SEC} 秒 ...")
    for i in range(ENTRY_COUNTDOWN_SEC, 0, -1):
        print(f"   ⏱️ {i} ...")
        time.sleep(1)

    return True


# ================= 阶段2: 答题主循环 =================

def phase2_answer_one_question(q_no):
    """
    答一题:识别 → 查库/随机 → 点击 → 提交 → 判定 → 写库
    :return: True 答完本题;False 识别失败需人工介入
    """
    print()
    print(f"{'─' * 60}")
    print(f"▶ 第 {q_no} 题 (限时 {QUESTION_TIMEOUT}s)")

    # 2.1 读提交前正确率(对错判定的基准)
    correct_before = read_accuracy_stable(attempts=2)
    print(f"   提交前正确率: {correct_before}")

    # 2.2 识别题目 + 选项(限次数,避免识别不到时反复等待导致超时)
    question, options = None, []
    for _ in range(4):
        question, options = recognize_question_and_options()
        if question and len(options) >= 2:
            break
        time.sleep(0.3)

    # 没识别到题目 → 放弃本题
    if not question:
        print(f"   ❌ 无法识别题目,跳过本题")
        return False

    # 识别到题目但选项不足(如单个数字选项漏检) → 快速重试1次,仍失败则盲选
    blind = False
    blind_idx = None
    if len(options) < 2:
        print(f"   ⚠️ 识别到题目但选项不足({len(options)}个),快速重试1次后盲选...")
        _, options = recognize_question_and_options()
        if len(options) < 2:
            blind = True
            # 若题库曾盲选答对,复用记录的选项位置(命中率更高);否则随机
            _db = load_quiz_db()
            _, _entry = lookup_in_db(_db, question)
            remembered = _entry.get("blind_idx") if _entry else None
            if remembered is not None:
                blind_idx = int(remembered)
                print(f"   🎯 重试后仍识别不到选项,复用题库记录:"
                      f"盲选第 {blind_idx + 1} 个选项位置")
            else:
                blind_idx = random.randint(0, 3)
                print(f"   🎲 重试后仍识别不到选项,盲选第 {blind_idx + 1} 个选项位置")

    print(f"   题目: {question}")
    for i, (text, center, sc) in enumerate(options, 1):
        print(f"      选项{i}: {text}  中心{center}  置信{sc:.2f}")

    # 2.3 查 quiz_db.json
    db = load_quiz_db()
    qkey, entry = lookup_in_db(db, question)
    known_answer = entry.get("answer") if entry else None

    # 2.4 选择选项(优先用题库答案,否则随机)
    chosen_text, chosen_center = None, None
    guess_row = None      # 本次实际点击的行号(0~3),答对后记录为 blind_idx
    matched_known = False  # 是否点选了题库已知答案(正确率读不到时用它兜底判定)

    if blind:
        # 完全盲选:选项文字未识别,按估算坐标点第 blind_idx 个选项
        chosen_center = blind_option_center(blind_idx)
        chosen_text = f"第{blind_idx + 1}项"
        guess_row = blind_idx
    elif known_answer:
        match = find_matching_option(options, known_answer)
        if match:
            chosen_text, chosen_center, sim = match
            guess_row = option_row_index(chosen_center)
            matched_known = True
            print(f"   ✅ 题库命中「{qkey}」→ 答案「{known_answer}」"
                  f" → 点选「{chosen_text}」(相似度 {sim:.2f})")
        else:
            # 答案不在已识别选项里 → 排除法:点漏检的位置(答案文字已知)
            missing = missing_option_indices(options)
            if missing:
                remembered = entry.get("blind_idx") if entry else None
                if remembered is not None and int(remembered) in missing:
                    guessed_idx = int(remembered)
                else:
                    guessed_idx = random.choice(missing)
                chosen_center = blind_option_center(guessed_idx)
                chosen_text = known_answer
                guess_row = guessed_idx
                print(f"   🔍 答案「{known_answer}」不在已识别选项中,"
                      f"排除法点漏检位置第 {guessed_idx + 1} 个")

    if chosen_center is None:
        # 未命中或答案不匹配:随机选一个(用于建库)
        idx = random.randint(0, len(options) - 1)
        chosen_text, chosen_center, sc = options[idx]
        guess_row = option_row_index(chosen_center)
        if known_answer:
            print(f"   ⚠️ 题库答案「{known_answer}」与选项不匹配,"
                  f"随机选 {idx + 1}: 「{chosen_text}」")
        else:
            print(f"   🎲 题库未收录「{qkey}」,随机选 {idx + 1}: "
                  f"「{chosen_text}」(用于建库)")

    # 2.5 点击选项 + 等游戏注册
    click_pos(chosen_center[0], chosen_center[1], duration=0.05)
    time.sleep(0.3)  # 简短等待选项注册

    # 2.6 点击提交按钮(固定坐标)
    click_pos(SUBMIT_BTN_POS[0], SUBMIT_BTN_POS[1], duration=0.05)
    time.sleep(0.3)

    # 2.7 读正确率(快速),提交后游戏已自动进入下一题
    correct_after = read_accuracy()
    if correct_after == correct_before:
        # 可能没读到更新值,再补 1 次
        time.sleep(0.3)
        correct_after = read_accuracy()

    # 2.8 判定对错
    is_correct = None
    if correct_before and correct_after:
        if correct_after[0] > correct_before[0]:
            is_correct = True
        elif correct_after[0] == correct_before[0]:
            is_correct = False
        # else: 新一轮开始,无法判定
    elif matched_known:
        # 正确率读取失败,但点选了题库已知答案,大概率答对
        is_correct = True
        print(f"   正确率读取失败,但点选了题库答案「{known_answer}」,按答对处理")

    if is_correct is True:
        print(f"   提交后正确率: {correct_after} → ✅ 答对")
    elif is_correct is False:
        print(f"   提交后正确率: {correct_after} → ❌ 答错")
    else:
        print(f"   提交后正确率: {correct_after} → ⚠️ 无法判定")

    # 2.9 写库
    option_texts = [t for t, _c, _s in options]
    if is_correct is True:
        # 答对 → 记录答案文字 + 正确答案位置 blind_idx(跨轮复用)
        record_to_db(question, chosen_text, blind_idx=guess_row,
                     answer_known=(not blind))
    elif is_correct is False:
        # 答错 → 写入 quiz_unknown.json (题目 + 全部 4 个选项)
        record_to_unknown(question, option_texts)
    else:
        # 无法判定:保守写入 quiz_unknown.json 待人工确认
        print(f"   ⚠️ 无法判定对错,保守写入 quiz_unknown.json")
        record_to_unknown(question, option_texts)

    return True


def phase2_answer_round():
    """
    阶段2: 答题主循环 (共 10 题)

    每题点「提交」后游戏会自动进入下一题,无需点「下一题」。
    第 10 题提交后进入结算,点「领取奖励」(935,689) 再点「退出」(1315,370)。
    """
    print()
    print("=" * 60)
    print(f"📌 阶段2: 答题主循环 (共 {MAX_QUESTIONS} 题,"
          f"每题限时 {QUESTION_TIMEOUT}s)")
    print("=" * 60)

    finished = False
    try:
        for q_no in range(1, MAX_QUESTIONS + 1):
            activate_game_window()
            phase2_answer_one_question(q_no)
            # 点「提交」后游戏自动进入下一题,无需额外点击翻页
            time.sleep(POST_SUBMIT_WAIT)

        # 10 题答完:点「领取奖励」按钮(固定坐标)
        print(f"   🏁 点击「领取奖励」按钮 {CLAIM_BTN_POS} ...")
        time.sleep(POST_SUBMIT_WAIT)
        click_pos(CLAIM_BTN_POS[0], CLAIM_BTN_POS[1], duration=0.05)
        time.sleep(1.0)

        # 领取奖励后:点「退出」按钮(固定坐标)
        print(f"   🚪 点击「退出」按钮 {EXIT_BTN_POS} ...")
        time.sleep(1.0)
        click_pos(EXIT_BTN_POS[0], EXIT_BTN_POS[1], duration=0.05)
        finished = True
    except KeyboardInterrupt:
        print("\n🛑 手动中断 —— 已答题目已写回题库,重跑即可继续")

    return finished


# ================= 主流程 =================

def run_full_quiz():
    """一键全流程:进入答题 + 答完一轮"""
    print("=" * 60)
    print("🎯 忍者测验答题 v5 —— 一键全流程")
    print("=" * 60)

    # 阶段1: 进入答题界面
    if not phase1_enter_quiz():
        return

    # 阶段2: 答题主循环
    finished = phase2_answer_round()

    # 总结
    db = load_quiz_db()
    unk = load_unknown()
    print()
    print("=" * 60)
    if finished:
        print("✅ 本轮答题完成!")
    else:
        print("⚠️ 本轮答题未完整结束(可能中断或识别失败)")
    print(f"📚 quiz_db.json      已收录 {len(db)} 题")
    print(f"📝 quiz_unknown.json 待人工补答 {len(unk)} 题")
    print("=" * 60)


# ================= 参数解析 =================

def parse_region(argv):
    """解析 --region left,top,width,height"""
    for arg in argv:
        if arg.startswith("--region="):
            arg = arg[len("--region="):]
        elif arg == "--region":
            arg = argv[argv.index("--region") + 1]
        else:
            continue
        parts = [int(x) for x in arg.replace(" ", "").split(",")]
        if len(parts) == 4:
            return tuple(parts)
        print("⚠️ --region 格式应为 left,top,width,height,已忽略")
    return None


def has_flag(argv, flag):
    return flag in argv


def parse_target(argv):
    """解析 --target "文字",未指定则用 NAV_PROMPT_TEXT"""
    for i, arg in enumerate(argv):
        if arg == "--target" and i + 1 < len(argv):
            return argv[i + 1]
        if arg.startswith("--target="):
            return arg[len("--target="):]
    return NAV_PROMPT_TEXT


# ================= 主入口 =================

def main():
    print("=" * 60)
    print("忍者测验答题 v5 (OCR + 自动点击 + 题库积累)")
    print("=" * 60)

    argv = sys.argv[1:]
    region = parse_region(argv)
    do_auto = has_flag(argv, "--auto-answer")
    do_quiz_only = has_flag(argv, "--quiz")
    do_click = has_flag(argv, "--click")
    target = parse_target(argv)

    # 模式 A: 仅答题(已在答题界面时)
    if do_auto:
        activate_game_window()
        phase2_answer_round()
        return

    # 模式 A2: 只识别当前题目/选项(不点击,核对版面 + 漏检/排除法预览)
    if do_quiz_only:
        q, options = recognize_question_and_options()
        if not q:
            print("❌ 未识别到题目")
            return
        print(f"📝 题目: {q}")

        # 题库命中情况
        db = load_quiz_db()
        qkey, entry = lookup_in_db(db, q)
        if entry and entry.get("answer"):
            extra = f", blind_idx={entry['blind_idx']}" if entry.get("blind_idx") is not None else ""
            print(f"   题库: 命中「{qkey}」→ 答案「{entry.get('answer')}」{extra}")
        else:
            print(f"   题库: 未收录(将随机建库)")

        # 识别到的选项 + 所属行
        fixed_ys = [blind_option_center(i)[1] for i in range(4)]
        print(f"   识别到选项 {len(options)} 个:")
        for i, (text, center, sc) in enumerate(options, 1):
            nearest = min(range(4), key=lambda j: abs(fixed_ys[j] - center[1]))
            print(f"      选项{i}: {text}  中心{center}  置信{sc:.2f} → 第{nearest+1}行")

        # 漏检行
        missing = missing_option_indices(options)
        print(f"   漏检行: {[m + 1 for m in missing] if missing else '无(4个全识别)'}")

        # 4 行固定坐标(盲选/排除法点击点)
        print(f"   4 行固定中心坐标:")
        for j in range(4):
            cx, cy = blind_option_center(j)
            mark = "  ← 漏检" if j in missing else ""
            print(f"      第{j+1}行: ({cx}, {cy}){mark}")

        # 预览:本次会怎么选
        if entry and entry.get("answer"):
            ans = entry["answer"]
            match = find_matching_option(options, ans)
            if match:
                print(f"   预览: 答案「{ans}」匹配到选项「{match[0]}」→ 正常点选")
            elif missing:
                remembered = entry.get("blind_idx")
                if remembered is not None and int(remembered) in missing:
                    pick = f"第{int(remembered) + 1}行(复用blind_idx)"
                elif len(missing) == 1:
                    pick = f"第{missing[0] + 1}行"
                else:
                    pick = f"第{[m + 1 for m in missing]}行 之一(随机)"
                print(f"   预览: 答案「{ans}」未匹配到已识别选项 → 排除法点 {pick}")
            else:
                print(f"   预览: 答案「{ans}」未匹配且无漏检行 → 随机选")
        return

    # 模式 B: 点击指定文字(单次轮询)
    if do_click:
        activate_game_window()
        clicked = wait_and_click_text(target, region=region)
        print("✅ 完成" if clicked else "❌ 本次未点击(超时)")
        return

    # 默认: 一键全流程
    run_full_quiz()


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    main()
