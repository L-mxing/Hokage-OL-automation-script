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
    ⚠️ 这几个常量已随"文字识别层"迁到 ocr_utils.py,本文件仍以导入引用方式沿用同名常量。

模块结构(2026-09-26 拆分):
    本文件            流程编排 + 题库管理 + 点击 + 窗口激活(不含任何 OCR 算法)
    ocr_utils.py      文字识别层:引擎/截图/选项归行/正确率读取/匹配辅助(零 ctypes)
    两者通过 ocr_utils.configure(...) 注入区域常量与打桩回调,单一事实来源仍在本文件。
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
from datetime import date
from difflib import SequenceMatcher

from screenshot_utils import click_pos, ensure_utf8_stdout, check_screen_size

# ---- 文字识别层(已抽成独立模块 ocr_utils,这里只做导入引用) ----
import ocr_utils
from ocr_utils import (
    # 引擎 / 单例
    _create_ocr_engine, get_ocr_engine, get_rec_only_engine,
    # 截图 + OCR
    grab_screen, ocr_image, recognize_screen,
    # 匹配定位
    text_similarity, box_center, find_target, locate_text,
    # 识别助手
    _ocr_region_scaled, options_to_rows, recognize_option_row,
    recognize_options, recognize_options_raw, recognize_question_and_options,
    read_accuracy, read_accuracy_stable, read_accuracy_after_submit,
    # 选项几何 / 匹配
    blind_option_center, option_row_band, missing_option_indices,
    find_matching_option, normalize_option_digits,
    # OCR 常量
    OCR_TEXT_SCORE, REC_ONLY_TEXT_SCORE, OPTION_SCALE, OPTION_MIN_CONFIDENCE,
    OPTION_ROW_HALF, OPTION_ROW_SCALE, OPTION_CENTER_X, OPTION_X_TOLERANCE,
    MIN_CONFIDENCE, ACC_MIN_CONFIDENCE, ANSWER_TEXT_SIM, ANSWER_MARGIN,
    BLIND_OPT_X,
)

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

# ---- OCR 容错(常量已随识别层迁到 ocr_utils,此处仅保留题库侧的阈值) ----
SIMILARITY_THRESHOLD = 0.55                    # 通用文字相似度下限
DB_KEY_SIM = 0.80                              # 题库查题相似度
DB_KEY_MARGIN = 0.08                           # 最佳匹配需领先次优的幅度(防长共前缀题面互撞)
ANSWER_TEXT_SIM = 0.65                         # 答案与选项相似度
ANSWER_MARGIN = 0.08                           # 选项最佳匹配需领先次优的幅度
MAX_CONSECUTIVE_UNREADABLE = 2                 # 连续N题识别不到题目即中止本轮(防在非答题界面误点)

# ---- 窗口 ----
GAME_WINDOW_TITLE = "火影忍者ol"               # 用于答题前激活窗口


# ================= 文字识别层(ocr_utils 注入) =================
# 识别层的区域常量是"单一事实来源":就上面这几个 xxx_REGION。
# ocr_utils 里另有一份安全默认值,启动时在这里覆盖掉,避免两处标定不一致。
ocr_utils.configure(question_region=QUESTION_REGION,
                    options_region=OPTIONS_REGION,
                    accuracy_region=ACCURACY_REGION)


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


# ---- 题面"语义敏感差异"守卫(2026-09-27 新增) ----
# 背景(实测事故):「献祭通灵查克拉【一个】能获取多少铜币」(库内 ans=5000) 与
# 「献祭通灵查克拉【一套】能获取多少铜币」(另一道题) 题面相似度 **0.938**,
# 而次优只有 0.357 ⇒ **DB_KEY_MARGIN 那道闸形同虚设**(margin 0.58 远超 0.08),
# 「一套」被稳稳错配到「一个」上,拿到 5000,再因选项对不上而退随机。
# ⚠️ 关键认知:**margin 守卫只防"两个相似 key 互相竞争",防不住"唯一但又错"的匹配**。
#
# 判据:若两个题面的差异片段里出现"语义敏感字"(数字/量词/五行属性等),就认为
# 它们是**两道不同的题**,禁止模糊匹配 —— 宁可判"未收录"去建库,也不能答错。
# 已对照库内全部 8 对相似度>=0.85 的面板验证:
#   「水/木」「四/一」「个/套」→ 判为敏感(正确拦截,这三对确属不同题)
#   「拿/辜」「做/傲」「狸」(插入)「佩/闻」「绊/伴」「谁」(删除) → 不敏感(正确放行,
#     这些是 OCR 误读或同题异写,必须允许命中)
_SEMANTIC_SENSITIVE = frozenset(
    "一二三四五六七八九十百千万零两"       # 数词
    "个套只次位件名瓶张把支种场本"        # 量词
    "水木火土风雷阴阳"                    # 属性/五行
    "上中下前后左右东南西北"              # 方位
    "男女大小多少新旧"                    # 对立
)


def _semantic_diff(a, b):
    """返回 (是否有语义敏感差异, 差异片段列表)。

    差异片段取自 SequenceMatcher 的非 equal 段;若某段两侧都很短(<=2 字符)
    且含语义敏感字,即视为"换了一问"。
    """
    sm = SequenceMatcher(None, a, b)
    diffs = [(a[i1:i2], b[j1:j2])
             for tag, i1, i2, j1, j2 in sm.get_opcodes() if tag != "equal"]
    for x, y in diffs:
        if len(x) <= 2 and len(y) <= 2 and any(ch in _SEMANTIC_SENSITIVE
                                               for ch in x + y):
            return True, diffs
    return False, diffs


def lookup_in_db(db, question):
    """查库:先精确匹配,再按相似度模糊匹配

    模糊匹配要求"最佳相似度达标"**且**"领先次优至少 DB_KEY_MARGIN" ——
    题库里大量题目共享长前缀(如都以「下面哪位忍者的奥义…」开头),
    只看最大值的话,一旦两道题面的相似度都越过 0.80 就会互相误命中,
    从而点到另一道题的答案。宁可判定"未收录"(随机建库),也不要答错。

    另加**语义敏感差异守卫**(见 _semantic_diff):候选 key 与当前题面若只差一个
    数词/量词(如「一个」vs「一套」),说明是另一道题,直接否决 —— 这是 margin
    守卫覆盖不到的盲区(次优很远时 margin 再大也没用)。
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
        # 语义敏感差异(只差数词/量词等)→ 这是一道**别的题**,直接排除,
        # 不参与 best/second 竞争(否则它会把 best 顶上去、margin 还很大)。
        if s >= DB_KEY_SIM:
            sensitive, diffs = _semantic_diff(key, k)
            if sensitive:
                print(f"   🚫 候选题面「{k}」与当前题目只差 {diffs},"
                      f"判定为另一道题,已排除(相似度 {s:.2f})")
                continue
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


def record_to_db(question, answer, answer_known=True, source=None):
    """题目答对,写入/更新 quiz_db.json

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

    ⚠️ 2026-09-27 起**不再写 blind_idx**(跨轮复用已弃用,理由见 phase2 里的注释):
       选项顺序每轮会变,"上次在第几行"没有预测价值,留着反而误导。
       库里历史遗留的 blind_idx 字段不再被读取,也无害。
    """
    db = load_quiz_db()
    key = normalize_question(question)
    entry = db.setdefault(key, {"answer": "", "seen": 0, "correct": 0,
                                 "wrong": 0, "last": ""})

    old_answer = str(entry.get("answer") or "")
    old_verified = bool(old_answer) and not entry.get("pending")

    if answer_known and not old_verified:
        entry["answer"] = answer
        entry.pop("pending", None)
        entry["source"] = source or entry.get("source") or "自动答题答对"
        print(f"   ✅ 写入 quiz_db.json: [{key}] → 「{answer}」")
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
    # ⚠️ 不清 suspect:随机蒙对**不能**证明库里那个答案是对的(我们只知道自己
    #   随机点中了某个选项,并不知道"正确选项"的原文是否与库里答案一致)。
    #   存疑只能由人工复核后手工清除,避免"蒙对一次就洗白"。
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


def mark_suspect(question, reason):
    """把题库条目标记为 suspect(存疑),但**保留 answer 原文**。

    用途:"可信答案"与真机选项对不上时 —— 这既可能是**库里答案本身错**,
    也可能是**OCR 把选项读花了**。两种都不该由脚本自动改答案,但要留下
    明确痕迹让人工复核,否则这条题会**每轮静默退随机**,永远不会被注意到
    (实测:「献祭通灵查克拉一个能获取多少铜币」answer=5000 与真机
     9w/10W/12W/15W 全不相容,却无声无息地随机了整整两天)。

    与 record_wrong_stats 的区别:那条路径靠"游戏判错"触发,要等**答完**
    才知道;这里是**提交前**就已经发现"答案压根不在选项里",更早、更确定。

    :return: True 表示确实打了标记(False=库里没这题/本就无可信答案)
    """
    db = load_quiz_db()
    key = normalize_question(question)
    entry = db.get(key)
    if entry is None:
        return False
    if not entry.get("answer") or entry.get("pending"):
        return False                    # 没有可信答案,谈不上"答案存疑"
    if entry.get("suspect"):
        return False                    # 已标记过,不重复写盘
    entry["suspect"] = True
    entry["suspect_reason"] = reason
    save_quiz_db(db)
    return True


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
            # ⚠️ 这里**不再复用题库的 blind_idx**(2026-09-27 弃用):
            #   实测同题在不同轮次的选项顺序**会变**(题目原来在第3行、
            #   换一轮跑到第1行),跨轮记住的"位置"毫无依据,复用等于自欺。
            #   盲选本来就只有 1/4 概率,一律随机才是诚实做法。
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
    matched_known = False  # 是否点选了题库已知答案(正确率读不到时用它兜底判定)
    used_random = False    # 是否走了随机兜底(只影响写库时的来源标注)

    if blind:
        # 完全盲选:选项文字未识别,按估算坐标点第 blind_idx 个选项
        chosen_center = blind_option_center(blind_idx)
        chosen_text = f"第{blind_idx + 1}项"
    elif known_answer:
        match = find_matching_option(options, known_answer)
        if match:
            chosen_text, chosen_center, sim = match
            matched_known = True
            print(f"   ✅ 题库命中「{qkey}」→ 答案「{known_answer}」"
                  f" → 点选「{chosen_text}」(相似度 {sim:.2f})")
        else:
            # 答案不在已识别选项里 → 排除法:点漏检的位置(答案文字已知)
            # ⚠️ 已弃用 blind_idx 复用(2026-09-27):选项顺序每轮会变,
            #    "上轮它在第几行"对本周目没有预测力,只能在漏检行里随机。
            missing = missing_option_indices(options)
            if missing:
                guessed_idx = random.choice(missing)
                chosen_center = blind_option_center(guessed_idx)
                chosen_text = known_answer
                print(f"   🔍 答案「{known_answer}」不在已识别选项中,"
                      f"排除法在漏检位置 {[m + 1 for m in missing]} 中随机点第 "
                      f"{guessed_idx + 1} 个")

    if chosen_center is None:
        # 未命中或答案不匹配:随机选一个(用于建库)
        idx = random.randint(0, len(options) - 1)
        chosen_text, chosen_center, sc = options[idx]
        used_random = True          # 来源标注为"随机命中"(答对仍说明这个选项是对的)
        if known_answer:
            # ⚠️ 库里"有答案"却匹配不上任何一个已识别选项 → 必须出声,不能静默随机。
            #   两种可能:① 库里答案本身错(旧条目多来自"资料查证",已坐实过 30级→50级 等);
            #   ② OCR 把选项读花了(如「15W」读成「sw」)。脚本不自动改答案,只:
            #       打出醒目告警 + 给条目标 suspect,交给人工复核 —— 否则这类题会
            #       每轮静默退随机,永远没人发现(实测被静默耽误过整整两天)。
            opts_brief = " / ".join(t for t, _c, _s in options) or "(无)"
            print(f"   🚨 题库答案与选项对不上!题库:「{known_answer}」,"
                  f"本次识别到的选项:[{opts_brief}]")
            print(f"      → 已排除/随机兜底,本次随机选 {idx + 1}: 「{chosen_text}」")
            if mark_suspect(question, f"答案「{known_answer}」不在识别到的选项内"):
                print(f"      ⚠️ 已把「{qkey}」标记 suspect,建议人工复核"
                      f"(是答案错了,还是选项被 OCR 读花?)")
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
        # 答对 → 写入/更新题库(不再记录位置:blind_idx 跨轮复用已弃用)
        # 命中题库答案时写回题库里的原文(而不是 OCR 读出来的、可能带错字的版本)。
        # 已有可信答案时不覆盖;只有盲选(文字没读到)才不写答案。
        # 只要选项文字是**真实读到的**(而非盲选占位符),答案就可信 ——
        # 「正确率+1」是游戏给出的判定。随机命中同样写库(实测:佩恩·畜生道 /
        # 轮回眼 / 12个 都是随机命中后被游戏确认的),区别只体现在来源标注上;
        # 以前把这类答案丢弃、转给人工补答,白白浪费了已经验证过的数据。
        answer_to_store = known_answer if matched_known else chosen_text
        record_to_db(question, answer_to_store,
                     answer_known=(not blind),
                     source="随机命中(游戏已确认,文字可能含OCR误差)" if used_random else None)
        if not blind:
            drop_unknown(question)      # 答案文字已确认,从待补答清单移除
        else:
            # 盲选:选项文字根本没读到,库里只留一个 pending 条目。
            # 必须把选项一并写进待补答清单,否则人工看不到选项,根本无从补答。
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
            print(f"   题库: 命中「{qkey}」→ 答案「{entry.get('answer')}」")
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
                if len(missing) == 1:
                    pick = f"第{missing[0] + 1}行"
                else:
                    pick = f"第{[m + 1 for m in missing]}行 之一(随机)"
                print(f"   预览: 答案「{ans}」未匹配到已识别选项 → 排除法点 {pick}")
            else:
                print(f"   预览: 🚨 答案「{ans}」未匹配且无漏检行 → 只能随机,"
                      f"且会标记 suspect(答案是错的?还是选项被读花?)")
            if entry.get("suspect"):
                print(f"   预览: ⚠️ 该条目已被标记 suspect"
                      f"（{entry.get('suspect_reason') or '原因未记录'}）")
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
