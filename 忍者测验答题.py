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
        5) 区域 (1110, 395, 230, 75) 识别「正确率 x/10」判定对错（较原标定略扩大以容错）
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
    python 忍者测验答题.py --quiz                 # 只识别当前题目/选项(不点击),
                                                  # 并打印原始识别明细,用于排查漏检
    python 忍者测验答题.py --click --target "文字" --region x,y,w,h  # 点击指定文字

识别参数(小数字/短文本识别率低时优先看这几个):
    OCR_TEXT_SCORE      引擎内部置信度门槛,默认 0.5 会把低分短文本直接丢弃
    OPTION_SCALE        选项区域放大倍数
    OPTION_MIN_CONFIDENCE  选项置信度下限(在引擎返回之后才起作用)
"""
from __future__ import annotations

import ctypes
import json
import os
import random
import re
import shutil
import sys
import time
from collections import Counter
from datetime import date
from difflib import SequenceMatcher

import numpy as np
import cv2
import mss
from rapidocr_onnxruntime import RapidOCR

from screenshot_utils import click_pos, ensure_utf8_stdout, check_screen_size

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
OPTION_ROW_HALF = 15                           # 逐行补漏时,行带以行中心为基准的半高(总高 30px)
OPTION_ROW_SCALE = 3                           # 逐行补漏时的放大倍数(裁得小,可以放大更多)
# 选项文字在按钮内是**水平居中**的。实测 10 道题里真实选项的中心 x 恒为 921~922,
# 而面板中心 = 753 + 332//2 = 919。据此可以滤掉"飘进选项区域"的系统滚动公告——
# 实测有一条「转动组织幸运转盘获得」中心在 x=976(y 落在第 1 行),被误当成选项点掉了。
OPTION_CENTER_X = OPTIONS_REGION[0] + OPTIONS_REGION[2] // 2
OPTION_X_TOLERANCE = 45                        # 允许的中心 x 偏差(像素)
# 逐行补漏用的引擎单独放宽内部阈值:它只负责"整区没检出的那一行",
# 裁出来的是一条单行横带,放宽后误识别的风险很低,却能救回更多单数字。
REC_ONLY_TEXT_SCORE = 0.2
ACC_MIN_CONFIDENCE = 0.35                      # 正确率区置信度下限(防低分噪声混进拼接结果)

# ★ OCR 引擎内部的置信度门槛 —— "单数字/两位数识别率极低"的根因就在这里。
#   RapidOCR 默认 text_score=0.5,比它低的结果**在库内部就被丢弃且不返回**,
#   所以下面那个 OPTION_MIN_CONFIDENCE=0.15 永远没机会生效。而单个数字/两位数
#   这类短文本的置信度天然偏低(常落在 0.2~0.5),于是整体被判为"没识别到"。
#   实测(4 种字体 x 4 个字号,单位数+双位数共 32 个样本,4 行全对才算通过):
#       text_score=0.5(默认) → 7/32        text_score=0.25 → 31/32
OCR_TEXT_SCORE = 0.25
DB_KEY_SIM = 0.80                              # 题库查题相似度
DB_KEY_MARGIN = 0.08                           # 最佳匹配需领先次优的幅度(防长共前缀题面互撞)
ANSWER_TEXT_SIM = 0.65                         # 答案与选项相似度
ANSWER_MARGIN = 0.08                           # 选项最佳匹配需领先次优的幅度
MAX_CONSECUTIVE_UNREADABLE = 2                 # 连续N题识别不到题目即中止本轮(防在非答题界面误点)

# ---- 窗口 ----
GAME_WINDOW_TITLE = "火影忍者ol"               # 用于答题前激活窗口

# ================= 引擎单例 =================

_ocr_engine = None
_rec_engine = None                 # "免检测"引擎,仅在逐行补漏时惰性创建
_sct = mss.MSS()


def _create_ocr_engine(**extra) -> RapidOCR:
    """创建 OCR 引擎

    关键参数 det_limit_type:RapidOCR 默认是 "min",含义是"把短边放大到
    det_limit_side_len(736)"。题目区是 502x72 这种宽而扁的区域,短边 72 会被
    放大 10 倍,实际送进检测模型的是 5132x736(378 万像素) —— 实测单次识别 734ms。
    截图场景应改用 "max"(长边不超过 736),同样内容降到 6ms(实测)。

    text_score:库内部的置信度门槛,详见 OCR_TEXT_SCORE 的注释。

    注意:rapidocr_onnxruntime 1.2.3 的 UpdateParameters.update_det_params 会
    无条件读取 det_dict['model_path'],只传 det_limit_* 会 KeyError,必须额外传
    det_model_path=""(空串会让它回填默认模型路径)。这里先按正常写法尝试,
    失败再补该参数,以便将来升级库后依然可用。
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
    """惰性初始化 OCR 引擎(单例)"""
    global _ocr_engine
    if _ocr_engine is None:
        _ocr_engine = _create_ocr_engine()
    return _ocr_engine


def get_rec_only_engine() -> RapidOCR:
    """惰性初始化"免检测"引擎(单例):把整块裁剪当**一行**文本直接识别。

    作用:单个数字/两位数在整区检测里很容易整条漏掉(检测模型对孤立的短文本
    本来就弱)。免检测路线不经过检测模型,而识别模型内部会把文字高度归一化到
    48px,反而更稳。仅在整区检测漏掉某一行时才会用到,所以做成惰性创建——
    用不上就不占额外内存。
    """
    global _rec_engine
    if _rec_engine is None:
        _rec_engine = _create_ocr_engine(use_text_det=False,
                                         text_score=REC_ONLY_TEXT_SCORE)
    return _rec_engine


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
    """读 JSON:文件缺失/损坏都回退 default,不中断主流程

    但**不再静默**:主文件损坏时会打印告警,并尝试同目录的 <path>.bak
    (save_json 采用"先备份再原子替换",所以崩溃后能从这里恢复)。
    原实现在损坏时静默返回 {},而下次保存会把这份"空库"写回去,
    等于把整个题库悄悄清空 —— 这是本函数存在的最大风险点。
    """
    for p in (path, path + ".bak"):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
        except FileNotFoundError:
            continue
        except (json.JSONDecodeError, OSError) as e:
            print(f"⚠️ 题库读取失败({p}): {type(e).__name__}: {e}")
            continue
        if isinstance(data, dict):
            if p != path:
                print(f"⚠️ 主文件不可用,已改用备份 {p} 的内容")
            return data
    return default


def save_json(path, data):
    """原子写入 JSON:UTF-8 + 缩进,方便人工打开编辑补题

    流程:写 <path>.tmp 并 fsync → 备份旧文件到 <path>.bak → os.replace 原子替换。

    为什么要这么麻烦:直接 open(path, "w") 会**立即截断**原文件,之后才逐块写入。
    答题过程中每题都写一次库,一旦在这个瞬间被 Ctrl+C 或 GUI 的 taskkill 打断,
    留下的就是半截 JSON —— 而读取侧会把它当成空库,下次保存即整库覆盖。
    os.replace 在 Windows 上也是原子操作,不会出现"读到一个写了一半的文件"。
    """
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
        f.flush()
        os.fsync(f.fileno())
    if os.path.exists(path):
        shutil.copyfile(path, path + ".bak")   # 只保留一份备份,不累积
    os.replace(tmp, path)


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
    """查库:先精确匹配,再按相似度模糊匹配

    模糊匹配要求"最佳相似度达标"**且**"领先次优至少 DB_KEY_MARGIN" ——
    题库里大量题目共享长前缀(如都以「下面哪位忍者的奥义…」开头),
    只看最大值的话,一旦两道题面的相似度都越过 0.80 就会互相误命中,
    从而点到另一道题的答案。宁可判定"未收录"(随机建库),也不要答错。
    """
    key = normalize_question(question)
    if key in db:
        return key, db[key]
    best_key, best_sim, second_sim = None, 0.0, 0.0
    for k in db:
        # 长度差过大不可能相似,先跳过,省掉昂贵的 SequenceMatcher
        if abs(len(k) - len(key)) > max(10, len(key) // 2):
            continue
        s = text_similarity(key, k)
        if s > best_sim:
            best_key, best_sim, second_sim = k, s, best_sim
        elif s > second_sim:
            second_sim = s
    if best_sim >= DB_KEY_SIM and (best_sim - second_sim) >= DB_KEY_MARGIN:
        return best_key, db[best_key]
    if best_sim >= DB_KEY_SIM:
        print(f"   ⚠️ 查库出现两个相近题面(相似度 {best_sim:.2f} / {second_sim:.2f}),"
              f"无法区分,按未收录处理")
    return None, None


def record_to_db(question, answer, blind_idx=None, answer_known=True,
                 source=None):
    """题目答对,写入/更新 quiz_db.json

    :param blind_idx: 正确答案所在行(0~3)
    :param answer_known: answer 是否为**真实读到的选项文字**(False=盲选,文字未知)
    :param source: 自定义来源标注(如"随机命中(游戏已确认)")
    :return: 题库 key

    answer 字段的写入规则(防止自动流程把人工积累的题库改花):
      - 已有**可信答案**(非空且未标 pending)时,一律不覆盖,只更新统计与位置
        —— 避免 OCR 抖动把正确选项读花成"山中凤"把好答案覆盖掉;
      - 只要 answer 是真实读到的选项文字,就可以写库 —— 因为"正确率+1"是**游戏
        给出的判定**,能确定这个选项就是正确答案。**随机命中同样可信**
        (实测:佩恩·畜生道 / 轮回眼 / 12个 都是随机命中后被游戏确认的),
        区别只体现在来源标注上;
      - 只有盲选(选项文字根本没读到,answer 是"第N项"这类占位符)才不能写。
    """
    db = load_quiz_db()
    key = normalize_question(question)
    entry = db.setdefault(key, {"answer": "", "seen": 0, "correct": 0,
                                 "wrong": 0, "last": ""})

    old_answer = str(entry.get("answer") or "")
    old_verified = bool(old_answer) and not entry.get("pending")

    if blind_idx is not None:
        entry["blind_idx"] = blind_idx

    if answer_known and not old_verified:
        entry["answer"] = answer
        entry.pop("pending", None)
        entry["source"] = source or entry.get("source") or "自动答题答对"
        tag = f" [位置第{blind_idx + 1}行]" if blind_idx is not None else ""
        print(f"   ✅ 写入 quiz_db.json: [{key}] → 「{answer}」{tag}")
    elif old_verified:
        print(f"   ✅ 已收录「{key}」,答案保持「{old_answer}」不改写"
              f"(本次仅更新统计)")
    else:
        # 答案文字不可信:标记待人工确认,不写入 answer
        # (原实现会把 "第2项" 这种位置占位符写进 answer 字段)
        entry["pending"] = True
        entry["source"] = "盲选答对(答案文字待人工确认)"
        print(f"   ✅ 写入 quiz_db.json: [{key}] [未写入答案,已标记待人工确认]")

    entry["seen"] = int(entry.get("seen", 0)) + 1
    entry["correct"] = int(entry.get("correct", 0)) + 1
    entry["wrong"] = 0                      # 连续答错计数,答对即清零
    entry["last"] = today_str()
    save_quiz_db(db)
    return key


def record_wrong_stats(question):
    """答错时累计 quiz_db.json 的统计字段(seen / wrong)。

    原实现答错只写 quiz_unknown.json,完全没碰 db 里的记录,导致:
      - wrong 字段永远只在答对时被重置为 0,从未 +1;
      - seen 只在答对时 +1,于是 seen 恒等于 correct。
    这里只更新统计,不动 answer(答错不改变正确答案)。
    """
    db = load_quiz_db()
    key = normalize_question(question)
    entry = db.get(key)
    if entry is None:
        return                      # 题库没这道题,无需统计
    entry["seen"] = int(entry.get("seen", 0)) + 1
    entry["wrong"] = int(entry.get("wrong", 0)) + 1
    # 库里已有"可信答案"却答错了 → 很可能是这个答案本身不对。
    # 实测:「当角色等级达到多少的时候开启通灵兽系统」的答案「30级」来自
    # "资料查证(火影常识/攻略)",真机被判错。全库 92 题里有 49 题是这个来源。
    # 打 suspect 标记提示人工复核,但不自动删答案(正确率读取也可能出错,不能只凭一次判错就动库)。
    if entry.get("answer") and not entry.get("pending"):
        entry["suspect"] = True
        print(f"   ⚠️ 题库答案「{entry['answer']}」被判为答错"
              f"(累计 {entry['wrong']} 次) → 标记 suspect,建议人工复核")
    save_quiz_db(db)


def record_to_unknown(question, options, reason="答错"):
    """
    题目需要人工补答时,写入/更新 quiz_unknown.json(含全部 4 个选项)

    两种触发:答错(reason="答错"),或"盲选答对但选项文字没读到"——
    后者也必须把选项记下来,否则 quiz_db.json 里只会留下一个 answer 为空的
    pending 条目,人工根本不知道有哪些选项,无从补答。

    选项采用**合并去重**而不是整体覆盖:本次 OCR 可能只认出 3 个,
    直接覆盖会把上一次更完整的选项列表冲掉。
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
    old_options = list(entry.get("options") or [])
    entry["options"] = list(dict.fromkeys(list(options) + old_options))
    entry["first"] = entry.get("first") or today_str()
    entry["times"] = int(entry.get("times", 0)) + 1
    entry["last"] = today_str()
    entry["reason"] = reason
    save_unknown(data)
    mark = "❌" if reason == "答错" else "📥"
    print(f"   {mark} 写入 quiz_unknown.json[{reason}]: [{key}] → 选项: {entry['options']}")
    return key


def drop_unknown(question):
    """题目答案已确认入库 → 从 quiz_unknown.json 移除,避免待补答清单无限堆积。

    :return: True 表示确实删掉了一条
    """
    data = load_unknown()
    key = normalize_question(question)
    if key not in data:
        return False
    data.pop(key)
    save_unknown(data)
    print(f"   🧹 已从 quiz_unknown.json 移除「{key}」(答案已确认)")
    return True


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


def option_row_band(idx, half=OPTION_ROW_HALF):
    """第 idx 行选项(0~3)的裁剪带 (top, bottom):以该行中心为基准上下各取 half。

    为什么不用"等分四行"的宽行带:识别模型会把输入图的高度归一化到 48px,
    裁得越松、文字在图里的占比越小,归一化后越糊。实测同一个数字 "9":
        行带高 38px → 读不出        行带高 30px → 置信 0.30
        行带高 26px → 置信 0.36     行带高 22px → 置信 0.39
    行间距约 50px,取 half=15(高 30px)能让文字基本占满高度,字号 22~26 时
    上下仍有余量,不会切到文字。
    """
    cy = blind_option_center(idx)[1]
    return cy - half, cy + half


# OCR 把数字读成形近字符的常见误读(仅用于"整串都像数字"的选项)
# 注意:成员判断要用 _DIGIT_CONFUSION_MAP —— str.maketrans 返回的字典键是
# 字符的整数序号,用 "x" in 那个表判断永远是 False(踩过)。
_DIGIT_CONFUSION_MAP = {
    "O": "0", "o": "0", "D": "0", "Q": "0", "C": "0", "c": "0",
    "I": "1", "l": "1", "i": "1", "丨": "1", "|": "1",
    "S": "5", "s": "5", "B": "8", "Z": "2", "z": "2", "g": "9", "q": "9",
}
_DIGIT_CONFUSION = str.maketrans(_DIGIT_CONFUSION_MAP)


def normalize_option_digits(text):
    """把形似数字的误读归一化(如 "1O"→"10"、"l5"→"15"、"2O"→"20")。

    仅当整串都是"数字或已知易混字符"时才处理,所以「佐井」「山中风」这类
    纯中文选项完全不受影响,不会把正常文字改坏。
    """
    if not text:
        return text
    if not all(ch.isdigit() or ch in _DIGIT_CONFUSION_MAP for ch in text):
        return text
    return text.translate(_DIGIT_CONFUSION)


def _is_option_like(text):
    """选项文字的宽松过滤:排除标题/按钮等噪声"""
    if not text or text in ("提交", "下一题", "确认", "退出"):
        return False
    if not (1 <= len(text) <= 12):     # 选项文字 1-12 字
        return False
    return True


def options_to_rows(items):
    """把整区检测到的文本框按"就近归行"整理成 {行号: (文字, 中心, 置信度)}。

    比原来"按 y 排序取前 4 个"更稳:噪声框不会把真正的选项挤掉,
    同一行出现多个框时也只保留置信度最高的那个。
    同时用"水平居中"这个已知几何特征把飘进区域的系统公告滤掉(见 OPTION_CENTER_X)。
    """
    fixed_y = [blind_option_center(i)[1] for i in range(4)]
    rows = {}
    for box, text, sc in items:
        if sc < OPTION_MIN_CONFIDENCE:
            continue
        text = normalize_option_digits(text)
        if not _is_option_like(text):
            continue
        cx, cy = box_center(box)
        if abs(cx - OPTION_CENTER_X) > OPTION_X_TOLERANCE:
            # 选项必定水平居中;偏离这么远的多半是滚动公告/提示条,不是选项
            continue
        idx = min(range(4), key=lambda i: abs(fixed_y[i] - cy))
        if idx not in rows or sc > rows[idx][2]:
            rows[idx] = (text, (cx, cy), sc)
    return rows


def recognize_option_row(idx):
    """逐行"免检测"识别第 idx 个选项(整区检测漏掉该行时的补救)。

    :return: (文字, 中心点, 置信度) 或 None
    """
    top, bot = option_row_band(idx)
    region = (OPTIONS_REGION[0], top, OPTIONS_REGION[2], max(1, bot - top))
    items = _ocr_region_scaled(region, OPTION_ROW_SCALE)
    if not items:
        return None
    text = normalize_option_digits(
        "".join(t for _b, t, _s in sorted(items, key=lambda it: box_center(it[0])[0])))
    if not text:
        return None
    sc = min(s for _b, _t, s in items)
    # 裁的是整行横带,所以中心点取该行固定中心(与盲选/排除法用的坐标一致)
    return text, blind_option_center(idx), sc


def recognize_options(probe=None):
    """识别 4 个选项,返回 [(文字, 中心点, 置信度), ...](按行顺序,可能少于 4 个)。

    两条路线配合:
      1) 整区检测(带放大):能拿到每个文本框的精确位置,是主路径;
      2) 逐行"免检测":只对第 1 步没检出的行启用,专治单数字/两位数整条漏检。

    :param probe: 传入一个 list 时,把诊断明细追加进去(供 --quiz 查看)
    """
    rows = options_to_rows(recognize_options_raw(probe))
    options = []
    for idx in range(4):
        hit = rows.get(idx)
        if hit is None:
            hit = recognize_option_row(idx)
            if probe is not None:
                probe.append(f"   第{idx + 1}行 整区未检出 → 逐行补漏: "
                             f"{hit[0] if hit else '仍未识别'}")
        elif probe is not None:
            probe.append(f"   第{idx + 1}行 整区检出: 「{hit[0]}」 置信{hit[2]:.2f}")
        if hit is not None:
            options.append(hit)
    return options


def recognize_options_raw(probe=None):
    """选项区域整区识别(放大),返回屏幕绝对坐标 —— 主路径的原始结果。"""
    items = _ocr_region_scaled(OPTIONS_REGION, OPTION_SCALE)
    if probe is not None:
        for _box, text, sc in items:
            probe.append(f"   [整区原始] 「{text}」 置信{sc:.2f}")
    return items


def recognize_question_and_options(probe=None):
    """识别当前题目 + 4 个选项

    :param probe: 传入一个 list 时,把识别明细追加进去(供 --quiz 诊断用)
    :return: (题目文字 or None,
              [(选项文字, 中心点, 置信度), ...])  # 至多 4 个
    """
    # ---- 题目 ----
    q_items = recognize_screen(QUESTION_REGION)
    if probe is not None:
        for _box, text, sc in q_items:
            probe.append(f"   [题目区原始] 「{text}」 置信{sc:.2f}")
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

    # ---- 选项:整区检测为主,漏检的行再用"逐行免检测"补(见 recognize_options) ----
    options = recognize_options(probe)

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
    # OCR_TEXT_SCORE 调低后可能混入低分噪声,这里按分值再筛一道,
    # 免得噪声把拼出来的「正确率 x/y」串扰坏。
    items = [it for it in items if it[2] >= ACC_MIN_CONFIDENCE]
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
    cnt = Counter(vals)
    return cnt.most_common(1)[0][0]


def read_accuracy_after_submit(correct_before, waits=(0.3, 0.6)):
    """提交后读正确率,允许界面刷新延迟。

    判定"答错"的依据是"正确数没有增加",而提交后界面需要时间刷新。
    原实现只补读 1 次(等 0.3s),机器卡顿或游戏掉帧时会误判答错,
    把本来答对的题写进 quiz_unknown.json。这里最多补读 len(waits) 次,
    一旦看到正确数上涨立即返回。

    :param correct_before: 提交前读数 (正确数, 总数) 或 None
    :return: 最后一次读数 (正确数, 总数) 或 None
    """
    acc = read_accuracy()
    for pause in waits:
        if correct_before and acc and acc[0] > correct_before[0]:
            break                       # 已确认答对,无需再读
        time.sleep(pause)
        nxt = read_accuracy()
        if nxt is not None:
            acc = nxt
    return acc


def find_matching_option(options, answer):
    """在识别到的选项中找与题库答案最匹配的一项

    同样要求"最佳相似度达标 + 领先次优 ANSWER_MARGIN"。
    实测只有最大值的写法有误命中风险:'10' vs '10人' 相似度 0.80、
    '山中风' vs '山中井野' 0.57(越过 0.65 阈值就会点错选项)。
    宁可返回 None 走排除法/随机,也不要选中一个"看起来像"的错选项。
    """
    scored = [(text, center, text_similarity(text, answer))
              for text, center, _sc in options]
    if not scored:
        return None
    scored.sort(key=lambda x: x[2], reverse=True)
    best = scored[0]
    second_sim = scored[1][2] if len(scored) > 1 else 0.0
    if best[2] >= ANSWER_TEXT_SIM and (best[2] - second_sim) >= ANSWER_MARGIN:
        return best
    if best[2] >= ANSWER_TEXT_SIM:
        print(f"   ⚠️ 选项匹配不唯一(「{best[0]}」{best[2]:.2f} vs "
              f"「{scored[1][0]}」{second_sim:.2f}),放弃匹配改走排除法")
    return None


# 盲选:选项区域横向中心 x(4 个选项纵向均分,取每行中心点)
BLIND_OPT_X = OPTION_CENTER_X                              # ≈ 919


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
    used_random = False    # 是否走了随机兜底(只影响写库时的来源标注)

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
        used_random = True          # 来源标注为"随机命中"(答对仍说明这个选项是对的)
        if known_answer:
            print(f"   ⚠️ 题库答案「{known_answer}」与选项不匹配,"
                  f"随机选 {idx + 1}: 「{chosen_text}」")
        else:
            # qkey 为 None 时直接打印它只会看到「None」,这里回退到归一化题面
            print(f"   🎲 题库未收录「{qkey or normalize_question(question)}」,"
                  f"随机选 {idx + 1}: 「{chosen_text}」(用于建库)")

    # 2.5 点击选项 + 等游戏注册
    click_pos(chosen_center[0], chosen_center[1], duration=0.05)
    time.sleep(0.3)  # 简短等待选项注册

    # 2.6 点击提交按钮(固定坐标)
    click_pos(SUBMIT_BTN_POS[0], SUBMIT_BTN_POS[1], duration=0.05)
    time.sleep(0.3)

    # 2.7 读正确率(提交后游戏已自动进入下一题)。
    # 界面刷新需要时间,单次读不到就判"答错"会把本来答对的题写进 unknown,
    # 所以这里允许补读(见 read_accuracy_after_submit)。
    correct_after = read_accuracy_after_submit(correct_before)

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
        # 答对 → 记录答案位置 blind_idx(跨轮复用,即使选项 OCR 漏检也能命中)
        # 命中题库答案时写回题库里的原文(而不是 OCR 读出来的、可能带错字的版本)。
        # 已有可信答案时不覆盖;只有盲选(文字没读到)才不写答案。
        # 只要选项文字是**真实读到的**(而非盲选占位符),答案就可信 ——
        # 「正确率+1」是游戏给出的判定。随机命中同样写库(实测:佩恩·畜生道 /
        # 轮回眼 / 12个 都是随机命中后被游戏确认的),区别只体现在来源标注上;
        # 以前把这类答案丢弃、转给人工补答,白白浪费了已经验证过的数据。
        answer_to_store = known_answer if matched_known else chosen_text
        record_to_db(question, answer_to_store, blind_idx=guess_row,
                     answer_known=(not blind),
                     source="随机命中(游戏已确认,文字可能含OCR误差)" if used_random else None)
        if not blind:
            drop_unknown(question)      # 答案文字已确认,从待补答清单移除
        else:
            # 盲选:选项文字根本没读到,库里只留下 blind_idx。
            # 必须把选项一并写进待补答清单,否则库里只剩一个空答案的 pending 条目,
            # 人工看不到选项,根本无从补答。
            record_to_unknown(question, option_texts, reason="盲选,答案待确认")
    elif is_correct is False:
        # 答错 → 写入 quiz_unknown.json (题目 + 全部 4 个选项) 并累计错题统计
        record_to_unknown(question, option_texts)
        record_wrong_stats(question)
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
    aborted = False
    try:
        consec_unreadable = 0
        for q_no in range(1, MAX_QUESTIONS + 1):
            activate_game_window()
            if phase2_answer_one_question(q_no):
                consec_unreadable = 0
            else:
                # 识别不到题目说明当前界面已不是预期的答题界面,
                # 此时再按固定坐标点「提交」会误触其它控件,所以连续失败即中止。
                consec_unreadable += 1
                print(f"   ⚠️ 第 {q_no} 题未识别到题目 "
                      f"(连续失败 {consec_unreadable}/{MAX_CONSECUTIVE_UNREADABLE})")
                if consec_unreadable >= MAX_CONSECUTIVE_UNREADABLE:
                    print("   ❌ 连续多题无法识别,中止本轮 —— 不执行结算点击,请人工确认界面")
                    aborted = True
                    break
            # 点「提交」后游戏自动进入下一题,无需额外点击翻页
            time.sleep(POST_SUBMIT_WAIT)

        if not aborted:
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
        print(f"   引擎参数: text_score={OCR_TEXT_SCORE}(默认 0.5), "
              f"选项放大×{OPTION_SCALE}, 选项置信度下限={OPTION_MIN_CONFIDENCE}")
        probe = []
        q, options = recognize_question_and_options(probe)
        print("   ---- 原始识别明细(排查小数字/漏检用) ----")
        for line in probe:
            print(line)
        print("   --------------------------------------")
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
    ensure_utf8_stdout()
    check_screen_size()
    main()
