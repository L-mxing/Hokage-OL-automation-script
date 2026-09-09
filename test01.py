import cv2
import mss
import mss.tools
import  numpy as np

with mss.MSS() as sct:
    # 设置捕获参数
    monitor = sct.monitors[1]  # 选择主显示器
    while True:
        sct_img = sct.grab(monitor)
        img=np.array(sct_img)
        img = img[:,:,:3]
        cv2.imshow('Screen Capture', img)
        if cv2.waitKey(1) & 0xFF == ord('q'):
            break
cv2.destroyAllWindows()



