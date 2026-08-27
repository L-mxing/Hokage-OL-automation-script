"""
图像识别与点击的公共工具模块
Opencv + pyautogui 实现，供各脚本调用
注：此模块不依赖任何游戏逻辑，单纯提供图像识别
"""
import time

import cv2
import numpy as np
import pyautogui
from PIL import ImageGrab

# 默认参数（各脚本需要不同值时，在调用处传参覆盖）
DEFAULT_CONFIDENCE = 0.8 # 匹配精度
DEFAULT_TIMEOUT = 30  # 等待图片出现的最长时间（秒）
DEFAULT_INTERVAL = 0.5 # 每次检查间隔
DEFAULT_DURATION = 0.3 # 鼠标移动持续时间

_template_cache = {}


def _load_template(image_path):
    """
    读取模板并转灰度（带缓存）；np.fromfile + imdecode 兼容中文路径
    :param image_path:  模板图片路径
    :return: 灰度模板图像
    """
    if image_path not in _template_cache:
        data = np.fromfile(image_path, dtype=np.uint8)
        template = cv2.imdecode(data, cv2.IMREAD_GRAYSCALE)
        if template is None:
            raise FileNotFoundError(f"模板图片加载失败: {image_path}")
        _template_cache[image_path] = template
    return _template_cache[image_path]


def locate_on_screen(image_path, region=None, confidence=DEFAULT_CONFIDENCE):
    """
    OpenCV 版图像定位（替代 pyautogui.locateOnScreen）
    返回 (left, top, width, height) 屏幕绝对坐标；未找到返回 None
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :return: 返回灰度图坐标(left, top, width, height) 或 None
    """
    template = _load_template(image_path)
    th, tw = template.shape[:2]

    offset_x = offset_y = 0
    if region:
        left, top, width, height = region
        bbox = (left, top, left + width, top + height)
        offset_x, offset_y = left, top
        if tw > width or th > height:
            return None
    else:
        bbox = None

    frame = cv2.cvtColor(np.array(ImageGrab.grab(bbox=bbox)), cv2.COLOR_RGB2GRAY)
    result = cv2.matchTemplate(frame, template, cv2.TM_CCOEFF_NORMED)
    _, max_val, _, max_loc = cv2.minMaxLoc(result)

    if max_val >= confidence:
        return (max_loc[0] + offset_x, max_loc[1] + offset_y, tw, th)
    return None


def click_pos(x, y, duration=DEFAULT_DURATION):
    """
    安全点击坐标
    :param x: 模版图像 x 坐标
    :param y: 模版图像 y 坐标
    :param duration: 鼠标移动持续时间
    """
    pyautogui.moveTo(x, y, duration=duration)
    pyautogui.click()


def wait_and_click_image(image_path, region=None, confidence=DEFAULT_CONFIDENCE,
                         timeout=DEFAULT_TIMEOUT, desc="", interval=DEFAULT_INTERVAL):
    """
    等待图片出现并点击其中心（或偏移位置）
    :param image_path: 模板图片路径
    :param region: 搜索区域 (left, top, width, height)
    :param confidence: 匹配精度
    :param timeout: 超时秒数
    :param desc: 描述文字（用于日志）
    :param interval: 每次搜索间隔秒数
    :return: True 点击成功，False 超时未找到
    """
    start_time = time.time()
    while time.time() - start_time < timeout:
        try:
            location = locate_on_screen(image_path, region=region, confidence=confidence)
            if location:
                print(f"✅ 检测到 {desc} 并点击")
                left, top, width, height = location
                click_pos(left + width // 2, top + height // 2)
                return True
        except FileNotFoundError:
            raise
        except Exception:
            pass
        time.sleep(interval)
    print(f"⚠️ 超时未找到 {desc} 图片: {image_path}")
    return False
