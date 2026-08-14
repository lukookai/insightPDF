import os
import json
import unicodedata
from PIL import Image
from concurrent.futures import ThreadPoolExecutor, as_completed

from configs import ocr_max_works
from get_layout_result import get_layout_result
from detect_figure_text import ocr_detect_to_json
from get_color import get_average_color_of_expanded_ring
from ocr_pillow_silence_by_api import extract_text_from_image

cpu_count = os.cpu_count()
default_max_workers = cpu_count + 1 if cpu_count else 4
if ocr_max_works:
    default_max_workers = ocr_max_works

def prepare_json_file(out_path):
    os.makedirs(os.path.dirname(out_path), exist_ok=True)
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump({}, f, ensure_ascii=False, indent=2)
    # 验证
    with open(out_path, 'r', encoding='utf-8') as f:
        content = f.read()
        try:
            data = json.loads(content)
            if data != {}:
                print("文件内容不是空JSON对象！")
        except Exception as e:
            print("文件内容不是合法的JSON！", e)

def is_symbol_or_digit(s):
    if not s:
        return True
    for ch in str(s):
        cat = unicodedata.category(ch)
        if not (cat.startswith('P') or cat.startswith('S') or cat == 'Nd' or cat == 'Zs'):
            return False
    return True

def get_image_full_coord(
    out_path='',
    img_path_test=[],
    max_workers=None,
    gpu_ids=[],
    extract_child=True,
    filtr_type=[],
    extract_bg_color=True,
    child_type=['image','table'],
    ban_type= ['image','table']
):
    # if max_workers is None:
    max_workers = default_max_workers
    prepare_json_file(out_path)
    img_abs_paths = [os.path.abspath(p) for p in img_path_test]
    # 预加载图片对象
    img_pil_map = {p: Image.open(p).convert('RGB') for p in img_abs_paths}
    save_path = os.path.abspath(out_path)

    layout_json = get_layout_result(img_paths=img_abs_paths, gpu_ids=gpu_ids, filtr_type=filtr_type,ban_type=ban_type)
    new_json = []
    child_ocr_tasks = []  # (父图在new_json的插入位置, item, sub_img, x1, y1)

    # ----------- 1. 收集子图OCR任务并记录父图位置 -----------
    from configs import  child_type as cl
    if not child_type:
        child_type =cl
    for idx, item in enumerate(layout_json):
        new_json.append(item)  # 保留原item
        if extract_child and item.get("image_type") in child_type:
            src_abs = os.path.abspath(item["src_image"])
            pil_img = img_pil_map.get(src_abs)
            if pil_img is None:
                print(f"警告: 未找到图片对象: {src_abs}")
                continue
            x1, y1, x2, y2 = map(int, item["bbox"])
            # PIL线程安全修正：每个线程内都用.copy()
            sub_img = pil_img.crop((x1, y1, x2, y2)).copy()
            # 记录父图在new_json的插入位置
            child_ocr_tasks.append((idx, item, sub_img, x1, y1))

    # ----------- 2. 并发执行 ocr_detect_to_json -----------
    def run_ocr_detect(item, sub_img, x1, y1):
        try:
            ocr_results = ocr_detect_to_json(sub_img)
            for ocr_item in ocr_results:
                bx1, by1, bx2, by2 = ocr_item["bbox"]
                ocr_item["bbox"] = [bx1 + x1, by1 + y1, bx2 + x1, by2 + y1]
                ocr_item["src_image"] = item["src_image"]
                # 新增：如果父item是table，给ocr_item加child_table字段
                if item.get("image_type") == "table":
                    ocr_item["child_table"] = 'fuck'
            return ocr_results
        except Exception as e:
            print(f"子图OCR失败: {e}")
            return []

    # 并发执行OCR，并收集结果，顺序与 child_ocr_tasks 保持一致
    ocr_results_list = [None] * len(child_ocr_tasks)
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_idx = {
            executor.submit(run_ocr_detect, item, sub_img, x1, y1): i
            for i, (idx, item, sub_img, x1, y1) in enumerate(child_ocr_tasks)
        }
        for future in as_completed(future_to_idx):
            i = future_to_idx[future]
            ocr_results_list[i] = future.result()

    # ----------- 3. 按父图顺序插入子图OCR结果 -----------
    # 倒序插入，避免插入后索引错乱
    for (idx, _, _, _, _), ocr_results in zip(reversed(child_ocr_tasks), reversed(ocr_results_list)):
        if ocr_results:
            new_json[idx+1:idx+1] = ocr_results

    # ========== 合并遍历：为plain元素加背景色，并收集OCR任务 ==========
    tasks = []
    for elem in new_json:
        if elem.get("content_type") == "plain" and "bbox" in elem and "src_image" in elem:
            src_abs = os.path.abspath(elem["src_image"])
            pil_img = img_pil_map.get(src_abs)
            if pil_img is None:
                print(f"警告: plain元素未找到图片对象: {src_abs}")
                continue
            bbox = elem["bbox"]
            try:
                if extract_bg_color:
                    color = get_average_color_of_expanded_ring(pil_img, bbox, expand=2)
                else:
                    color = "#ffffff"
            except Exception as e:
                print(f"获取颜色失败: {e}，bbox={bbox}, src={src_abs}")
                print('问题图片地址：', elem.get('src_image'))
                color = "#ffffff"
            elem["background-color"] = color
            tasks.append((elem, pil_img))

    # ========== 并发处理OCR ==========
    def process_elem_text(elem, pil_img):
        bbox = elem["bbox"]
        image_type = elem.get("image_type", "")
        # PIL线程安全修正：每个线程都用.copy()
        sub_img = pil_img.crop((bbox[0], bbox[1], bbox[2], bbox[3])).copy()
        try:
            text = extract_text_from_image(sub_img, image_type=image_type)
        except Exception as e:
            print(f"OCR处理失败: {e}")
            text = ""
        return (elem, text)

    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_elem = {
            executor.submit(process_elem_text, elem, pil_img): elem
            for elem, pil_img in tasks
        }
        for future in as_completed(future_to_elem):
            elem, text = future.result()
            elem["content"] = text
            if is_symbol_or_digit(text):
                elem["content_type"] = "symbol"

    # ========== 保存 ==========
    with open(save_path, "w", encoding="utf-8") as f:
        json.dump(new_json, f, ensure_ascii=False, indent=2)

    print("处理完成，结果已保存到:", save_path)
    return save_path

if __name__ == "__main__":
    img_paths_test = [
        r"./test2.png",
        r"./temp_file/test2.png",
    ]
    # out_path = "output.json"
    # get_image_full_coord(out_path=out_path, img_path_test=img_paths_test, max_workers=8)

    layout_result = get_layout_result(img_paths=img_paths_test, gpu_ids=[0, 1, 2])
    print(layout_result)
