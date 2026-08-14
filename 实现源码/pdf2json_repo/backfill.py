import fitz
import os
import json
import Subset_Font
import time
import merge_pdf
from get_image_data import test_step3,get_sorted_png_paths
from group_image_insert_to_par import get_page_number_from_filename_double
def decimal_to_hex_color(decimal_color):
    if decimal_color == 0:
        return '#000000'
    hex_color = hex(decimal_color)[2:]
    hex_color = hex_color.zfill(6)
    return f'#{hex_color}'

def load_json_blocks(json_path):
    with open(json_path, 'r', encoding='utf-8') as f:
        return json.load(f)

def subset_font(in_font_path, out_font_path, text, language):
    Subset_Font.subset_font(
        in_font_path=in_font_path,
        out_font_path=out_font_path,
        text=text,
        language=language
    )

def main(pdf_path, target_lang, bilingual_pdf,enable_image=None):
    # base_dir = os.path.dirname(os.path.abspath(pdf_path))

    base_name = os.path.splitext(os.path.basename(pdf_path))[0]



    script_dir = os.path.dirname(os.path.abspath(__file__))
    print("script_dir:", script_dir)

    fonts_dir       = os.path.join(script_dir, "temp", "fonts")
    font_ttf        = os.path.join(fonts_dir, f"{target_lang}.ttf")
    font_bold_ttf   = os.path.join(fonts_dir, f"{target_lang}_bold.ttf")
    font_path       = os.path.join(script_dir,  f'temp/output_pdf/{base_name}', f"{target_lang}_subset.ttf")
    bold_font_path  = os.path.join(script_dir,  f'temp/output_pdf/{base_name}', f"{target_lang}_bold_subset.ttf")
    out_pdf_path    = os.path.join(script_dir, f'temp/output_pdf/{base_name}', f"{base_name}_{target_lang}.pdf")
    merged_output_path = os.path.join(script_dir,  f'temp/output_pdf/{base_name}',
                                      f"{base_name}_auto_{target_lang}.pdf")
    json_path = os.path.join(script_dir,  f'temp/output_pdf/{base_name}',
                                      f"{base_name}_allpages.json")

    data = load_json_blocks(json_path)

    normal_insert_texts = []
    bold_insert_texts = []
    for page_item in data["pages"]:
        for block in page_item["blocks"]:
            if block["content_type"] in {"abandon", "abandon_cell", "table"}:
                continue
            if block.get('src_image'):
                continue
            text_for_insert = str(block["content"])
            is_bold = block.get("font_bold", False)
            if is_bold:
                bold_insert_texts.append(text_for_insert)
            else:
                normal_insert_texts.append(text_for_insert)

    bold_text_str = "".join(bold_insert_texts)
    normal_text_str = "".join(normal_insert_texts)
    print(f"子集化普通文本用字符数: {len(normal_text_str)}")
    print(f"子集化粗体文本用字符数: {len(bold_text_str)}")

    if bold_text_str:
        subset_font(font_bold_ttf, bold_font_path, bold_text_str, target_lang)
        subset_font(font_ttf, font_path, normal_text_str, target_lang)
    else:
        subset_font(font_ttf, font_path, normal_text_str, target_lang)

    doc = fitz.open(pdf_path)

    total_apply_time = 0.0
    total_insert_time = 0.0
    insert_pages = 0

    for page_item in data["pages"]:
        pg = doc.load_page(page_item["page_number"] - 1)

        # 删除所有注释（高亮、批注等）
        while True:
            annots = list(pg.annots() or [])
            if not annots:
                break
            for annot in annots:
                pg.delete_annot(annot)

        t0 = time.time()
        for block in page_item["blocks"]:
            block_type = block["content_type"]
            block_indent = block.get("indent", 0)
            block_font_size = block.get("font_size", 12)
            if block_type in {"abandon", "abandon_cell", "table"}:
                continue

            if block.get('src_image'):
                continue

            bbox = block["bbox"]
            x0, y0, x1, y1 = bbox
            rect = fitz.Rect(*bbox)
            try:
                if block_type == 'cell':
                    pg.add_redact_annot(rect)
                    pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE, graphics=0)
                else:
                    if block_indent:
                        # 第一个矩形：左侧缩进部分
                        left_rect = fitz.Rect(x0 + block_indent, y0, x1, y0 + block_font_size*1.1)
                        pg.add_redact_annot(left_rect)
                        # 第二个矩形：上方部分
                        top_rect = fitz.Rect(x0, y0 + block_font_size*1.1, x1, y1)
                        pg.add_redact_annot(top_rect)
                        pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
                    else:
                        pg.add_redact_annot(rect)
                        pg.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE)
            except Exception as e:
                annots = list(pg.annots() or [])
                if annots:
                    pg.delete_annot(annots[-1])
                try:
                    pg.draw_rect(rect, color=(1, 1, 1), fill=(1, 1, 1))
                except Exception as e2:
                    print(f"创建白色画布时发生错误: {e2}")
                print(f"应用重编辑时发生错误: {e}")
        t1 = time.time()
        total_apply_time += (t1 - t0)

        # 分类：普通/粗体
        normal_blocks = []
        bold_blocks = []
        for block in page_item["blocks"]:
            block_type = block["content_type"]
            if block_type in {"abandon", "abandon_cell", "table"}:
                continue
            if block.get('src_image'):
                continue
            if block.get("font_bold", False):
                bold_blocks.append(block)
            else:
                normal_blocks.append(block)

        t2 = time.time()

        # 处理普通字体
        if normal_blocks:
            font_family = f"{target_lang}_font"
            abs_normal_path = os.path.abspath(font_path).replace("\\", "/")
            css_prefix = f"""@font-face {{
    font-family: "{font_family}";
    src: url("{abs_normal_path}");
}}"""

            for block in normal_blocks:
                text_content = block["content"]
                rect_coords  = block["bbox"]
                angle        = block.get("rotation_angle", 0)
                html_color   = decimal_to_hex_color(block.get("color", 0))
                text_indent  = block.get("indent", 0)
                text_size    = block.get("font_size", 12)

                rect = fitz.Rect(*rect_coords)
                css = f"""{css_prefix}
* {{
  font-family: "{font_family}";
  color: {html_color};
  text-indent: {text_indent}pt;
  font-size: {float(text_size)}pt;
  line-height: 1.2;
}}"""

                pg.insert_htmlbox(
                    rect,
                    text_content,
                    css=css,
                    rotate=angle
                )

        # 处理粗体字体
        if bold_blocks:
            font_family = f"{target_lang}_bold_font"
            abs_bold_path = os.path.abspath(bold_font_path).replace("\\", "/")
            css_prefix = f"""@font-face {{
    font-family: "{font_family}";
    src: url("{abs_bold_path}");
}}"""

            for block in bold_blocks:
                text_content = block["content"]
                rect_coords  = block["bbox"]
                angle        = block.get("rotation_angle", 0)
                html_color   = decimal_to_hex_color(block.get("color", 0))
                text_indent  = block.get("indent", 0)
                text_size    = block.get("font_size", 12)

                rect = fitz.Rect(*rect_coords)
                css = f"""{css_prefix}
* {{
  font-family: "{font_family}";
  color: {html_color};
  text-indent: {text_indent}pt;
  font-size: {float(text_size)}pt;
  line-height: 1.5;
}}"""

                pg.insert_htmlbox(
                    rect,
                    text_content,
                    css=css,
                    rotate=angle
                )

        t3 = time.time()
        total_insert_time += (t3 - t2)

        insert_pages += 1
        if insert_pages % 20 == 0:
            print(f"已经完成回填文本 {insert_pages} 页")



    if enable_image:
        output_dir = os.path.join('temp/output_pdf/', base_name)
        img_paths = get_sorted_png_paths(output_dir)
        # 步骤1：图片转json
        out_json = os.path.abspath(os.path.join(output_dir, f"{base_name}_image.json"))

        abs_json = out_json
        # print('abs',abs_json)
        # 步骤2：自动翻译json
        # test_step2(abs_json)
        if img_paths:  # 只有当 img_paths 非空时才执行
            # 步骤3：构建html并截图，delete_html，是否保留生成中间文件html,True为删除
            html_files, screenshots = test_step3(img_paths, abs_json, type='pdf', delete_html=False)
            for path in img_paths:
                pag_number, xref = get_page_number_from_filename_double(src_image=path)
                page = doc[pag_number - 1]
                page.replace_image(xref, filename=path)


    # get_page_number_from_filename_double()
    doc.save(out_pdf_path, garbage=4, deflate=True)
    doc.close()

    print("处理完成:", out_pdf_path)
    print(f"apply打码总耗时: {total_apply_time:.3f} 秒")
    print(f"插入回填文本总耗时: {total_insert_time:.3f} 秒")
    if bilingual_pdf:
        print("正在创建双语对照PDF...")
        merge_pdf.merge_pdfs_horizontally(pdf1_path=pdf_path, pdf2_path=out_pdf_path,
                                          output_path=merged_output_path)
        print(f"处理完成！输出文件: {merged_output_path}")



if __name__=="__main__":
    target_lang = "en"
    pdf_path = "g2.pdf"
    bilingual_pdf = True

    main(pdf_path, target_lang, bilingual_pdf,enable_image=None)
