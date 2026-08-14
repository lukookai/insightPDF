import os
import json

def group_by_src_image(img_json_list):
    """ 按src_image分组，返回{src_image: [item, ...]} """
    grouped = {}
    for item in img_json_list:
        key = os.path.abspath(item['src_image'])
        grouped.setdefault(key, []).append(item)
    return grouped
def get_page_number_from_filename_double(src_image):
    """ 从文件名如 2_1_225.31915283203125_49.999298095703125.png 提取page_number=2 """
    # 假设文件名格式：2_1_xxx_xxx.png
    filename = os.path.basename(src_image)
    last_part = filename.split('_')[-1]        # '49.png'
    number_str = last_part.split('.')[0]

    return int(filename.split('_')[0]),int(number_str)

def extract_filename(src_image):
    """ 提取文件名（含扩展名） """
    return os.path.basename(src_image)

def get_page_number_from_filename(filename):
    """ 从文件名如 2_1_225.31915283203125_49.999298095703125.png 提取page_number=2 """
    # 假设文件名格式：2_1_xxx_xxx.png
    return int(filename.split('_')[0])

def insert_img_json_to_mother_json(mother_json_path, img_json_path, output_path=None):
    # 1. 加载json
    with open(mother_json_path, 'r', encoding='utf-8') as f:
        mother = json.load(f)
    with open(img_json_path, 'r', encoding='utf-8') as f:
        img_json = json.load(f)

    # 2. 分组
    img_grouped = group_by_src_image(img_json)

    # 3. 遍历每一组
    for src_image, items in img_grouped.items():
        filename = extract_filename(src_image)
        page_number = get_page_number_from_filename(filename)
        # 找到母json中对应page_number的page
        page = next((p for p in mother['pages'] if p['page_number'] == page_number), None)
        if not page:
            print(f"Warning: Page {page_number} not found for src_image {src_image}")
            continue
        # 在blocks中找到src_image相同的block
        blocks = page['blocks']
        idx = next((i for i, block in enumerate(blocks)
                    if os.path.abspath(block.get('src_image', '')) == src_image), None)
        if idx is None:
            print(f"Warning: Block with src_image {src_image} not found in page {page_number}")
            continue
        # 插入分组的items到该block后面
        for offset, item in enumerate(items):
            blocks.insert(idx + 1 + offset, item)

    # 4. 保存（直接覆盖母json）
    out_path = output_path or mother_json_path
    with open(out_path, 'w', encoding='utf-8') as f:
        json.dump(mother, f, ensure_ascii=False, indent=2)
    print(f"写入完成: {out_path}")
    return out_path

# 用法示例
if __name__ == "__main__":
    mother_json_path = r"D:\ebook-translator\pdf2json\temp\output_pdf\t1\t1_allpages.json"
    img_json_path = r"D:\ebook-translator\pdf2json\temp\output_pdf\t1\t1_image.json"
    insert_img_json_to_mother_json(mother_json_path, img_json_path, output_path=mother_json_path)
