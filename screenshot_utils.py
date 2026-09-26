"""
图像识别与点击的公共工具模块
Opencv + pyautogui 实现，供各脚本调用
v3：mss 截图定位；轮询统一走 _poll 集中处理异常（连续失败即上抛，避免静默空等）；
    模板大于搜索区域时提前返回 None；locate_on_screen 可选返回匹配分数。
v4：pyautogui.PAUSE 由默认 0.1s 压到 0.01s（每次点击原本白等 0.2s）；
    新增 ensure_utf8_stdout() / check_screen_size()，供各脚本 __main__ 做环境自检。
v5：自动战斗/倍速三件套重做：
    - 探测改用短超时 SHORT_PROBE_TIMEOUT（原来误吃 DEFAULT_TIMEOUT=60，已开启时白等一分钟）；
    - 点击复用刚定位到的中心点，省掉一次"重新截图 + 重新匹配"；
    - 返回值由 bool 改为三态 BattleState（False 同时表示"本来就开着"和"界面不在这"，
      上层无法据此决定要不要告警）；
    - 参数全部透传（confidence/timeout/interval/duration）并补进 __all__。
v6：战斗开始时的一次性界面检测：
    - 新增 locate_many_on_screen / wait_any_image：一次截屏匹配多个模板（4 个战斗图标
      共用同一帧，省掉 3 次截屏 + 3 次灰度转换）；
    - 新增 set_speed（三态 SpeedState）/ BattleUiReport：自动战斗与倍速各自三态，
      按检测结果打印状态；
    - auto_battle_with_speed 改为「战斗开始时调用一次」的入口，返回 BattleUiReport。
      一场战斗只调一次，检测完就不再重复探测。
v7：真机日志驱动的小修（2026-09-14）：
    - 面板已由 wait_any_image 确认存在后，逐个探测改吃 QUICK_PROBE_TIMEOUT(0.6s)，
      不再各白等 2s（真机上每轮白等了 4s）；
    - 新增 describe_matches()：未确认时把每个模板的"最高分 @ 位置"打出来，
      一眼区分"图标不在这个区域"（分数极低）和"图标在但模板对不上"（分数接近阈值）。
v8：v7 的收窗口踩了自己的脚：把"点击后等状态变化"的窗口也收成 0.6s，真机上出现
    点击明明生效（下一帧 auto_enable 匹配 0.999）却报"复核失败"。现在拆成两档：
    QUICK_PROBE_TIMEOUT(0.6s) 只用于"图标在不在"，点击后的复核用
    CLICK_VERIFY_TIMEOUT(2.0s)（UI 动画/同步没那么快）。
v9：真机截图定位到根因后收尾：模板必须"裁紧"（原图带战斗背景，同一状态换场景分数能
    从 0.99 掉到 0.79 → 倍速切不上），三个脚本改用 *_tight.png；倍速"当前档位"的判定
    阈值收紧到 SPEED_STATE_CONFIDENCE（x1/x2 模板互相关 0.86，0.8 会漏点）；
    战斗界面 UI 会淡出导致后续场次探不到自动战斗按钮，新增 BattleState.REUSED
    + reset_battle_ui_memo()：沿用本进程内已确认的状态（倍速不沿用）。
v10：不再往磁盘落任何调试截图（曾经的 dump_region_debug / dump_debug 参数 / imwrite_u
    与 debug/ 目录相关代码已全部移除）。未确认时只保留控制台诊断 describe_matches()，
    排查靠日志里的"分数@坐标"，脚本运行期间不会再产生图片文件。
v11：自动战斗的探测阈值与倍速拆开（2026-09-17，用户日志驱动）：
    真机上"点完开始战斗"那一刻，自动战斗按钮处于半透明渲染态，auto_enable 只能到
    0.615、auto_disable 只有 0.29~0.32，被通用阈值 0.80 一路挡住 → BattleState 恒为
    UNKNOWN → 三个脚本的 _battle_ui_ready 永不置位 → 每场战斗都重跑整套探测（日志里
    "本场仅检测一次"却场场出现）。现在 auto 这一对改用 AUTO_STATE_CONFIDENCE(0.55)，
    倍速仍用严格阈值；describe_matches 支持按模板传阈值，诊断行的 ✓ 与真实判定一致。
v12：未命中就地补打"最高分 @ 位置"（2026-09-17）。原来只有"整套未确认"才会有诊断行，
    单步没命中时只剩一句"超时未出现"，看不出差多少（真机上 auto_enable 差 0.185 那次
    就是这么被埋掉的）。现在 ensure_auto_battle / set_speed 的每一步未命中都各补一行，
    由 auto_battle_with_speed 的 diagnose_miss（默认开）统一控制；只在失败路径多截一帧
    （约 10~30ms），命中路径零开销。诊断自身的异常一律只提示、不打断战斗流程。
v13：新增 count_matches()（2026-09-22）：数同一个图标在区域里出现了几次。
    locate_on_screen / locate_many_on_screen 都只给"最佳的那一个位置"，数不了个数；
    八门遁甲需要数"背包还剩几个空格子"，就补了这个。纯新增，不改动任何既有函数。
"""
from __future__ import annotations # python版本3.10以上可不写

import os
import sys
import time
from dataclasses import dataclass
from enum import Enum

import cv2
import numpy as np
import pyautogui
import mss

__all__ = [
    "locate_on_screen",
    "locate_many_on_screen",
    "count_matches",
    "describe_matches",
    "click_pos",
    "wait_and_click_image",
    "wait_image",
    "wait_any_image",
    "ensure_auto_battle",
    "set_speed",
    "auto_battle_with_speed",
    "BattleState",
    "SpeedState",
    "BattleUiReport",
    "reset_battle_ui_memo",
    "ensure_utf8_stdout",
    "check_screen_size",
]

# 默认参数（各脚本需要不同值时，在调用处传参覆盖）
DEFAULT_CONFIDENCE = 0.8           # 匹配精度
DEFAULT_TIMEOUT = 60               # 等待图片出现的最长时间（秒）
DEFAULT_INTERVAL = 0.5             # 每次检查间隔
DEFAULT_DURATION = 0.3             # 鼠标移动持续时间
MAX_CONSECUTIVE_ERRORS = 5         # 轮询连续异常达到该次数即上抛（多为环境/代码问题）

SHORT_PROBE_TIMEOUT = 2.0          # 幂等探测窗口：判断"图标此刻在不在"，不是"等它出现"
QUICK_PROBE_TIMEOUT = 0.6          # 面板已确认出现后的探测窗口（图标要么在要么不在，别多等）
CLICK_VERIFY_TIMEOUT = 2.0         # 点击后等"状态真的变了"的窗口（UI 动画/同步没这么快！）
CLICK_SETTLE_DELAY = 0.3           # 两次点击之间的界面响应间隔
BATTLE_UI_READY_TIMEOUT = 6.0      # 战斗开始后等"战斗界面图标出现"的总窗口（秒）

# 判定"倍速当前是几倍"要用的严格阈值。x1/x2 两个模板灰度互相关高达 0.86
# （只差数字笔画），用默认 0.8 会把 x1 画面认成 x2 → 跳过点击 → 倍速永远切不上
# （真机组队副本第 2、5 轮就是这么漏的）。实测：本档 0.99x，另一档 0.86x，取 0.9 分开。
SPEED_STATE_CONFIDENCE = 0.9

# 自动战斗按钮的探测阈值，**比通用阈值 0.80 松**（2026-09-17 实测）。
# 为什么必须松：战斗刚开始（探测时刻）该按钮是半透明渲染态，与当初裁模板时的
# 不透明态不是同一张画面。同一帧、同一区域里的实测值：
#   auto_enable.png  = 0.615（已开启态，探测时刻）   0.34 是"未开启/其它态"的天花板
#   auto_disable.png = 0.29~0.32
#   tag_speed_2x.png = 1.000（同一帧里满值 → 区域坐标没错、面板也在，问题只在 auto 模板）
# 排除过的其它解释：位置偏移（±6px 扫描峰值仍在原点）、尺寸变化（径向梯度剖面测按钮
# 半径 11px vs 10px）、单纯发虚（对模板加高斯模糊最多只救到 0.643）。
# 取 0.55：距"已开启态 0.615"0.065，距"未开启态天花板 0.34"0.21。
# ⚠️ 已知不确定性：绿色"自动"（未开启）态在现存真机帧里从未出现过（红/绿两个模板的
# 交叉分只有 0.18~0.23），所以这个阈值是按"红模板对绿按钮不会高分"推的。真遇到自动
# 战斗真的关着时要留意日志里有没有把未开启态误判成已开启。
AUTO_STATE_CONFIDENCE = 0.55

# pyautogui 每次调用后都会固定等待 PAUSE 秒，默认 0.1。而 click_pos 内部是
# moveTo + click 两次调用，等于每次点击白多 0.2s；一次刷图几十次点击就是好几秒。
# 压到 0.01s（不设为 0，避免游戏来不及处理输入而丢点击事件）。
PYAUTOGUI_PAUSE = 0.01
pyautogui.PAUSE = PYAUTOGUI_PAUSE

_template_cache: dict[str, tuple[np.ndarray, float]] = {}
_sct = mss.MSS()                   # mss 全局实例，复用避免反复初始化开销


@dataclass
class MatchResult:
    """图像匹配结果：屏幕外接矩形 + 匹配分数"""
    left: int
    top: int
    width: int
    height: int
    score: float

    @property
    def center(self) -> tuple[int, int]:
        """矩形中心坐标（用于点击）"""
        return (self.left + self.width // 2, self.top + self.height // 2)


def _load_template(image_path: str) -> np.ndarray:
    """
    读取模板并转灰度（带缓存 + mtime 校验）；np.fromfile + imdecode 兼容中文路径
    开发期替换模板 PNG 后会自动重新加载，无需重启脚本。
    :param image_path: 模板图片路径
    :return: 灰度模板图像
    """

    mtime = os.path.getmtime(image_path)# 系统调用变局部变量更省
    if image_path not in _template_cache or \
            mtime != _template_cache[image_path][1]:
        # 用 != 比 > 更严谨
        data = np.fromfile(image_path, dtype=np.uint8)
        template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(f"模板图片加载失败: {image_path}")
        _template_cache[image_path] = (template, mtime)
    return _template_cache[image_path][0]


def locate_on_screen(image_path: str, region: tuple[int, int, int, int] | None = None,
                     confidence: float = DEFAULT_CONFIDENCE,
                     return_score: bool = False
                     ) -> tuple[int, int, int, int] | MatchResult | None:
    """
    mss 截图 + OpenCV 匹配
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :param return_score: True 时返回 MatchResult（含匹配分数），否则返回坐标元组
    :return: (left, top, width, height) 或 MatchResult；未找到返回 None
    """
    template = _load_template(image_path)
    th, tw = template.shape[:2]

    offset_x = offset_y = 0
    if region:
        left, top, width, height = region
        offset_x, offset_y = left, top
        if tw > width or th > height:
            return None
        mon = {"left": left, "top": top, "width": width, "height": height}
    else:
        mon = _sct.monitors[1]     # 主显示器

    # mss 输出 BGRA，截掉 alpha 转灰度
    frame = cv2.cvtColor(np.array(_sct.grab(mon))[:, :, :3], cv2.COLOR_BGR2GRAY)
    fh, fw = frame.shape[:2]
    if th > fh or tw > fw:
        return None                # 模板比截图区域还大，无法匹配（避免 cv2 抛错）

    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= confidence:
        left = max_loc[0] + offset_x
        top = max_loc[1] + offset_y
        if return_score:
            return MatchResult(left, top, tw, th, float(max_val))
        return (left, top, tw, th)
    return None


def locate_many_on_screen(image_paths, region: tuple[int, int, int, int] | None = None,
                          confidence: float = DEFAULT_CONFIDENCE,
                          want_best: bool = False
                          ) -> dict[str, MatchResult | None]:
    """一次截屏同时匹配多个模板，返回 {模板路径: MatchResult | None}。

    界面状态检测往往要同时看好几个图标（战斗界面就是自动战斗开/关 + x1/x2 四个），
    逐个调 locate_on_screen 会白截 N-1 次图、白转 N-1 次灰度；这里共用同一帧，
    也保证所有图标判定的时间点完全一致（不会出现"看 A 时是开的、看 B 时已经关了"）。

    :param image_paths: 模板路径列表（自动去重，保序）
    :param region: 搜索区域 (left, top, width, height)，None = 全屏
    :param confidence: 匹配精度
    :param want_best: True 时即使没过阈值也返回"最佳匹配"（含真实分数与位置），
        用于诊断"图标到底不在区域里，还是模板对不上"；此时只有模板大于区域才是 None
    :return: 与入参一一对应的字典；未命中或模板大于截图区域时为 None
    """
    paths = list(dict.fromkeys(image_paths))          # 去重且保持顺序
    templates = {p: _load_template(p) for p in paths}

    offset_x = offset_y = 0
    if region:
        left, top, width, height = region
        offset_x, offset_y = left, top
        mon = {"left": left, "top": top, "width": width, "height": height}
    else:
        mon = _sct.monitors[1]

    frame = cv2.cvtColor(np.array(_sct.grab(mon))[:, :, :3], cv2.COLOR_BGR2GRAY)
    fh, fw = frame.shape[:2]

    found: dict[str, MatchResult | None] = {}
    for path in paths:
        template = templates[path]
        th, tw = template.shape[:2]
        if th > fh or tw > fw:
            found[path] = None                        # 模板比截图区域还大，无法匹配
            continue
        result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
        _, max_val, _, max_loc = cv2.minMaxLoc(result)
        if max_val >= confidence or want_best:
            found[path] = MatchResult(max_loc[0] + offset_x, max_loc[1] + offset_y,
                                      tw, th, float(max_val))
        else:
            found[path] = None
    return found


def count_matches(image_path: str, region: tuple[int, int, int, int] | None = None,
                  confidence: float = DEFAULT_CONFIDENCE,
                  min_distance: int = 20) -> tuple[int, float]:
    """数同一个模板在区域里出现了几次，返回 (个数, 区域内最高分)。

    locate_on_screen 只给"最佳的那一个位置"、locate_many_on_screen 也是每个模板一个，
    都数不了个数。数"背包还剩几个空格子"这类需求就得单独来一趟。

    去重规则：两个命中点的中心相距小于 min_distance 像素时算同一个图标。
    默认 20 是给"格子间距 ~95px"那种场景用的（实测 20/0 两态都数得准）；
    图标挨得很近时这个值要按实际间距调小。

    :param image_path: 模板路径
    :param region: 搜索区域 (left, top, width, height)，None = 全屏
    :param confidence: 命中阈值
    :param min_distance: 去重半径（像素）
    :return: (命中个数, 区域内最高分)；模板比区域大时返回 (0, 0.0)
    """
    template = _load_template(image_path)
    th, tw = template.shape[:2]

    offset_x = offset_y = 0
    if region:
        left, top, width, height = region
        offset_x, offset_y = left, top
        if tw > width or th > height:
            return 0, 0.0
        mon = {"left": left, "top": top, "width": width, "height": height}
    else:
        mon = _sct.monitors[1]

    frame = cv2.cvtColor(np.array(_sct.grab(mon))[:, :, :3], cv2.COLOR_BGR2GRAY)
    if th > frame.shape[0] or tw > frame.shape[1]:
        return 0, 0.0

    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    ys, xs = np.where(result >= confidence)
    limit = min_distance ** 2
    centers: list[tuple[int, int]] = []
    for y, x in zip(ys, xs):
        cx, cy = int(x) + tw // 2 + offset_x, int(y) + th // 2 + offset_y
        if all((cx - px) ** 2 + (cy - py) ** 2 > limit for px, py in centers):
            centers.append((cx, cy))
    return len(centers), float(result.max())


def describe_matches(image_paths, region: tuple[int, int, int, int] | None = None,
                     confidence: float | dict[str, float] = DEFAULT_CONFIDENCE) -> str:
    """诊断用：把几个模板的"最高分 @ 中心坐标"拼成一行，看图标到底去哪了。

    判读方法：
    - 分数很低（< 0.5）→ 图标根本不在这块区域（位置不对 / 面板没显示 / 被遮挡）
    - 分数贴着阈值（0.7~0.8）→ 图标就在这，只是模板与真实画面有差（重截模板或调阈值）
    - 模板大于区域 → 会直接说明，这种是整块漏检

    :param confidence: 判定阈值。可以传 float（所有模板共用，旧行为），也可以传
        `{模板路径: 阈值}`——战斗界面上自动战斗与倍速用的阈值本来就不一样
        （见 AUTO_STATE_CONFIDENCE / SPEED_STATE_CONFIDENCE），传映射时 ✓ 才与
        真实判定一致。
    """
    per_path = confidence if isinstance(confidence, dict) else None
    # want_best=True 时阈值不参与筛选，但 locate_many_on_screen 会先做 `>= confidence`
    # 比较，所以传映射时必须换成一个 float，否则 dict 比较直接抛 TypeError。
    best = locate_many_on_screen(image_paths, region=region,
                                 confidence=DEFAULT_CONFIDENCE if per_path else confidence,
                                 want_best=True)
    parts = []
    for path, match in best.items():
        name = os.path.basename(path)
        if match is None:
            parts.append(f"{name}: 模板大于搜索区域")
        else:
            cx, cy = match.center
            thr = per_path.get(path, DEFAULT_CONFIDENCE) if per_path else confidence
            hit = "✓" if match.score >= thr else ""
            parts.append(f"{name}: {match.score:.3f}{hit}@({cx},{cy})")
    return " | ".join(parts)


def _print_miss_hint(image_path: str, region, confidence: float) -> None:
    """探测未命中时补一行"最高分 @ 位置"，用来区分"图标不在这"和"模板对不上"。

    只在**失败**路径上多截一帧，成功路径零额外开销。纯诊断：任何异常都只提示，
    绝不打断战斗流程 —— 查问题的手段本身不该变成新的故障源。

    为什么加它（2026-09-17）：真机上"自动战斗已开启"这一项曾因为差 0.185 被阈值挡住，
    而日志里只有一句"超时未出现"，看不到它究竟差多少，只能事后翻历史帧反推。
    现在失败当场就能看到分数与位置。
    """
    try:
        print(f"   · 未命中诊断（阈值 {confidence:.2f}）："
              f"{describe_matches([image_path], region, confidence)}")
    except Exception as e:
        print(f"   · 未命中诊断失败（可忽略，不影响战斗）: {type(e).__name__}: {e}")


# 本进程内最后一次"实测确认"的自动战斗状态。战斗界面 UI 会淡出，淡出后按钮变成
# 半透明（两个模板分数都掉到 0.5 左右，互相只差 0.03，任何阈值都分不开），
# 于是后续场次永远探不到 —— 这时沿用本进程内已经确认过的结论，比报"未确认"准确。
# 只对自动战斗做沿用：倍速 tag 是常亮状态指示，且倍速档位每场可能被游戏重置，
# 沿用旧的倍速结论会漏点（真机上出现过第二场又从 x1 开始）。
_auto_battle_memo: BattleState | None = None


def reset_battle_ui_memo() -> None:
    """清掉"沿用"用的记忆（换账号/换模式/重新开一轮刷图时调一次）。

    另外它还给回归测试用：不清的话，前面的用例会把状态留给后面的用例。
    """
    global _auto_battle_memo
    _auto_battle_memo = None


def click_pos(x: int, y: int, duration: float = DEFAULT_DURATION) -> None:
    """
    安全点击坐标
    :param x: 模版图像 x 坐标
    :param y: 模版图像 y 坐标
    :param duration: 鼠标移动持续时间
    """
    pyautogui.moveTo(x, y, duration=duration)
    pyautogui.click()


def _poll(image_path: str, region, confidence: float, timeout: float, interval: float):
    """
    轮询定位模板，返回坐标元组或 None；集中处理异常策略：
    - 模板缺失（FileNotFoundError）：立即上抛，别空等
    - 其他异常：打印首次详情，连续出现 MAX_CONSECUTIVE_ERRORS 次才上抛
    """
    start_time = time.time()
    errors = 0
    while time.time() - start_time < timeout:
        try:
            loc = locate_on_screen(image_path, region=region, confidence=confidence)
            if loc:
                return loc
            errors = 0             # 恢复成功后清零计数
        except FileNotFoundError:
            raise
        except Exception as e:
            errors += 1
            if errors == 1:
                print(f"⚠️ 首次异常（可忽略）: {type(e).__name__}: {e}")
            if errors >= MAX_CONSECUTIVE_ERRORS:
                raise              # 连续失败多为环境/代码问题，直接上抛而不是静默超时
        time.sleep(interval)
    return None


def wait_any_image(image_paths, region: tuple[int, int, int, int] | None = None,
                   confidence: float = DEFAULT_CONFIDENCE,
                   timeout: float = DEFAULT_TIMEOUT,
                   interval: float = DEFAULT_INTERVAL) -> str | None:
    """轮询直到列表里任意一个模板出现，返回命中的模板路径；超时返回 None。

    用途是"等界面出现"：战斗界面上的图标有好几个（自动战斗开/关、x1/x2），
    不管先出现哪一个都说明面板已经起来了，这里一次截屏全部匹配，命中即返回。
    异常处理策略与 _poll 一致（模板缺失立即上抛，连续异常到阈值上抛）。
    """
    start_time = time.time()
    errors = 0
    while time.time() - start_time < timeout:
        try:
            found = locate_many_on_screen(image_paths, region=region, confidence=confidence)
            for path, match in found.items():
                if match is not None:
                    return path
            errors = 0
        except FileNotFoundError:
            raise
        except Exception as e:
            errors += 1
            if errors == 1:
                print(f"⚠️ 首次异常（可忽略）: {type(e).__name__}: {e}")
            if errors >= MAX_CONSECUTIVE_ERRORS:
                raise
        time.sleep(interval)
    return None


def wait_and_click_image(image_path: str, region=None, confidence: float = DEFAULT_CONFIDENCE,
                         timeout: float = DEFAULT_TIMEOUT, desc: str = "",
                         interval: float = DEFAULT_INTERVAL,
                         duration: float = DEFAULT_DURATION) -> bool:
    """
    等待图片出现并点击其中心（或偏移位置）
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :param timeout: 超时秒数
    :param desc: 描述文字（用于日志）
    :param interval: 每次搜索间隔秒数
    :param duration: 鼠标移动时间；调用处可传 0.05 提速
    :return: True 点击成功，False 超时未找到
    """
    loc = _poll(image_path, region, confidence, timeout, interval)
    if not loc:
        print(f"⚠️ 超时未找到 {desc} 图片: {image_path}")
        return False
    left, top, width, height = loc
    click_pos(left + width // 2, top + height // 2, duration=duration)
    print(f"✅ 检测到 {desc} 并点击")
    return True


def wait_image(image_path: str, region=None, confidence: float = DEFAULT_CONFIDENCE,
               timeout: float = DEFAULT_TIMEOUT, interval: float = DEFAULT_INTERVAL,
               desc: str = "") -> tuple[int, int] | None:
    """
    检测图片是否出现，不点击。返回中心坐标或 None
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :param timeout: 超时秒数
    :param desc: 描述文字（用于日志）
    :param interval: 每次搜索间隔秒数
    :return: 图片中心坐标 (cx, cy)；超时返回 None
    """
    loc = _poll(image_path, region, confidence, timeout, interval)
    if not loc:
        print(f"⚠️ 超时未出现 {desc} 图片: {image_path}")
        return None
    left, top, width, height = loc
    cx, cy = left + width // 2, top + height // 2
    print(f"✅ 检测到 {desc}")
    return cx, cy


# ================= 运行时环境自检 =================

def ensure_utf8_stdout() -> None:
    """把 stdout/stderr 切到 UTF-8。

    中文 Windows 控制台默认 cp936，而各脚本的日志里有 ❌/✅/⚠️ 这类字符，
    cp936 无法编码会直接抛 UnicodeEncodeError 把脚本打断（实测
    '❌'.encode('cp936') 即报错）。经 GUI 启动时父进程已设 PYTHONIOENCODING=utf-8，
    但双击或在 cmd 里直接运行时没有这层保护，所以各脚本 __main__ 里调一次。
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8")
        except Exception:
            pass


def check_screen_size(expected: tuple[int, int] = (1920, 1080)) -> bool:
    """校验主屏分辨率是否与脚本坐标标定的一致，不一致时告警（不中断）。

    所有点击坐标都按 1920x1080 硬编码，换分辨率或改系统缩放会整体点偏。
    这里只做提示，避免用户以为是脚本坏了。

    :return: True 表示一致
    """
    try:
        mon = _sct.monitors[1]
    except Exception as e:
        print(f"⚠️ 无法读取屏幕分辨率: {type(e).__name__}: {e}")
        return False
    actual = (mon["width"], mon["height"])
    if actual == tuple(expected):
        return True
    print(f"⚠️ 当前主屏 {actual[0]}x{actual[1]}，但脚本坐标按 "
          f"{expected[0]}x{expected[1]} 标定，点击位置可能整体偏移！")
    return False





class BattleState(Enum):
    """自动战斗开关的探测结果（四态）。

    这里用 bool 表达不了：返回 False 既可能是"本来就开着"（正常，不该告警），
    也可能是"界面不在这 / 被遮挡"（异常，该告警），上层无法据此决策。
    """

    ENABLED = "enabled"                  # 探到未开启态 → 已点击开启（传了 on_image_path 时还做了复核）
    ALREADY_ENABLED = "already_enabled"  # 探到已开启态，无需动作
    REUSED = "reused"                    # 本场没探到按钮（战斗界面淡出/被遮挡），沿用本进程内已确认的状态
    UNKNOWN = "unknown"                  # 两种图标都没探到，且本进程内没有可沿用的结论

    @property
    def ok(self) -> bool:
        """最终是否确认处于"已开启"状态（UNKNOWN 无法确认，算 False；REUSED 按已开启算）"""
        return self is not BattleState.UNKNOWN

    @property
    def confirmed(self) -> bool:
        """本场是否真的探到了图标（REUSED 只是沿用推断，不算本场确认）"""
        return self in (BattleState.ENABLED, BattleState.ALREADY_ENABLED)

    @property
    def desc(self) -> str:
        """给日志用的中文说明"""
        return {
            BattleState.ENABLED: "已开启（本次点击开启）",
            BattleState.ALREADY_ENABLED: "已开启（本来就开着，无需点击）",
            BattleState.REUSED: "已开启（沿用本进程内已确认的状态；本场未探到按钮）",
            BattleState.UNKNOWN: "未确认（未探到图标：界面不在这 / 被遮挡）",
        }[self]


class SpeedState(Enum):
    """倍速开关的探测结果（三态），与 BattleState 对称。"""

    CHANGED = "changed"            # 探到低倍速图标 → 已点击切换
    ALREADY_FAST = "already_fast"  # 探到目标倍速图标，无需动作
    UNKNOWN = "unknown"            # 都没探到，或点击后复核失败

    @property
    def ok(self) -> bool:
        return self is not SpeedState.UNKNOWN

    @property
    def desc(self) -> str:
        """给日志用的中文说明"""
        return {
            SpeedState.CHANGED: "已切换（本次点击切到高倍速）",
            SpeedState.ALREADY_FAST: "已就绪（本来就是高倍速，无需点击）",
            SpeedState.UNKNOWN: "未确认（未探到倍速图标 / 点击后复核失败）",
        }[self]


@dataclass(frozen=True)
class BattleUiReport:
    """一场战斗开始时的战斗界面状态快照（自动战斗 + 倍速）"""

    auto: BattleState
    speed: SpeedState

    @property
    def ok(self) -> bool:
        """两项都已确认就绪（UNKNOWN 不算就绪）"""
        return self.auto.ok and self.speed.ok

    def print_status(self, desc: str = "") -> None:
        """按检测结果打印状态（战斗开始时调用一次，战斗中不再打印）"""
        title = "战斗界面状态" + (f"｜{desc}" if desc else "") + "（本场仅检测一次）"
        print("------ " + title + " ------")
        print(f"  自动战斗：{self.auto.desc}")
        print(f"  倍速    ：{self.speed.desc}")
        print("  结论    ：" + ("✅ 自动战斗与倍速均已就绪"
                               if self.ok else "⚠️ 有未确认项，见上（不影响继续战斗）"))


def ensure_auto_battle(image_path_1: str,
                       region: tuple[int, int, int, int] | None = None,
                       *,
                       on_image_path: str | None = None,
                       confidence: float = DEFAULT_CONFIDENCE,
                       probe_timeout: float = SHORT_PROBE_TIMEOUT,
                       verify_timeout: float = CLICK_VERIFY_TIMEOUT,
                       interval: float = DEFAULT_INTERVAL,
                       duration: float = DEFAULT_DURATION,
                       diagnose_miss: bool = False) -> BattleState:
    """开启自动战斗（幂等：已经开了就不重复点）

    :param image_path_1: 未开启态的按钮模板
    :param region: 搜索区域 (left, top, width, height)
    :param on_image_path: 可选，"已开启态"的图标模板。传了它才能区分
        "本来就开着"和"界面不在这"；不传则两种情况都归为 UNKNOWN
    :param probe_timeout: 探测窗口（秒）。这里问的是"图标现在在不在"，
        必须用短超时；传 DEFAULT_TIMEOUT(60) 会在已开启时空等一分钟
    :param verify_timeout: 点击后等"状态真的变了"的窗口。**别用 probe_timeout 顶替**：
        真机上点完一帧内按钮还没切过来，0.6s 的窗口会天天误报复核失败
    :param diagnose_miss: 某一步没探到时，补打一行该模板的最高分 @ 位置（多截一帧）。
        真机调阈值时需要它，默认关（auto_battle_with_speed 会打开）
    :return: BattleState 三态，见类文档
    """
    # 1) 给了"已开启态"模板就先看它：命中即无需任何动作，幂等的最优路径
    if on_image_path:
        if wait_image(on_image_path, region=region, confidence=confidence,
                      timeout=probe_timeout, interval=interval,
                      desc="自动战斗(已开启)"):
            print("→ 自动战斗已处于开启状态，跳过点击")
            return BattleState.ALREADY_ENABLED
        if diagnose_miss:
            # 这一步没命中就是"当前不是已开启态"，它的分数是判断"阈值松紧"的关键证据
            _print_miss_hint(on_image_path, region, confidence)

    # 2) 探"未开启态"，命中则点开
    center = wait_image(image_path_1, region=region, confidence=confidence,
                        timeout=probe_timeout, interval=interval,
                        desc="自动战斗(未开启)")
    if center is None:
        if diagnose_miss:
            _print_miss_hint(image_path_1, region, confidence)
        if on_image_path:
            print("→ 两种图标都未探到，界面可能不在自动战斗面板，或已被窗口遮挡")
        else:
            print("→ 未检测到未开启图标（可能本来就开着，也可能界面不在这）；"
                  "要区分请传 on_image_path")
        return BattleState.UNKNOWN

    # 复用刚定位到的中心点点击，省掉一次"重新截图 + 重新匹配"
    # （若该面板有滑入动画、坐标会漂，则退化为
    #   wait_and_click_image(image_path_1, region=region, timeout=probe_timeout)）
    click_pos(center[0], center[1], duration=duration)
    print("✅ 检测到 自动战斗按钮 并点击")

    # 3) 给了"已开启态"模板就复核一下，把"点过了"升级为"确认开上了"
    if not on_image_path:
        return BattleState.ENABLED
    if wait_image(on_image_path, region=region, confidence=confidence,
                  timeout=verify_timeout, interval=interval,      # 复核用长窗口
                  desc="自动战斗(开启复核)"):
        return BattleState.ENABLED
    print("⚠️ 点击后仍未探到已开启态图标：点击可能未生效，或界面被遮挡")
    if diagnose_miss:
        _print_miss_hint(on_image_path, region, confidence)
    return BattleState.UNKNOWN


def set_speed(slow_image_path: str,
              region: tuple[int, int, int, int] | None = None,
              *,
              fast_image_path: str | None = None,
              confidence: float = DEFAULT_CONFIDENCE,
              state_confidence: float = SPEED_STATE_CONFIDENCE,
              probe_timeout: float = SHORT_PROBE_TIMEOUT,
              verify_timeout: float = CLICK_VERIFY_TIMEOUT,
              interval: float = DEFAULT_INTERVAL,
              duration: float = DEFAULT_DURATION,
              diagnose_miss: bool = False) -> SpeedState:
    """把倍速切到更高档（幂等：已经是高倍速就不重复点）

    :param slow_image_path: 低倍速图标模板（如 x1）—— 看到它说明还没加速
    :param region: 搜索区域 (left, top, width, height)
    :param fast_image_path: 可选，目标倍速图标模板（如 x2）。传了才能区分
        "本来就已经是高倍速"和"界面不在这"，并做点击后复核
    :param confidence: 点击后复核用的阈值（宽松，只求确认"变了"）
    :param state_confidence: 判定"当前是几倍"用的阈值（严格！x1/x2 模板长得太像，
        用宽松阈值会把 x1 认成 x2 从而漏点，见常量处注释）
    :param probe_timeout: 探测窗口（秒）。这里问的是"图标现在在不在"，必须用短超时
    :param verify_timeout: 点击后等"档位真的换了"的窗口，比探测窗口要长
    :param diagnose_miss: 某一步没探到时，补打一行该模板的最高分 @ 位置（多截一帧）。
        x1/x2 互相关高达 0.86，调阈值时特别需要看到实际分数，默认关
    :return: SpeedState 三态，见类文档
    """
    # 1) 给了目标倍速模板就先看它：命中即无需动作，幂等的最优路径（严格阈值判定档位）
    if fast_image_path:
        if wait_image(fast_image_path, region=region, confidence=state_confidence,
                      timeout=probe_timeout, interval=interval,
                      desc="倍速(已高倍速)"):
            print("→ 倍速已是目标档位，跳过点击")
            return SpeedState.ALREADY_FAST
        if diagnose_miss:
            _print_miss_hint(fast_image_path, region, state_confidence)

    # 2) 探低倍速图标，命中则点一下切档（同样用严格阈值，避免把高倍速认成低倍速去点）
    center = wait_image(slow_image_path, region=region, confidence=state_confidence,
                        timeout=probe_timeout, interval=interval,
                        desc="倍速(低倍速)")
    if center is None:
        if diagnose_miss:
            _print_miss_hint(slow_image_path, region, state_confidence)
        if fast_image_path:
            print("→ 两种倍速图标都未探到，界面可能不在战斗面板，或已被窗口遮挡")
        else:
            print("→ 未检测到低倍速图标（可能已是目标倍速，也可能界面不在这）；"
                  "要区分请传 fast_image_path")
        return SpeedState.UNKNOWN

    click_pos(center[0], center[1], duration=duration)
    print("✅ 检测到 倍速按钮 并点击")

    # 3) 给了目标倍速模板就复核一下，把"点过了"升级为"确认切过去了"
    if not fast_image_path:
        return SpeedState.CHANGED
    if wait_image(fast_image_path, region=region, confidence=confidence,
                  timeout=verify_timeout, interval=interval,      # 复核用长窗口
                  desc="倍速(切换复核)"):
        return SpeedState.CHANGED
    print("⚠️ 点击后仍未探到目标倍速图标：点击可能未生效，或界面被遮挡")
    if diagnose_miss:
        _print_miss_hint(fast_image_path, region, confidence)
    return SpeedState.UNKNOWN


def auto_battle_with_speed(auto_off_image: str, speed_slow_image: str,
                           region: tuple[int, int, int, int] | None = None,
                           *,
                           auto_on_image: str | None = None,
                           speed_fast_image: str | None = None,
                           confidence: float = DEFAULT_CONFIDENCE,
                           auto_confidence: float = AUTO_STATE_CONFIDENCE,
                           ready_timeout: float = BATTLE_UI_READY_TIMEOUT,
                           probe_timeout: float = QUICK_PROBE_TIMEOUT,
                           verify_timeout: float = CLICK_VERIFY_TIMEOUT,
                           interval: float = DEFAULT_INTERVAL,
                           duration: float = DEFAULT_DURATION,
                           diagnose: bool = True,
                           diagnose_miss: bool = True,
                           reuse_last: bool = True,
                           desc: str = "") -> BattleUiReport:
    """★ 战斗开始时调用一次：检测自动战斗与倍速是否已开启，该开就开，并打印状态。

    一场战斗只调用一次（放在"战斗刚开始、战斗界面刚出现"的位置）：检测一次定论，
    整场战斗过程中不再重复检测 —— 不要放进战斗循环里反复调用。

    :param auto_off_image: 自动战斗"未开启态"图标（绿色"自动"）
    :param speed_slow_image: 低倍速图标（如 x1），看到它说明还没加速
    :param region: 搜索区域 (left, top, width, height)，None = 全屏
    :param auto_on_image: 可选，自动战斗"已开启态"图标（红色"取消"）。
        传了才能区分"本来就开着"和"界面不在这"，并做点击后复核
    :param speed_fast_image: 可选，目标倍速图标（如 x2），作用同上
    :param confidence: 通用匹配阈值（倍速的状态判定仍额外用 SPEED_STATE_CONFIDENCE）
    :param auto_confidence: 自动战斗那一对的探测阈值，默认就是 AUTO_STATE_CONFIDENCE，
        **比 confidence 松**。别改回 0.80：真机上探测时刻按钮是半透明态，auto_enable
        只有 0.615，用 0.80 会把"已经开着"一路判成未确认（见常量处实测记录）
    :param ready_timeout: 等战斗界面图标出现的总窗口（秒）；只等面板出现，不等整场战斗
    :param probe_timeout: 面板已确认出现后，单个图标的探测窗口。默认 QUICK_PROBE_TIMEOUT，
        因为这时问的是"图标在不在"，不是"等它出现"，多等只是白费时间
    :param verify_timeout: 点击后等状态变化的窗口，别跟着 probe_timeout 一起收
    :param diagnose: 有未确认项时，额外打印一行"各模板最高分 @ 位置"用于定位问题
        （只写控制台，不落盘任何截图）
    :param diagnose_miss: 单步没命中时，就地为该模板补打一行"最高分 @ 位置"（多截一帧，
        只在失败路径上发生，约 10~30ms）。默认开着：整套未确认的诊断只在最后打一次，
        看不到"哪一步差多少"，而真机调阈值靠的就是这个数
    :param reuse_last: 本场没探到自动战斗按钮时，是否沿用本进程内已确认过的状态
        （战斗界面 UI 会淡出，后续场次按钮变半透明、两个模板分数都掉到 0.5 左右，
        任何阈值都分不开；此时沿用比报"未确认"准确）。倍速不做沿用，见模块内注释。
    :param desc: 打印状态时带的说明（如"第 3 轮"）
    :return: BattleUiReport（auto / speed 各为多态；.ok 表示两项都已确认就绪）
    """
    global _auto_battle_memo
    templates = [auto_off_image, speed_slow_image]
    for path in (auto_on_image, speed_fast_image):
        if path:
            templates.append(path)

    def _report(out: BattleUiReport) -> BattleUiReport:
        """统一的"打印状态 + 未确认时给证据"收尾"""
        out.print_status(desc)
        if not out.ok and diagnose:
            # 分数极低说明图标不在这块区域；贴着阈值说明模板对不上
            # 阈值按模板分开给：自动战斗那对用 auto_confidence，倍速那对用倍速的严格阈值，
            # 这样诊断行里的 ✓ 才等于"这一项真的会被判读出来"
            thresholds = {auto_off_image: auto_confidence,
                          speed_slow_image: SPEED_STATE_CONFIDENCE}
            for path in (auto_on_image, speed_fast_image):
                if path:
                    thresholds[path] = (auto_confidence if path is auto_on_image
                                        else SPEED_STATE_CONFIDENCE)
            try:
                print(f"   · 匹配诊断（阈值 自动{auto_confidence:.2f}/倍速"
                      f"{SPEED_STATE_CONFIDENCE:.2f}，区域 {region}）："
                      f"{describe_matches(templates, region, thresholds)}")
            except Exception as e:      # 纯诊断，失败也不能把战斗打断（同 _print_miss_hint）
                print(f"   · 匹配诊断失败（可忽略，不影响战斗）: {type(e).__name__}: {e}")
        return out

    # 0) 先等战斗界面起来：这几个图标不管先出现哪一个，都说明面板已经在了
    #    （一次截屏同时匹配全部模板，命中即返回）
    if wait_any_image(templates, region=region, confidence=confidence,
                      timeout=ready_timeout, interval=interval) is None:
        print(f"⚠️ {ready_timeout:.1f}s 内未探到任何战斗界面图标，本次跳过检测"
              "（界面没起来 / 被窗口遮挡）")
        return _report(BattleUiReport(auto=BattleState.UNKNOWN, speed=SpeedState.UNKNOWN))

    # 1) 面板已就绪：下面两步都是幂等的短探测，命中即返回，不会重复点
    auto = ensure_auto_battle(auto_off_image, region=region,
                              on_image_path=auto_on_image, confidence=auto_confidence,
                              probe_timeout=probe_timeout, verify_timeout=verify_timeout,
                              interval=interval, duration=duration,
                              diagnose_miss=diagnose_miss)
    if auto.confirmed:
        _auto_battle_memo = auto          # 记住本进程内"实测确认"过的状态，供后续场次沿用
    elif reuse_last and _auto_battle_memo is not None:
        print(f"→ 本场未探到自动战斗按钮（界面淡出 / 被遮挡），"
              f"沿用本进程内已确认的状态：{_auto_battle_memo.desc}")
        auto = BattleState.REUSED
    if auto is BattleState.ENABLED:
        time.sleep(CLICK_SETTLE_DELAY)   # 刚点过自动战斗，等界面响应再点倍速，避免互相干扰

    speed = set_speed(speed_slow_image, region=region,
                      fast_image_path=speed_fast_image, confidence=confidence,
                      state_confidence=SPEED_STATE_CONFIDENCE,
                      probe_timeout=probe_timeout, verify_timeout=verify_timeout,
                      interval=interval, duration=duration,
                      diagnose_miss=diagnose_miss)

    return _report(BattleUiReport(auto=auto, speed=speed))

