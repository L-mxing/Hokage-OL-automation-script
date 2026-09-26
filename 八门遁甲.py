import os
import sys
import time
from enum import Enum

import mss

from screenshot_utils import (locate_on_screen, wait_and_click_image, click_pos,
                              count_matches, describe_matches,
                              ensure_utf8_stdout, check_screen_size)


# ================= 配置区域 =================
# 图片路径
IMG_SUMMON = "image/BM/BM_01_summon.png"                 # 点击召唤
IMG_EMPTY_SLOT = "image/BM/BM_02_last_slot.png"          # 空格子图标（槽位没被物品占用）
# ★ 一键合成后可能弹出的"确认吞噬"框：**只在遇到"低级吞噬高级"时才会弹**（用户 2026-09-22 确认）。
#   它不是必经步骤，而是游戏的一道保护 —— 点下去等于同意吃掉一个高级物品。
#   用户要求：让脚本自动点掉，不用手动确认（2026-09-22 21:48）。所以默认开着；
#   想改回"遇到就停下交给人"，把这个开关设成 False 即可。
AUTO_CONFIRM_SYNTHESIS = True
# 确认框上的【确定】按钮模板（95x29，从真机弹窗帧 screenshots/SC_02_reset1.png 切出）。
# ★ 更正（2026-09-22）：旧的 image/BM/BM_03_confirm_synthesis.png **没有失效** ——
#   它在这张弹窗帧上全屏匹配 1.000，只是那两张"未满/已满"帧里根本没有弹窗，
#   所以匹配不到（0.307）＝"画面里没有它"，不是"模板坏了"。当时是我读错了。
#   现在换成这张完整的按钮（旧的是按钮内部的一小块），两者都能用。
IMG_CONFIRM_SYNTHESIS = "image/BM/BM_04_confirm_synthesis.png"
# ★ 真机已闭环（2026-09-22 22:59:22，用户日志）：
#     [22:59:21] 检测到没有空位（图像=满, 颜色=满），执行一键合成
#     [22:59:22] 空位没恢复 —— 按"低级吞噬高级"的确认框处理
#     [22:59:22] ✅ 检测到确认框（匹配 1.000），点它的【确定】(874, 581)
#     [22:59:23] ✅ 点了确认框之后空位恢复
#   ⇒ 实时渲染与模板**完全一致（1.000）**，命中的正是实测的按钮中心，点完 1 秒内空位恢复。
#     也确认了 SYNTHESIS_QUICK_TIMEOUT=1.5 这条"先快等、没恢复才处理弹框"的路径成立。
# 实测：弹窗帧区域内 1.000（命中）@(874,581)；两张无弹窗帧区域内都只有 0.184；
#       全屏最高也只有 0.33 —— 阈值 0.8 两边余量都很大。
REGION_CONFIRM_SYNTHESIS = (780, 540, 260, 80)
CONFIRM_CONFIDENCE = 0.8
# 兜底坐标：万一模板没匹配上（游戏改版 / 渲染差异），就直接点这里。
# 实测这个点就是【确定】按钮的几何中心（按钮本体 x 827..921、y 567..595，中心 (874,581)），
# 而且"没弹窗"时那一带是面板空白处（两张真机帧实测），所以点错也不会有副作用。
# 截至 2026-09-22 的真机记录：模板每次都命中，**兜底坐标还没被真正用到过**。
POS_CONFIRM_SYNTHESIS = (874, 581)

# 范围
REGION_SUMMON = (543, 460, 847, 63)  # 点击召唤
# 全部 20 个格子。实测格子中心 x = 534 + 95k (k=0..9)、y = 629/736，模板 32x30，
# 区域取 x 515..1410 / y 610..755 刚好把 20 格全包住。
REGION_SLOT_GRID = (515, 610, 895, 145)

# 固定坐标
POS_SYNTHESIS = (1369, 541)          # 一键合成（实测红框正压在按钮的"合"字上）

# 点击速度：调用处直接传参（不动 screenshot_utils 默认值）
CLICK_DURATION = 0.05                # 0.05 秒 ≈ 瞬移

# 单点颜色快速检测（mss 抓固定 1x1 区域，媲美按键精灵 IfColor）
# ⚠️ 实测 1x1 取色 6.06ms / 847x63 截图 6.08ms —— 每次 grab 的固定开销主导，
#    取色**并不比截图快**。留着它是因为它够便宜（6ms）且能提供第二个信号，
#    不是因为快。
PIXEL_BAG_CHECK = (1389, 736)        # 右下角那一格的中心（20 格里的最后一格）
# 参考色 = 背包**未满**时该点的颜色（即"空格子"图标的中心色）。
# 语义：颜色**贴近**参考色 → 未满；**偏离**超过容差 → 已满。
# 命名故意带 NOT_FULL —— 存的是"未满"的颜色，别按名字理解成"满的颜色"。
# 实测（2026-09-22，screenshots/BM_full_notfull.png 与 BM_full_full.png）：
#   未满 (57,34,19)   已满 (255,215,12)   逐通道差 198/181/7
#   未满帧 20 个空格子中心色全部是 (57,34,19)，零抖动
#   已满帧 20 个占用格中心色两两不同，但最小的逐通道差也有 130+，没有一格贴着参考色
# 标定方法：python 八门遁甲.py --calibrate（两段式，会直接告诉你两者可不可分）
BAG_NOT_FULL_COLOR = (57, 34, 19)
COLOR_TOLERANCE = 30                 # 容差：颜色在此范围内视为"未满"

# 判定阈值与超时
# 召唤按钮的匹配阈值。数值就是 screenshot_utils 的 DEFAULT_CONFIDENCE（0.8），
# 显式写出来 + 显式传参，是为了让本脚本用到的每个阈值都一目了然：
# 以前它隐式吃默认值，改 screenshot_utils 时会悄悄跟着变，出了问题也看不见（--check 会打印它）。
SUMMON_CONFIDENCE = 0.8
# 空格子模板的分数实测：未满帧区域内最高 1.000（20 个格子全中）；
# 已满帧区域内最高 0.499（一个都中不了）。阈值 0.8 落在中间，两边余量都 >0.2。
EMPTY_SLOT_CONFIDENCE = 0.8
# 数空位时的去重半径：实测格子间距 95px，所以中心相距 20px 以内算同一格。
EMPTY_SLOT_MIN_DISTANCE = 20
# 背包格子总数（实测 20 格：10 列 × 2 行）——只用于 --check 里报"还剩几格"。
# 关于"要召唤多少次才能填满"（2026-09-22 用户告知的游戏机制）：
#   每次召唤产出 1 个物品，但**灰色物品会自动售卖、不占槽位**，只有非灰色才占格。
#   所以填满 20 格需要的召唤次数 = (20 − 合成后剩余占用) ÷ (1 − 灰色出现率)，**必然大于 20 次**。
#   实测（22:38 那次真机日志）：一个"合成生效→再次满"周期约 **12.5 秒 / 32 次召唤**。
#   ⇒ 这条同时说明两件事：① 32 > 20 的差额是灰色被卖掉造成的，**不是点击被游戏吞**；
#     ② 脚本 ~2.6 次/秒的点速游戏完全跟得上，**不需要为了"点太快"去放慢节奏**。
SLOT_TOTAL = 20
# "没有空位"是**状态**、不是"等它出现"，所以不能像 wait_image 那样死等 3 秒
# （实测那 3 秒里只做了 15 次探测、每次 5.9ms，99.8% 是 sleep 空等，而且"已满"是正常
#  状态却会打印一行 ⚠️ 超时未出现）。改成隔一小会儿再探一次，避开格子重绘的中间态。
ABSENCE_CONFIRM_GAP = 0.25
# 点完一键合成先等一小会儿：真生效的话空位很快就回来（实测 20:59 那次 ≤2s）。
# 等不到就按"弹了确认框"处理 —— 那个坐标在没弹窗时是空白面板，点错也不会有副作用，
# 所以这里敢用短窗口，省掉每次合成都可能白等的几秒。
SYNTHESIS_QUICK_TIMEOUT = 1.5
# 点完确认按钮后，再等空位恢复的窗口。
SYNTHESIS_VERIFY_TIMEOUT = 5.0
# 注意：合成没生效时**不反复盲点**同一个按钮（再点也帮不上，还可能点到弹窗上的东西），
# 点一次确认就够，还不行就停下来交给人。

# 轮询与失败上限：原实现失败一次要等默认 60s、重试再 60s，且 while True 永不退出，
# 会一直空转（GUI 里排在其后的脚本也因此永远轮不到）。
FIRST_TIMEOUT = 10                   # 首次等待召唤按钮出现的超时（秒）
RETRY_TIMEOUT = 5                    # 重试等待超时（秒）
LOOP_INTERVAL = 0.3                  # 轮询间隔，兼顾速度与 UI 响应
MAX_CONSECUTIVE_FAILURES = 5         # 连续失败达到该次数即退出，避免无限空转


class Round(Enum):
    """一轮的结果 —— 决定主循环是继续、重试还是收工交给人。

    （沿用项目里 BattleState / SpeedState 的写法：bool 说不清"为什么失败"，
     而"找不到召唤按钮"和"合成卡在人工确认"该有完全不同的处置。）
    """
    OK = "ok"                            # 正常（含"还有空位、这轮不用合成"）
    NO_SUMMON = "no_summon"              # 找不到召唤按钮：游戏没开/界面不对，可以重试
    SYNTHESIS_STUCK = "synthesis_stuck"  # 合成了但空位没回来：多半等人工确认，必须停下

_sct = None                          # mss 实例，首次用色时才建（不在 import 期做副作用）
_calib_warned = False                # 取色不可用提示只打印一次，避免刷屏


def _screenshotter():
    """懒加载 mss 实例：import 期不建，避免"只管导入也占住一个屏幕句柄"。"""
    global _sct
    if _sct is None:
        _sct = mss.MSS()
    return _sct


def get_pixel_color(x, y):
    """
    :param x: 色点的 x 坐标
    :param y: 色点的 y 坐标
    抓取固定 1x1 区域取色，返回 (R, G, B)；失败返回 None
    """
    try:
        img = _screenshotter().grab({"left": x, "top": y, "width": 1, "height": 1})
        return img.pixel(0, 0)[:3]      # mss 的 pixel() 返回 RGB（raw 才是 BGRA）
    except Exception:
        return None


def is_bag_full_by_pixel():
    """颜色检测：贴近 BAG_NOT_FULL_COLOR → 未满；偏离超过容差 → 已满

    :return: True=已满 / False=未满 / None=未标定或取色失败
             返回 None 表示"颜色这条路不可用"，调用方应完全依赖图像识别，
             绝不能把它当成"未满"（原实现未标定时的占位值会导致恒判已满）。
    """
    if BAG_NOT_FULL_COLOR is None:
        return None                      # 未标定 → 不参与判定
    rgb = get_pixel_color(*PIXEL_BAG_CHECK)
    if rgb is None:
        return None                      # 取色失败 → 交给图像识别
    return not all(abs(c - b) <= COLOR_TOLERANCE
                   for c, b in zip(rgb, BAG_NOT_FULL_COLOR))


def has_empty_slot():
    """整格区里还有没有空格子图标（= 背包还有没有空位）"""
    return locate_on_screen(IMG_EMPTY_SLOT, region=REGION_SLOT_GRID,
                            confidence=EMPTY_SLOT_CONFIDENCE) is not None


def is_bag_full_by_image():
    """图像判定：整格区里一个空格子都找不到 → 已满。

    为什么扫"整个格区"而不是"最后一格"：原来的写法只看右下角那一格，隐含假设
    "物品一定按顺序填格"，一旦合成后物品前移、或填格顺序变了，就会提前判满。
    扫全格问的是"还有没有空位"，这才是这段代码真正关心的问题。
    为什么探两次：格子重绘的中间态可能让某一帧读不到图标，隔 0.25s 再确认一次。
    """
    if has_empty_slot():
        return False
    time.sleep(ABSENCE_CONFIRM_GAP)
    return not has_empty_slot()


def wait_empty_slot(timeout):
    """等空位重新出现（合成生效的判据），超时返回 False"""
    deadline = time.time() + timeout
    while time.time() < deadline:
        if has_empty_slot():
            return True
        time.sleep(0.2)
    return False


def synthesize():
    """点一键合成；万一弹了"确认吞噬"框就顺手点掉它。

    游戏逻辑（用户 2026-09-22 确认）：点一键合成后**只有遇到"低级吞噬高级"才会弹确认框**。
    所以正常路径是"点一下 → 空位回来 → 完事"，只有没回来时才按弹框处理。

    流程：
      1. 点一键合成 → 等 SYNTHESIS_QUICK_TIMEOUT，空位回来就成功（常态，最快）；
      2. 没回来 → 认为弹了确认框 → 先用模板找【确定】按钮（找到就点它的中心），
         模板没匹配上才退回兜底坐标；
      3. 还没有 → 停下交给人（不反复盲点，见下面的注释）。

    :return: True=合成确实生效了；False=没生效，主循环应当停下来交给人
    """
    click_pos(*POS_SYNTHESIS, duration=CLICK_DURATION)
    if wait_empty_slot(SYNTHESIS_QUICK_TIMEOUT):
        print("✅ 一键合成生效（空位已恢复）")
        return True

    if not AUTO_CONFIRM_SYNTHESIS:
        print(f"❌ 点击一键合成后 {SYNTHESIS_QUICK_TIMEOUT:.1f}s 空位没恢复 —— "
              f"大概率弹出了'低级吞噬高级'的确认框")
        print("   已按配置关掉自动确认（AUTO_CONFIRM_SYNTHESIS=False），先停在这里交给人")
        return False

    print("空位没恢复 —— 按'低级吞噬高级'的确认框处理")
    match = locate_on_screen(IMG_CONFIRM_SYNTHESIS, region=REGION_CONFIRM_SYNTHESIS,
                             confidence=CONFIRM_CONFIDENCE, return_score=True)
    if match is None:
        print(f"⚠️ 区域 {REGION_CONFIRM_SYNTHESIS} 里没匹配到确认框模板"
              f"（阈值 {CONFIRM_CONFIDENCE}），改用兜底坐标 {POS_CONFIRM_SYNTHESIS} 点一下")
        pos = POS_CONFIRM_SYNTHESIS
    else:
        pos = match.center
        print(f"✅ 检测到确认框（匹配 {match.score:.3f}），点它的【确定】{pos}")
    click_pos(*pos, duration=CLICK_DURATION)
    if wait_empty_slot(SYNTHESIS_VERIFY_TIMEOUT):
        print("✅ 点了确认框之后空位恢复")
        return True

    print(f"❌ 点了确认之后 {SYNTHESIS_VERIFY_TIMEOUT:.0f}s 空位依然没恢复 —— 停下交给人")
    print(f"   排查顺序：① 用截图工具量一下游戏里【确定】按钮的实际位置，"
          f"改 POS_CONFIRM_SYNTHESIS（当前 {POS_CONFIRM_SYNTHESIS}）；")
    print("             ② 看游戏画面是不是别的东西挡住了（比如其它弹窗/掉线提示）；")
    print("             ③ 如果画面本来就没有确认框，那是空位判定误报了，把截图发我。")
    return False


def do_synthesis():
    """点一次召唤；没有空位时执行一键合成。

    :return: Round.OK（正常）/ Round.NO_SUMMON（找不到召唤按钮）/ Round.SYNTHESIS_STUCK
             （合成了但空位没回来，多半在等人工确认，不该继续硬点）
    """
    # 1. 点击召唤（带一次重试；超时收紧到 FIRST_TIMEOUT/RETRY_TIMEOUT，别再用默认 60s）
    summon = wait_and_click_image(IMG_SUMMON, region=REGION_SUMMON,
                                  confidence=SUMMON_CONFIDENCE,
                                  desc='点击召唤按钮', interval=0.2,
                                  timeout=FIRST_TIMEOUT, duration=CLICK_DURATION)
    if not summon:
        print(f"未检测到点击召唤按钮，{RETRY_TIMEOUT}s 后重试一次")
        summon = wait_and_click_image(IMG_SUMMON, region=REGION_SUMMON,
                                      confidence=SUMMON_CONFIDENCE,
                                      desc='点击召唤按钮', interval=0.2,
                                      timeout=RETRY_TIMEOUT, duration=CLICK_DURATION)
        if not summon:
            print("检测召唤按钮失败，跳过本轮")
            return Round.NO_SUMMON
    # 注：召唤按钮刚被模板匹配确认过，说明八门遁甲面板此刻确实在、且没挪位置，
    #     所以下面那一下"点裸坐标 (1369,541)"是安全的（本函数内不会再等很久）。

    # 2. 判断有没有空位：图像识别为主，颜色快速检测为辅（两者取"或"，互为兜底）
    global _calib_warned
    by_image = is_bag_full_by_image()
    by_pixel = is_bag_full_by_pixel()
    if by_pixel is None and not _calib_warned:
        _calib_warned = True
        print("ℹ️ 颜色检测不可用（未标定或取色失败），当前仅依据图像识别判定")

    if not (by_image or by_pixel is True):
        return Round.OK                  # 还有空位，继续下一轮

    # 3. 没有空位 → 一键合成
    print(f"检测到没有空位（图像={'满' if by_image else '未满'}, "
          f"颜色={'不可用' if by_pixel is None else '满' if by_pixel else '未满'}），执行一键合成")
    return Round.OK if synthesize() else Round.SYNTHESIS_STUCK


def _sample_color(seconds, label):
    """采样 seconds 秒，返回 (主色, 直方图)；取不到色返回 (None, {})"""
    print(f"  【{label}】采样 {seconds}s ...")
    deadline = time.time() + seconds
    samples = {}
    while time.time() < deadline:
        rgb = get_pixel_color(*PIXEL_BAG_CHECK)
        if rgb is not None:
            samples[rgb] = samples.get(rgb, 0) + 1
        time.sleep(0.1)
    if not samples:
        print("  ❌ 取色失败：坐标可能越界，或屏幕不可访问")
        return None, {}
    total = sum(samples.values())
    top = sorted(samples.items(), key=lambda kv: -kv[1])[:5]
    print("  出现最多的颜色：" + "、".join(f"{rgb}×{n}" for rgb, n in top))
    best = max(samples, key=samples.get)
    print(f"  主色 = {best}（{samples[best]}/{total} 次）")
    return best, samples


def _pause(prompt, fallback_seconds=8):
    """等用户按回车；没有交互终端时退化成固定等待（GUI 拉起时没有 stdin）。"""
    try:
        input(prompt)
    except (EOFError, KeyboardInterrupt):
        print(f"  （无交互终端，{fallback_seconds}s 后自动继续）")
        time.sleep(fallback_seconds)


def run_calibration(seconds=5):
    """取色助手：标定 BAG_NOT_FULL_COLOR。

    两段式：先采"未满"、再采"已满"，直接告诉你这两者能不能分开 —— 只有一种状态的
    数据没法验证"容差 30 够不够"（旧版只能采一种，填错了也不报错，只会恒判满/恒判不满）。
    用法：python 八门遁甲.py --calibrate
    """
    print(f"取色点 {PIXEL_BAG_CHECK}（空格子图标的中心）")
    print("第 1 段：请把游戏停在【背包未满】的状态")
    _pause("      准备好后按回车开始采样…")
    not_full, s1 = _sample_color(seconds, "未满")
    if not_full is None:
        return

    print("\n第 2 段：请把背包弄成【已满】（一键召唤凑满即可）")
    _pause("      准备好后按回车开始采样…")
    full, _s2 = _sample_color(seconds, "已满")
    if full is None:
        return

    diff = [abs(a - b) for a, b in zip(not_full, full)]
    print(f"\n未满主色 {not_full}  vs  已满主色 {full}")
    print(f"逐通道差值 {diff}   （当前 COLOR_TOLERANCE = {COLOR_TOLERANCE}）")
    worst = min(diff)
    if worst > COLOR_TOLERANCE:
        print(f"✅ 可分辨：最接近的那个通道也差了 {worst} > 容差 {COLOR_TOLERANCE}")
    else:
        print(f"❌ 不可分辨：有通道只差 {worst} ≤ 容差 {COLOR_TOLERANCE}，"
              f"颜色判定会误判 —— 这个取色点不能用，换一个点再试")
    if len(s1) > 1:
        print(f"ℹ️ 未满段采到 {len(s1)} 种颜色，说明该点画面会变（直方图不集中），"
              f"建议换一个更稳的点")

    print("\n请把文件顶部的两行改成：")
    print(f"PIXEL_BAG_CHECK = {PIXEL_BAG_CHECK}")
    print(f"BAG_NOT_FULL_COLOR = {not_full}")


USAGE = """用法（用项目 venv 的 python 跑；从哪个目录调用都行，脚本会自己切回项目目录）：
  python 八门遁甲.py              正常挂机（GUI 里跑的就是这条）
  python 八门遁甲.py --check      只读自检：打印四项判定的分数与位置，不点鼠标——挂机前/排错时用
  python 八门遁甲.py --calibrate  取色标定：两段式采样"未满/已满"，给出可不可分的结论
  python 八门遁甲.py --help       显示这段说明

提示：--check 读的是屏幕像素，跑之前请把游戏窗口切到最前面、停在召唤界面（被挡住会读成"没有"）。
"""
# 注：下面 __main__ 里会先 chdir 到本文件所在目录 —— 模板路径都是相对路径（image/BM/*.png），
#     这样从任意目录调用都能跑对，不用先 cd 到项目里。


def run_check():
    """只读自检：把「脚本此刻看到的画面」逐项打出来，全程一个鼠标都不点。

    它解决的是"我看不到脚本眼里是什么"这个问题 —— 挂机脚本出怪事时，最难的往往是
    分不清"游戏变了"还是"判定错了"。这里把每一项的**分数和位置**都打出来，一眼定位。

    什么时候跑：
      · 挂机前  —— 确认界面、坐标、取色点都对得上再挂（30 秒的事）；
      · 改动后  —— 改过坐标/模板/阈值，立刻验证，不用等一整个"召满到合成"的周期；
      · 出怪事  —— 脚本卡住/误判时先跑它，看脚本到底"看到"了什么。
    用法：python 八门遁甲.py --check
    """
    print("=" * 62)
    print("八门遁甲 只读自检（不会点击、不会按键、不写文件）")
    print("=" * 62)

    print("\n[1] 召唤按钮 —— 靠它确认「面板在不在、位置对不对」")
    print(f"    {describe_matches([IMG_SUMMON], REGION_SUMMON, SUMMON_CONFIDENCE)}")
    print(f"    区域 {REGION_SUMMON}，阈值 {SUMMON_CONFIDENCE}")
    summon_hit = locate_on_screen(IMG_SUMMON, region=REGION_SUMMON,
                                  confidence=SUMMON_CONFIDENCE) is not None

    print("\n[2] 背包空位 —— 靠它决定要不要去点一键合成")
    print(f"    {describe_matches([IMG_EMPTY_SLOT], REGION_SLOT_GRID, EMPTY_SLOT_CONFIDENCE)}")
    print(f"    区域 {REGION_SLOT_GRID}，阈值 {EMPTY_SLOT_CONFIDENCE}")
    n_empty, _score = count_matches(IMG_EMPTY_SLOT, region=REGION_SLOT_GRID,
                                    confidence=EMPTY_SLOT_CONFIDENCE,
                                    min_distance=EMPTY_SLOT_MIN_DISTANCE)
    print(f"    → 空位 {n_empty} 个 / 共 {SLOT_TOTAL} 格"
          f"（占用 {max(0, SLOT_TOTAL - n_empty)} 格）"
          f"；判定：{'还有空位（继续召唤）' if n_empty else '没有空位（会去点一键合成）'}")

    print("\n[3] 取色点 —— 颜色那一路的读数")
    rgb = get_pixel_color(*PIXEL_BAG_CHECK)
    if rgb is None:
        print("    ❌ 取不到色（坐标越界，或屏幕不可访问）")
    elif BAG_NOT_FULL_COLOR is None:
        print(f"    点位 {PIXEL_BAG_CHECK}  RGB={rgb} —— 未标定，这一路是关的")
    else:
        diff = [abs(c - b) for c, b in zip(rgb, BAG_NOT_FULL_COLOR)]
        print(f"    点位 {PIXEL_BAG_CHECK}  RGB={rgb}")
        print(f"    参考色(未满) {BAG_NOT_FULL_COLOR}  逐通道差 {diff}  容差 {COLOR_TOLERANCE}")
        print(f"    → 判定：{'已满' if is_bag_full_by_pixel() else '未满'}")

    print("\n[4] 确认框 —— 只有'低级吞噬高级'时才会出现")
    print(f"    {describe_matches([IMG_CONFIRM_SYNTHESIS], REGION_CONFIRM_SYNTHESIS, CONFIRM_CONFIDENCE)}")
    _hit = locate_on_screen(IMG_CONFIRM_SYNTHESIS, region=REGION_CONFIRM_SYNTHESIS,
                            confidence=CONFIRM_CONFIDENCE)
    print(f"    区域 {REGION_CONFIRM_SYNTHESIS}，阈值 {CONFIRM_CONFIDENCE}")
    print(f"    → 判定：{'检测到确认框' if _hit else '没看到确认框（正常，它只在低级吞噬高级时弹）'}")

    print("\n[5] 综合：脚本此刻会怎么做")
    if not summon_hit:
        print("    ⚠️ 召唤按钮没匹配到 —— 要么当前不在八门遁甲界面、要么面板被别的窗口挡住了一部分。")
        print("       （匹配的是屏幕像素；被遮住就会读成「没有」。实测被压住一角时分数会掉到 0.76 左右）")
        print("       下面这几句只是「照着判定念」，真实流程里这种情况会直接跳过本轮，")
        print("       根本不会去点合成。要看真实判定，请把游戏切到最前面、停在召唤界面再跑一次。")
    by_image = is_bag_full_by_image()
    by_pixel = is_bag_full_by_pixel()
    _color_txt = '不可用' if by_pixel is None else ('满' if by_pixel else '未满')
    if not (by_image or by_pixel is True):
        print(f"    → 还有空位（图像判定未满）→ 点一次召唤，继续下一轮")
    else:
        print(f"    → 没有空位（图像={'满' if by_image else '未满'}, 颜色={_color_txt}）"
              f"→ 点一次一键合成 {POS_SYNTHESIS}")
        if AUTO_CONFIRM_SYNTHESIS:
            print(f"      随后等 {SYNTHESIS_QUICK_TIMEOUT}s：空位没回来就找确认框"
                  f"（{IMG_CONFIRM_SYNTHESIS}）→ 命中就点它中心，没命中就用兜底坐标 "
                  f"{POS_CONFIRM_SYNTHESIS}")
        else:
            print(f"      随后等 {SYNTHESIS_QUICK_TIMEOUT}s：空位没回来就停下交给人"
                  f"（AUTO_CONFIRM_SYNTHESIS=False）")

    print("\n" + "=" * 62)
    print("以上全是只读操作：没有移动鼠标、没有点击、没有按键。")


def main():
    """轮询合成。

    正常挂机时这个循环会一直跑（符合预期），但两种情况下会主动退出，而不是无限空转：
    · 连续 MAX_CONSECUTIVE_FAILURES 次找不到召唤按钮 → 游戏没开 / 界面不对；
    · 一键合成卡住（空位一直没恢复）→ 多半是"低级吞噬高级"的确认框在等人工确认，
      再点也帮不上忙，必须停下（否则后面的脚本会在带弹窗的画面上乱点）。
    """
    failures = 0
    while True:
        result = do_synthesis()
        if result is Round.OK:
            failures = 0
        elif result is Round.SYNTHESIS_STUCK:
            print("❌ 一键合成卡住：空位一直没恢复，再点也帮不上，主动停下")
            print("   （多半是'低级吞噬高级'的确认框；请人工确认或关掉弹窗后重启脚本，")
            print("     否则后面的脚本会在带着弹窗的画面上乱点）")
            return
        else:
            failures += 1
            print(f"⚠️ 连续失败 {failures}/{MAX_CONSECUTIVE_FAILURES}")
            if failures >= MAX_CONSECUTIVE_FAILURES:
                print(f"❌ 连续 {failures} 次失败，退出"
                      f"（请确认游戏已启动、当前界面为召唤界面）")
                return
        time.sleep(LOOP_INTERVAL)


if __name__ == "__main__":
    # 模板都是相对路径（image/BM/*.png），先切到脚本所在目录，
    # 这样"从哪个目录敲命令"都不会变成 FileNotFoundError。（GUI 用 Popen(cwd=...) 拉起，
    # 切到的是同一个目录，不受影响。）
    os.chdir(os.path.dirname(os.path.abspath(__file__)))
    ensure_utf8_stdout()
    check_screen_size()
    if "--help" in sys.argv or "-h" in sys.argv:
        print(USAGE)
    elif "--check" in sys.argv:
        run_check()                      # 只读自检，不点鼠标
    elif "--calibrate" in sys.argv:
        run_calibration()
    else:
        main()
