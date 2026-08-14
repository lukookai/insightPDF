import fitz
import os
import json
import Subset_Font
import time
import shutil
import merge_pdf
def decimal_to_hex_color(decimal_color):
    if decimal_color == 0:
        return '#000000'  # 黑色
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

def main(pdf_path, target_lang,bilingual_pdf):
    base_dir = os.path.dirname(os.path.abspath(pdf_path))
    base_name = os.path.splitext(os.path.basename(pdf_path))[0]

    json_path = os.path.join(base_dir, f"{base_name}_allpages.json")
    script_dir = os.path.dirname(os.path.abspath(__file__))
    print("script_dir:", script_dir)

    fonts_dir       = os.path.join(script_dir, "temp", "fonts")
    font_ttf        = os.path.join(fonts_dir, f"{target_lang}.ttf")
    font_bold_ttf   = os.path.join(fonts_dir, f"{target_lang}_bold.ttf")
    font_path       = os.path.join(fonts_dir, f"{target_lang}_subset.ttf")
    bold_font_path  = os.path.join(fonts_dir, f"{target_lang}_bold_subset.ttf")
    out_pdf_path    = os.path.join(base_dir, f"{base_name}_{target_lang}.pdf")
    merged_output_path = os.path.join(script_dir,  'merged_pdf',
                                      f"{out_pdf_path}_auto_{target_lang}.pdf")

    temp_pdf_path = os.path.join(base_dir, f'{base_name}_working.pdf')
    shutil.copy(pdf_path, temp_pdf_path)

    data = load_json_blocks(json_path)

    # 收集所有实际要插入的文本
    normal_insert_texts = []
    bold_insert_texts = []
    for page_item in data["pages"]:
        for block in page_item["blocks"]:
            if block[2] in {"abandon", "abandon_cell", "table"}:
                continue
            text_for_insert = str(block[0])
            is_bold = block[6] if len(block) > 6 else False
            if is_bold:
                bold_insert_texts.append(text_for_insert)
            else:
                normal_insert_texts.append(text_for_insert)

    bold_text_str = "".join(bold_insert_texts)
    normal_text_str = "".join(normal_insert_texts)
    print(f"子集化普通文本用字符数: {len(normal_text_str)}")
    print(f"子集化粗体文本用字符数: {len(bold_text_str)}")

    # 子集化字体
    if bold_text_str:
        subset_font(font_bold_ttf, bold_font_path, bold_text_str, target_lang)
        subset_font(font_ttf, font_path, normal_text_str, target_lang)
    else:
        subset_font(font_ttf, font_path, normal_text_str, target_lang)

    # 打开临时 PDF，开始插入
    doc = fitz.open(temp_pdf_path)

    total_apply_time = 0.0
    total_insert_time = 0.0
    insert_pages = 0

    for page_item in data["pages"]:
        pg = doc.load_page(page_item["page_number"] - 1)
        normal_blocks = []
        bold_blocks = []

        t0 = time.time()
        for block in page_item["blocks"]:
            block_type = block[2]
            if block_type in {"abandon", "abandon_cell", "table"}:
                continue
            bbox = block[1]
            rect = fitz.Rect(*bbox)
            try:
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

        for block in page_item["blocks"]:
            block_type = block[2]
            if block_type in {"abandon", "abandon_cell", "table"}:
                continue
            text = block[0]
            bbox = block[1]
            text_angle = block[3] if len(block) > 3 else 0
            text_color = block[4] if len(block) > 4 else 0
            html_color = decimal_to_hex_color(text_color)
            text_indent = block[5] if len(block) > 5 else 0
            text_bold = block[6] if len(block) > 6 else False
            text_size = block[7] if len(block) > 7 else 12
            line_count = block[16] if len(block) > 16 else 1

            block_tuple = (
                text,       # 0
                bbox,       # 1
                text_angle, # 2
                html_color, # 3
                text_indent,# 4
                text_bold,  # 5
                text_size,  # 6
                line_count  # 7
            )
            if text_bold:
                bold_blocks.append(block_tuple)
            else:
                normal_blocks.append(block_tuple)

        t2 = time.time()

        # 处理普通字体
        if normal_blocks:
            font_family = f"{target_lang}_font"
            abs_normal_path = os.path.abspath(font_path).replace("\\", "/")
            # print(f"[普通字体] 加载路径: {abs_normal_path}, 存在: {os.path.exists(abs_normal_path)}, 大小: {os.path.getsize(abs_normal_path) if os.path.exists(abs_normal_path) else 0}")

            css_prefix = f"""@font-face {{
    font-family: "{font_family}";
    src: url("{abs_normal_path}");
}}"""

            for block_idx, item in enumerate(normal_blocks):
                text_content = item[0]
                rect_coords  = item[1]
                angle        = item[2]
                html_color   = item[3]
                text_indent  = item[4]
                text_size    = item[6]

                rect = fitz.Rect(*rect_coords)
                css = f"""{css_prefix}
* {{
  font-family: "{font_family}";
  color: {html_color};
  text-indent: {text_indent}pt;
  font-size: {float(text_size)}pt;
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
            print(f"[粗体字体] 加载路径: {abs_bold_path}, 存在: {os.path.exists(abs_bold_path)}, 大小: {os.path.getsize(abs_bold_path) if os.path.exists(abs_bold_path) else 0}")

            css_prefix = f"""@font-face {{
    font-family: "{font_family}";
    src: url("{abs_bold_path}");
}}"""

            for block_idx, item in enumerate(bold_blocks):
                text_content = item[0]
                rect_coords  = item[1]
                angle        = item[2]
                html_color   = item[3]
                text_indent  = item[4]
                text_size    = item[6]

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

        t3 = time.time()
        total_insert_time += (t3 - t2)

        insert_pages += 1
        if insert_pages % 20 == 0:
            print(f"已经完成回填文本 {insert_pages} 页")

    doc.save(out_pdf_path, garbage=4, deflate=True)
    doc.close()

    try:
        os.remove(temp_pdf_path)
    except Exception as e:
        print(f"删除临时文件失败: {e}")

    print("处理完成:", out_pdf_path)
    print(f"apply打码总耗时: {total_apply_time:.3f} 秒")
    print(f"插入回填文本总耗时: {total_insert_time:.3f} 秒")
    if bilingual_pdf:
        print("正在创建双语对照PDF...")
        merge_pdf.merge_pdfs_horizontally(pdf1_path=pdf_path, pdf2_path=out_pdf_path,
                                          output_path=merged_output_path)
        print(f"处理完成！输出文件: {merged_output_path}")

if __name__=="__main__":
    target_lang = "zh"
    pdf_path = "股东p1.pdf"
    bilingual_pdf = True
    
    main(pdf_path, target_lang,bilingual_pdf)