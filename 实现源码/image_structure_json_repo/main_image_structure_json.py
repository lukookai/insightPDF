import json
import os
from PIL import Image
from get_image_full_coord import get_image_full_coord
from to_shoot import generate_screenshots
from generate_html import main as gen_html_main
from to_vector import convert_with_inkscape
from png_transparent_to_white import transparent_to_white

def extract_image_text(img_paths, out_json, ocr_works=10, gpu_ids=None,
                       extract_child=True,
                       filtr_type=[],
                       extract_bg_color=True,
                        child_type=['image','table'],
                       ban_type=['image','table']
                       ):
    """步骤1：提取图片文本"""
    abs_out_path = os.path.abspath(out_json)
    for img_path in img_paths:
        transparent_to_white(img_path)
    get_image_full_coord(
        out_path=abs_out_path,
        img_path_test=img_paths,
        max_workers=ocr_works,
        gpu_ids=gpu_ids or [],
        extract_child= extract_child,
        filtr_type=filtr_type,
        extract_bg_color= extract_bg_color,
       child_type = child_type,
       ban_type = ban_type
    )
    return abs_out_path

def auto_translate_json(json_path):
    """
    步骤2：自动翻译json，将content+"translated:"写入translated_content字段，并回写json。
    """
    with open(json_path, "r", encoding="utf-8") as f:
        jsonl = json.load(f)
    changed = False
    for item in jsonl:
        content = item.get("content", "")
        if content:
            item["translated_content"] = content + "translated:"
            changed = True
        else:
            item["translated_content"] = ""
    if changed:
        with open(json_path, "w", encoding="utf-8") as f:
            json.dump(jsonl, f, ensure_ascii=False, indent=2)

def build_html(img_paths, json_path):
    """遍历json，构建html"""
    with open(json_path, "r", encoding="utf-8") as f:
        jsonl = json.load(f)
    html_path = gen_html_main(img_paths_test=img_paths, jsonl=jsonl)
    return html_path

def assemble_html_files(img_paths, type=''):
    """组装html_files"""
    html_files = []
    # print('hhh')
    for img_path in img_paths:
        abs_img_path = os.path.abspath(img_path)
        html_path = os.path.splitext(abs_img_path)[0] + ".html"
        with Image.open(abs_img_path) as img:
            width, height = img.size
        # 获取不带点的后缀
        ext = os.path.splitext(abs_img_path)[1].lstrip('.').lower()
        cur_type = type if type else ext
        html_files.append({
            "file": html_path,
            "width": width,
            "height": height,
            "type": cur_type
        })
    return html_files
def generate_pdf_from_image(img_path, pdf_path):
    """将图片保存为PDF"""
    with Image.open(img_path) as img:
        if img.mode == "RGBA":
            img = img.convert("RGB")
        img.save(pdf_path, "PDF", resolution=100.0)

def step1_extract_image_text(img_paths, out_json,
                             ocr_works=10, gpu_ids=None,extract_child=True,
                             filtr_type=[],extract_bg_color=True,
                             child_type=['image','table'],ban_type=['image','table']):
    """步骤1：提取图片文本"""

    if filtr_type is None:
        filtr_type = []
    if child_type is None:
        child_type = ['image','table']
    if ban_type is None:
        ban_type = ['image','table']
    return extract_image_text(img_paths, out_json, ocr_works,
                              gpu_ids,extract_child,
                              filtr_type,
                              extract_bg_color=extract_bg_color
                              ,child_type= child_type,ban_type=ban_type)

def step2_auto_translate_json(json_path):
    """步骤2：自动翻译json"""
    auto_translate_json(json_path)

def step3_build_html_and_screenshots(img_paths, json_path,type='png',delete_html = True):
    """
    步骤3：构建html并生成截图
    返回html文件信息和截图结果
    """
    build_html(img_paths, json_path)
    html_files = assemble_html_files(img_paths,type=type)
    # print('生成html路径',html_files)
    screenshots = generate_screenshots(html_files)
    # print('sceenshots',screenshots)
    # 新增：如果delete_html为True，删除生成的html文件
    if delete_html:
        for info in html_files:
            html_file = info.get("file")
            if html_file and os.path.exists(html_file):
                try:
                    os.remove(html_file)
                    print(f"已删除html文件: {html_file}")
                except Exception as e:
                    print(f"删除html文件失败: {html_file}，错误: {e}")
    return html_files, screenshots

def step4_pdf_and_convert(html_files, convert_type=None):
    """
    步骤4：生成pdf并可选转换为emf/svg
    """
    results = []
    for info in html_files:
        if info.get("type") == "pdf":
            img_path = os.path.splitext(info["file"])[0] + os.path.splitext(info["file"])[1].replace(".html", ".png")
            pdf_path = os.path.splitext(img_path)[0] + ".pdf"
            generate_pdf_from_image(img_path, pdf_path)
            convert_path = None
            if convert_type in ("emf", "svg"):
                convert_path = convert_with_inkscape(pdf_path, output_type=convert_type)
            results.append({
                "pdf": pdf_path,
                "converted": convert_path
            })
    return results

# =========================
# 示例用法
if __name__ == "__main__":
    img_paths_test = [r"C:\Users\Administrator\Desktop\t11.png"]
    out_path = "temp_file/2319913213.json"
    out_path = os.path.abspath(out_path)
    ocr_works = 10
    gpu_ids = [0, 1, 2, 3]
    pdf_convert_type = "emf"  # 或"svg"或None

    # 步骤一：图片转json
    abs_json = step1_extract_image_text(img_paths_test, out_path, ocr_works, gpu_ids)
    # 步骤二：自动翻译json
    # step2_auto_translate_json(abs_json)
    # # 步骤三：构建html并截图
    html_files, screenshots = step3_build_html_and_screenshots(img_paths_test, abs_json,delete_html=True)
    # 步骤四：生成pdf和emf/svg
    # pdf_results = step4_pdf_and_convert(html_files, convert_type=pdf_convert_type)
    # print({
    #     "screenshots": screenshots,
    #     "pdf_results": pdf_results
    # })
