import json
import numpy as np
from sklearn.cluster import KMeans

def detect_columns(blocks, img_width=None):
    if len(blocks) < 2:
        return np.zeros(len(blocks), dtype=int), 1
    x_coords = np.array([[block['bbox'][0]] for block in blocks])
    if img_width is None:
        img_width = max(block['bbox'][2] for block in blocks) - min(block['bbox'][0] for block in blocks)
    kmeans = KMeans(n_clusters=2, random_state=0).fit(x_coords)
    centers = kmeans.cluster_centers_.flatten()
    labels = kmeans.labels_
    center_dist = abs(centers[0] - centers[1])
    if center_dist > img_width * 0.3:
        # 双栏，左栏编号靠左
        if centers[0] < centers[1]:
            return labels, 2
        else:
            # 保证左栏是0，右栏是1
            labels = np.abs(labels - 1)
            return labels, 2
    else:
        return np.zeros(len(blocks), dtype=int), 1

def sort_blocks(blocks, labels, n_col, y_thresh=10):
    sorted_blocks = []
    for col in range(n_col):
        col_blocks = [block for i, block in enumerate(blocks) if labels[i] == col]
        # 先按y分组，组内按x排序
        col_blocks.sort(key=lambda b: (round(b['bbox'][1] / y_thresh), b['bbox'][0]))
        sorted_blocks.extend(col_blocks)
    return sorted_blocks

def reorder_json(json_path, out_path=None):
    with open(json_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    for page in data["pages"]:
        blocks = page["blocks"]
        if not blocks:
            continue
        img_width = max(block['bbox'][2] for block in blocks) - min(block['bbox'][0] for block in blocks)
        labels, n_col = detect_columns(blocks, img_width)
        new_blocks = sort_blocks(blocks, labels, n_col)
        page["blocks"] = new_blocks  # 重排
        print(f"Page {page['page_number']}: Detected {n_col} columns, blocks reordered.")

    if out_path:
        with open(out_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        print(f"重排结果已保存到: {out_path}")
    else:
        print(json.dumps(data, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    json_path = r"D:\ebook-translator\pdf2json\temp\output_pdf\g2\g2_allpages.json"  # 输入路径
    out_path = r"D:\ebook-translator\pdf2json\g2_reordered.json"  # 输出路径
    reorder_json(json_path, out_path)
