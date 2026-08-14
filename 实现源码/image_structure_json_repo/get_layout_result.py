
import os
from configs import doclayout_api,ban_type as bt
def process_detection_results(result, img_paths,filtr_type=[],ban_type=[]):
    output = []
    for img_path, detections in zip(img_paths, result):
        abs_img_path = os.path.abspath(img_path)
        for det in detections:
            # 过滤掉 class_name 为 "abandon" 的数据

            if det.get("class_name", "") in filtr_type:
                continue
            class_name = det.get("class_name", "")
            is_edge = det.get("is_edge", False)
            read_index = det.get("read_index")
            # 判断 content_type
            if not ban_type:
                ban_type=bt

            if class_name in ban_type:
                content_type = "abandon"
            else:
                content_type = "plain"
            entry = {
                "src_image": abs_img_path,
                "image_type": class_name,
                "bbox": [round(float(x), 6) for x in det.get("box", [])],
                "content": "",
                "translated_content": "",
                "background-color": "",
                "content_type": content_type,
                "is_edge": is_edge,
                "read_index": read_index
            }
            output.append(entry)
    return output



import requests

import time  # 导入time模块


def get_layout_result(img_paths=[], api_url=doclayout_api,

                      gpu_ids=[],filtr_type=[],
                      ban_type=[]):
    files = []
    for img_path in img_paths:
        files.append(("files", (os.path.basename(img_path), open(img_path, "rb"), "image/jpeg")))

    start_time = time.time()  # 记录开始时间
    response = requests.post(api_url, files=files)
    end_time = time.time()  # 记录结束时间

    cost_time = end_time - start_time
    print(f"doclayout_请求耗时: {cost_time:.3f} 秒")

    if response.status_code != 200:
        raise RuntimeError(f"FastAPI服务返回错误: {response.status_code} {response.text}")
    result_json = response.json()
    print(result_json)
    # 兼容你的旧格式，提取每张图片的detections
    result = [item["detections"] for item in result_json["results"]]
    processed_result = process_detection_results(result, img_paths,filtr_type=filtr_type,ban_type=ban_type)
    return processed_result
