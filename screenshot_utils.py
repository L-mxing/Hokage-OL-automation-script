"""
图像识别与点击的公共工具模块
Opencv + pyautogui 实现，供各脚本调用
v3：mss 截图定位；轮询统一走 _poll 集中处理异常（连续失败即上抛，避免静默空等）；
    模板大于搜索区域时提前返回 None；locate_on_screen 可选返回匹配分数。
v4：pyautogui.PAUSE 由默认 0.1s 压到 0.01s（每次点击原本白等 0.2s）；
    新增 ensure_utf8_stdout() / check_screen_size()，供各脚本 __main__ 做环境自检。
"""
from __future__ import annotations # python版本3.10以上可不写

import os
import sys
import time
from dataclasses import dataclass

import cv2
import numpy as np
import pyautogui
import mss

__all__ = [
    "locate_on_screen",
    "click_pos",
    "wait_and_click_image",
    "wait_image",
    "ensure_utf8_stdout",
    "check_screen_size",
]

# 默认参数（各脚本需要不同值时，在调用处传参覆盖）
DEFAULT_CONFIDENCE = 0.8           # 匹配精度
DEFAULT_TIMEOUT = 60               # 等待图片出现的最长时间（秒）
DEFAULT_INTERVAL = 0.5             # 每次检查间隔
DEFAULT_DURATION = 0.3             # 鼠标移动持续时间
MAX_CONSECUTIVE_ERRORS = 5         # 轮询连续异常达到该次数即上抛（多为环境/代码问题）

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
