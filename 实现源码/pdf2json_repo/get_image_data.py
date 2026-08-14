from config import image_process_url
import requests
import json
import os
import requests
import json
import os

API_URL = image_process_url


import glob

def get_sorted_png_paths(folder):
    # 获取所有png文件路径
    png_paths = glob.glob(os.path.join(folder, "*.png"))
    # 按修改时间排序
    png_paths.sort(key=lambda x: os.path.getmtime(x))
    # 转为绝对路径
    abs_png_paths = [os.path.abspath(p) for p in png_paths]
    return abs_png_paths




# 2. 步骤1：图片转json
def test_step1(img_paths, out_json, ocr_works=10, gpu_ids=None):
    url = f"{API_URL}/step1_extract_image_text"
    payload = {
        "img_paths": img_paths,
        "out_json": out_json,
        "ocr_works": ocr_works,
        "gpu_ids": gpu_ids or []
    }
    resp = requests.post(url, json=payload)
    print("step1_extract_image_text:", resp.json())
    return resp.json()["abs_json"]

# 3. 步骤2：自动翻译json
def test_step2(json_path):
    url = f"{API_URL}/step2_auto_translate_json"
    payload = {"json_path": json_path}
    resp = requests.post(url, json=payload)
    print("step2_auto_translate_json:", resp.json())

# 4. 步骤3：构建html并截图
def test_step3(img_paths, json_path, type='png', delete_html=True):
    url = f"{API_URL}/step3_build_html_and_screenshots"
    payload = {
        "img_paths": img_paths,
        "json_path": json_path,
        "type": type,
        "delete_html": delete_html
    }
    resp = requests.post(url, json=payload)
    print("step3_build_html_and_screenshots:", resp.json())
    return resp.json()["html_files"], resp.json()["screenshots"]

# 5. 步骤4：生成pdf和emf/svg
def test_step4(html_files, convert_type=None):
    url = f"{API_URL}/step4_pdf_and_convert"
    payload = {
        "html_files": html_files,
        "convert_type": convert_type
    }
    resp = requests.post(url, json=payload)
    print("step4_pdf_and_convert:", resp.json())
    return resp.json()["pdf_results"]

if __name__ == "__main__":
    # 你的本地图片路径列表
    #传入图片地址，生成译文图片覆盖传入图片地址
    img_paths = [

        r"C:\Users\Administrator\Desktop\t1.png",
        # r"C:\Users\Administrator\Desktop\t2.png",




        # r"C:\Users\Administrator\Desktop\t12.png",  # 如果有多张图片，继续加
    ]
    # 步骤1：图片转json
    out_json = os.path.abspath("temp_file/2319913213.json")
    abs_json = test_step1(img_paths, out_json, ocr_works=10, gpu_ids=[0, 1, 2, 3])
    print('abs',abs_json)
    # 步骤2：自动翻译json
    # test_step2(abs_json)

    # 步骤3：构建html并截图，delete_html，是否保留生成中间文件html,True为删除
    html_files, screenshots = test_step3(img_paths, abs_json, type='pdf', delete_html=False)

    # 步骤4：生成pdf和emf/svg
    # pdf_results = test_step4(html_files, convert_type="emf")

    print("\n全部流程跑通！")
