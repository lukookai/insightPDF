

import requests
from PIL import Image
import numpy as np
import json
import os
import io
from configs import ocr_detect_api
# 配置API服务地址
API_URL = ocr_detect_api

def ocr_detect_to_json(image):
    """
    输入图片路径或PIL.Image对象，返回检测结果的json格式列表
    实现方式：调用本地API服务
    """
    if isinstance(image, str):
        img_path = image
        with open(img_path, "rb") as f:
            img_bytes = f.read()
        filename = os.path.basename(img_path)
    elif isinstance(image, Image.Image):
        img_path = getattr(image, "filename", None) or "unknown"
        buf = io.BytesIO()
        image.save(buf, format="PNG")
        img_bytes = buf.getvalue()
        filename = img_path
    else:
        raise ValueError("image参数必须为图片路径字符串或PIL.Image对象")

    files = {"file": (filename, img_bytes, "image/png")}
    try:
        resp = requests.post(API_URL, files=files, timeout=3000)
        resp.raise_for_status()
        result = resp.json()
        return result
    except Exception as e:
        print(f"OCR API调用失败: {e}")
        return []

# 示例用法1：传图片路径
if __name__ == "__main__":
    img_path = "t3.jpg"
    # json_result = ocr_detect_to_json(img_path)
    # print(json.dumps(json_result, ensure_ascii=False, indent=2))

    # 示例用法2：传PIL.Image对象
    img_obj = Image.open(img_path)
    json_result = ocr_detect_to_json(img_obj)
    print(json.dumps(json_result, ensure_ascii=False, indent=2))
