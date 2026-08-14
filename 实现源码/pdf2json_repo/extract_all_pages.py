import os
import time
import fitz
import json
from get_new_blocks import get_new_blocks
import shutil
import math
import os
import shutil
import fitz  # PyMuPDF
import json
from get_image_data import get_sorted_png_paths,test_step1

from group_image_insert_to_par import insert_img_json_to_mother_json
# ====== 新增：排序（所有block，包括图片和文本）======

y_thresh = 10  # 阈值可根据实际页面单位调整
def sort_key(d):
    b = d.get('bbox', [0, 0, 0, 0])
    return (round(b[1] / y_thresh), b[0])
def extract_all_pages(pdf_path, save_json_path=None,enable_image=None):
    pdf_path = os.path.abspath(pdf_path)
    doc = fitz.open(pdf_path)
    all_pages_data = []

    # 获取PDF文件名（不含扩展名），拼接输出图片文件夹
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]
    output_dir = os.path.join('temp/output_pdf/', base_name)
    if os.path.exists(output_dir):
        shutil.rmtree(output_dir)  # 删除整个文件夹及其内容
    os.makedirs(output_dir, exist_ok=True)



    for i in range(doc.page_count):
        page_number = i + 1  # 1-based page number
        print(f"正在处理第 {page_number} 页...")
        try:
            page = doc[i]
            new_blocks = get_new_blocks(page=page)

            # ------- 只用 get_image_info -------
            img_info_list = page.get_image_info(xrefs=True)  # 需要PyMuPDF 1.22.0及以上

            # 新增：用于存储本页所有图片block
            image_blocks = []

            for img_index, info in enumerate(img_info_list, start=1):
                xref = info['xref']
                bbox = info.get('bbox')
                # print('完整info', info)
                # print('该页width,height',page.rect.width ,page.rect.height)
                #
                # 计算图片旋转角度
                if 'transform' in info and info['transform'] is not None:
                    a, b, c, d, e, f = info['transform']
                    theta = math.atan2(b, a)
                    angle_deg = theta * 180 / math.pi
                    angle_deg = (round(angle_deg, 2) + 360) % 360


                    # print(f"  图片旋转角度（顺时针，上为负）：{angle_deg:.2f} 度")
                    #
                else:
                    print("  未找到transform，无法计算旋转角度。")
                try:
                    pix = fitz.Pixmap(doc, xref)
                except Exception as e:
                    # print(f"  xref={xref} 创建Pixmap失败: {e}")
                    continue

                if bbox is not None:
                    bbox_width = bbox[2] - bbox[0]
                    ratio = pix.width / bbox_width if bbox_width != 0 else None
                    # print(f"第{page_number}页，第{img_index}张图片，xref={xref}, bbox={bbox}")
                    # print(f"  bbox宽度: {bbox_width:.2f} pt, 图片宽度: {pix.width} px, 比例: {ratio:.4f} px/pt")
                    #
                else:
                    print(f"第{page_number}页，第{img_index}张图片，xref={xref}，未找到bbox！")

                img_filename = f"{page_number}_{img_index}_{bbox[0] if bbox else 'None'}_{bbox[1] if bbox else 'None'}_{xref}.png"
                img_save_path = os.path.join(output_dir, img_filename)
                if pix.n < 5:
                    pix.save(img_save_path)
                else:
                    pix1 = fitz.Pixmap(fitz.csRGB, pix)
                    pix1.save(img_save_path)
                    pix1 = None
                pix = None

                # 新增：构建图片block并加入image_blocks
                image_block = {
                    "content": None,
                    "bbox": bbox if bbox else [0, 0, 0, 0],
                    "content_type": "image",
                    "font_bold": False,
                    "font_size": 10.0,
                    "end_indent": 0,
                    "src_image": os.path.abspath(img_save_path)
                }
                image_blocks.append(image_block)

            # 新增：把所有图片block加入new_blocks
            if new_blocks is None:
                new_blocks = []
            if isinstance(new_blocks, list):
                new_blocks.extend(image_blocks)



            new_blocks.sort(key=sort_key)
            # ===================================================

        except Exception as e:
            print(f"第{page_number}页处理出错: {e}")
            new_blocks = None

        all_pages_data.append({
            "page_number": page_number,
            "blocks": new_blocks if new_blocks else [],
        })
    if enable_image:
        img_paths = get_sorted_png_paths(output_dir)
        # print('img_paths',img_paths)
        if img_paths:  # 只有当 img_paths 非空时才执行
            out_json = os.path.abspath(os.path.join(output_dir, f"{base_name}_image.json"))
            print(img_paths, out_json)

            abs_json = test_step1(img_paths, out_json, ocr_works=10, gpu_ids=[0, 1, 2, 3])

        # print(abs_json)
    result = {
        "pdf_path": pdf_path,
        "pages": all_pages_data
    }


    # 自动命名json文件名（如nd1.pdf→nd1_allpages.json）
    if save_json_path is None:
        save_json_path = f"temp/output_pdf/{base_name}/{base_name}_allpages.json"

    with open(save_json_path, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    if enable_image:
        if img_paths:  # 只有当 img_paths 非空时才执行
            insert_img_json_to_mother_json(mother_json_path=save_json_path,img_json_path=abs_json,output_path=save_json_path)
    return result, os.path.abspath(save_json_path)


if __name__ == "__main__":
    pdf_path = "t5.pdf"  # 或通过命令行、配置指定 xref=13
    start_time = time.time()

    all_data, save_json_path = extract_all_pages(pdf_path,enable_image=True)

    elapsed = time.time() - start_time
    print(f"全部页面提取已完成，已存为 {save_json_path}")
    print(f"总耗时：{elapsed:.2f} 秒")


