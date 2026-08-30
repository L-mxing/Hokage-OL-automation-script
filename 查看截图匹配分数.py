import cv2
import numpy as np
from PIL import ImageGrab

from screenshot_utils import _load_template

TPL = 'image/PW/PW_02_confirm.png'
REGION = (915, 649, 96, 26)

tpl = _load_template(TPL)
print(f"模板尺寸: {tpl.shape[::-1]}")

frame = cv2.cvtColor(np.array(ImageGrab.grab()), cv2.COLOR_RGB2GRAY)

# ① 全屏匹配：看最高分在哪
res = cv2.matchTemplate(frame, tpl, cv2.TM_CCOEFF_NORMED)
_, max_val, _, max_loc = cv2.minMaxLoc(res)
print(f"全屏最高分: {max_val:.3f} @ {max_loc}")

# ② region 内匹配：看脚本实际搜索的区域
x0, y0, w, h = REGION
sub = frame[y0:y0+h, x0:x0+w]
_, max2, _, loc2 = cv2.minMaxLoc(cv2.matchTemplate(sub, tpl, cv2.TM_CCOEFF_NORMED))
print(f"region内最高分: {max2:.3f} -> 绝对位置 {(loc2[0]+x0, loc2[1]+y0)}")
print(f"\n阈值0.8: {'通过' if max2 >= 0.8 else '不通过'} | 阈值0.7: {'通过' if max2 >= 0.7 else '不通过'}")
