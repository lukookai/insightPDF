import fitz  # PyMuPDF
import os


def extract_images_to_folder(pdf_path):
    # 获取PDF文件名（不带扩展名）
    pdf_name = os.path.splitext(os.path.basename(pdf_path))[0]
    # 构造目标文件夹路径
    output_dir = os.path.join('temp', pdf_name)
    os.makedirs(output_dir, exist_ok=True)

    doc = fitz.open(pdf_path)
    for page_number, page in enumerate(doc, start=1):
        img_list = page.get_images(full=True)
        for img_index, img in enumerate(img_list, start=1):
            xref = img[0]
            pix = fitz.Pixmap(doc, xref)
            img_filename = f"page{page_number}_img{img_index}.png"
            img_save_path = os.path.join(output_dir, img_filename)
            if pix.n < 5:  # this is GRAY or RGB
                pix.save(img_save_path)
            else:  # CMYK: convert to RGB first
                pix1 = fitz.Pixmap(fitz.csRGB, pix)
                pix1.save(img_save_path)
                pix1 = None
            pix = None


# 示例调用
extract_images_to_folder("your.pdf")
