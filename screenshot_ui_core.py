# -*- coding: utf-8 -*-
"""截图工具的**逻辑层** —— 不依赖 tkinter，可脱离界面单独测试。

`screenshot_ui.py` 只管控件、布局和显示；这里放三件事：
  1. 截图后端的加载（同目录的 `截图函数.py`）
  2. 文件名与保存路径的规则（时间戳命名、补扩展名、撞车让位、展开 ~）
  3. 坐标输入的解析（四个全空 = 全屏）

只依赖标准库。改规则时改这个文件，改界面时改 `screenshot_ui.py`。
"""

import os
from importlib import util as _importlib_util

# 本目录（`截图函数.py` 也在这里）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
BACKEND_PATH = os.path.join(BASE_DIR, "截图函数.py")

# 允许的图片扩展名；不在名单里就补 .png（避免造出 shot.b / shot. 这种名不副实的文件）
IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".bmp")

_capture_region = None


# ============================================================ 一、后端加载与截图执行
def ensure_backend():
    """加载同目录的 `截图函数.py`，返回其中的 capture_region（只加载一次）。

    用绝对路径加载，所以从任意工作目录启动都能跑。
    注意：spec_from_file_location() 对不存在的文件也会返回 spec（实测 spec 与 loader
    均非 None），因此必须自己先判断文件是否存在 —— 真正的报错点在 exec_module() 那一行。
    """
    global _capture_region
    if _capture_region is not None:
        return _capture_region

    if not os.path.exists(BACKEND_PATH):
        raise SystemExit(f"找不到截图模块：{BACKEND_PATH}（请确认它和本文件在同一目录）")

    spec = _importlib_util.spec_from_file_location("screenshot_func", BACKEND_PATH)
    module = _importlib_util.module_from_spec(spec)
    spec.loader.exec_module(module)
    _capture_region = module.capture_region
    return _capture_region


def capture_region(x1, y1, x2, y2, output_path):
    """薄转发到后端真正的 capture_region（首次调用时才加载后端）。"""
    return ensure_backend()(x1, y1, x2, y2, output_path)


# ============================================================ 二、文件名与路径规则
def generate_default_filename(now=None):
    """默认截图文件名，格式为 Clipboard_Screenshot.png。

    这里保持固定命名，后续重复截图由 dedupe_path() 负责自动加 _2 / _3 等后缀。
    """
    return "Clipboard_Screenshot.png"


def dedupe_path(path):
    """若该文件已存在，就在扩展名前加 _1 / _2 / _3 …（只用于自动生成的名字）。

    默认命名是 `Clipboard_Screenshot.png`，第一次冲突就变成 `Clipboard_Screenshot_1.png`。
    """
    if not os.path.exists(path):
        return path
    stem, ext = os.path.splitext(path)
    index = 1
    while os.path.exists(f"{stem}_{index}{ext}"):
        index += 1
    return f"{stem}_{index}{ext}"


def is_auto_name(file_name, auto_name):
    """判断这个名字是「自动生成的」还是「用户手输的」。

    手输的同名覆盖是用户的意图，调用方不该替他让位。
    """
    stripped = file_name.strip()
    return (not stripped) or (stripped == auto_name)


def should_advance_auto_name(current, auto_name):
    """截图成功后，文件名是否该前进到下一个时间戳。

    当前值仍是「上一次自动生成的名字」或为空 -> 该前进；
    用户手输过 -> 不动（他可能就是要覆盖同名文件）。
    """
    return is_auto_name(current, auto_name)


def build_output_path(output_dir, file_name, auto_name=""):
    """算出最终写到哪个文件。规则：

    - `output_dir` 为空 -> ValueError
    - `output_dir` 里的 `~` 会展开
    - `file_name` 留空 -> 用 generate_default_filename() 自动命名
    - 结尾的点不算扩展名；扩展名不在白名单 -> 补 `.png`
    - 自动名（见 is_auto_name）撞车 -> dedupe_path() 让位成 `_2`/`_3`
    - 手输名撞车 -> 原样返回（尊重用户主动覆盖）
    """
    output_dir = os.path.expanduser(output_dir.strip())
    if not output_dir:
        raise ValueError("保存目录不能为空。")

    auto_named = is_auto_name(file_name, auto_name)
    name = file_name.strip().rstrip(".")
    if not name:
        # 留空 = 自动命名（全屏和区域行为一致）
        name = generate_default_filename()
        auto_named = True

    if os.path.splitext(name)[1].lower() not in IMAGE_EXTENSIONS:
        name += ".png"

    path = os.path.join(output_dir, name)
    return dedupe_path(path) if auto_named else path


# ============================================================ 三、坐标输入解析
def parse_coordinates(x1_text, y1_text, x2_text, y2_text):
    """把四个输入框的文本解析成 (x1, y1, x2, y2)。

    四个全空 -> 返回 None，表示「截全屏」（屏幕尺寸由界面层提供，逻辑层不碰屏幕）。
    只填了一部分、或不是整数 -> ValueError。
    """
    texts = (x1_text, y1_text, x2_text, y2_text)
    if not any(t.strip() for t in texts):
        return None
    try:
        return tuple(int(t.strip()) for t in texts)
    except (TypeError, ValueError):
        raise ValueError("请输入完整且有效的坐标，或全部留空以截取全屏。")
