# -*- coding: utf-8 -*-
"""
文字识别函数 —— 屏幕截图 / 本地图片 → OCR 文字

依赖(已在 requirements.txt):
    mss, numpy, rapidocr_onnxruntime

用法:
    from ocr_utils import ocr_text, ocr_to_text

    ocr_text()                        # 识别整个主屏幕
    ocr_text(region=(0,150,400,300))  # 只识别屏幕区域 (left, top, width, height)
    ocr_text("screen.png")            # 识别本地图片文件
    ocr_to_text(region=(...))         # 便捷版:只返回拼好的纯文字串

返回结构:
    [(box, text, score), ...]
    box   = 4 顶点 [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],像素坐标(屏幕/图片内)
    text  = 识别出的文字
    score = 置信度 0~1
"""

from __future__ import annotations

import numpy as np
import mss
from rapidocr_onnxruntime import RapidOCR

_engine = None                 # OCR 引擎单例(首次调用才加载模型,较慢)
_sct = mss.MSS()               # 截屏实例,复用


def get_engine() -> RapidOCR:
    """惰性初始化 OCR 引擎(模型只加载一次)"""
    global _engine
    if _engine is None:
        _engine = RapidOCR()
    return _engine


def ocr_image(img: np.ndarray) -> list:
    """识别一张 BGR ndarray 图像 → [(box, text, score), ...]"""
    result, _ = get_engine()(img)
    if not result:
        return []
    return [(box, str(text).strip(), float(score)) for box, text, score in result]


def ocr_screen(region=None) -> list:
    """识别屏幕: region=(left, top, width, height);None = 主屏全屏。
    box 已还原为屏幕绝对坐标。"""
    if region:
        left, top, w, h = region
        mon = {"left": left, "top": top, "width": w, "height": h}
    else:
        left = top = 0
        mon = _sct.monitors[1]                 # 主显示器
    raw = _sct.grab(mon)
    img = np.array(raw)[:, :, :3].copy()       # BGRA -> BGR
    items = ocr_image(img)
    if region:                                  # 裁剪识别 → 坐标加回偏移
        items = [([[p[0] + left, p[1] + top] for p in box], t, s)
                 for box, t, s in items]
    return items


def ocr_file(path: str) -> list:
    """识别本地图片文件 → [(box, text, score), ...]"""
    import cv2
    img = cv2.imread(path)
    if img is None:
        raise FileNotFoundError(f"读图失败(路径或格式有误): {path}")
    return ocr_image(img)


def ocr_text(source=None, region=None) -> list:
    """统一入口:
        source 为图片路径(str)  → 识别该文件
        source 为 ndarray       → 直接识别该图
        source 为 None          → 识别屏幕(可传 region 限定区域)
    返回 [(box, text, score), ...]
    """
    if isinstance(source, str):
        return ocr_file(source)
    if isinstance(source, np.ndarray):
        return ocr_image(source)
    return ocr_screen(region)


def ocr_to_text(source=None, region=None) -> str:
    """便捷版:只返回拼好的纯文字串(每行一条)"""
    return "\n".join(t for _b, t, _s in ocr_text(source, region))


if __name__ == "__main__":
    import sys
    try:
        sys.stdout.reconfigure(encoding="utf-8")   # Windows 控制台 GBK -> UTF-8
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
    # 演示:python ocr_utils.py [图片路径]  不带参 = 识别全屏
    items = ocr_text(sys.argv[1]) if len(sys.argv) > 1 else ocr_text()
    print(f"识别到 {len(items)} 条文字:")
    for box, text, score in items:
        xs = [p[0] for p in box]
        ys = [p[1] for p in box]
        cx, cy = int(sum(xs) / 4), int(sum(ys) / 4)
        print(f"  中心({cx:>5},{cy:>5}) 置信{score:.2f} | {text}")
