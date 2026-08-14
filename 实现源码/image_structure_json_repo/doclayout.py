
import cv2
import datetime
import fitz  # PyMuPDF
import numpy as np

import matplotlib

matplotlib.use('TkAgg')
import matplotlib.pyplot as plt
import matplotlib.patches as patches

# 新增: 先导入 torch 和出现问题的类，然后将其加入 safe_globals
import torch
from torch.serialization import add_safe_globals
import doclayout_yolo.nn.tasks as tasks  # 用于获取 YOLOv10DetectionModel

# 添加到白名单，让 doclayout_yolo.nn.tasks.YOLOv10DetectionModel 在反序列化时被允许
add_safe_globals([tasks.YOLOv10DetectionModel])

from doclayout_yolo import YOLOv10
import os


def pdf_page_to_image(pdf_path, page_number, dpi=300):
    pdf_document = fitz.open(pdf_path)
    if page_number >= len(pdf_document):
        raise ValueError(f"PDF 只有 {len(pdf_document)} 页，无法访问第 {page_number + 1} 页")
    page = pdf_document[page_number]
    page_width = page.rect.width
    page_height = page.rect.height
    pix = page.get_pixmap(dpi=dpi)
    pix_width = pix.width
    pix_height = pix.height

    output_image_path = f"temp_page_{page_number + 1}.jpg"
    pix.save(output_image_path)
    pdf_document.close()

    return output_image_path, pix_width, pix_height, page_width, page_height


def image_to_info(image_path):
    img = cv2.imread(image_path)
    if img is None:
        raise FileNotFoundError(f"图片无法读取：{image_path}")
    pix_height, pix_width = img.shape[:2]
    page_width = pix_width
    page_height = pix_height
    return image_path, pix_width, pix_height, page_width, page_height


def print_model_parameters(model):
    # 计算模型的参数总数
    total_params = sum(p.numel() for p in model.parameters())
    print(f"模型参数量：{total_params} 个参数")


if __name__ == "__main__":
    input_type = "image"  # "pdf" 或 "image"

    # 使用加入 safe_globals 之后的环境来初始化模型
    model = YOLOv10("doclayout_yolo_docstructbench_imgsz1280_2501.pt")

    # 打印模型的参数量
    print_model_parameters(model)

    if input_type == "pdf":
        pdf_path = "nd1.pdf"
        page_number = 6
        image_path, pix_width, pix_height, page_width, page_height = pdf_page_to_image(pdf_path, page_number)
    elif input_type == "image":
        image_path = "img3.png"
        image_path, pix_width, pix_height, page_width, page_height = image_to_info(image_path)
    else:
        raise ValueError('input_type 只能是 "pdf" 或 "image"')

    start_time = datetime.datetime.now()
    det_res = model.predict(
        image_path,
        imgsz=768,
        conf=0.2,
        device="0"
    )
    elapsed_time = datetime.datetime.now() - start_time
    elapsed_seconds = elapsed_time.total_seconds()
    print(f"推理时间：{elapsed_seconds}秒")

    print(det_res)
    boxes = det_res[0].boxes
    names = det_res[0].names

    box_coords_image = []
    for box in boxes:
        x1, y1, x2, y2 = box.xyxy[0].tolist()
        confidence = box.conf[0].item()
        class_id = int(box.cls[0].item())
        class_name = names[class_id]

        msg_image_coord = f"图像坐标: [{x1:.1f}, {y1:.1f}, {x2:.1f}, {y2:.1f}]"
        pdf_x1 = x1 / pix_width * page_width
        pdf_y1 = y1 / pix_height * page_height
        pdf_x2 = x2 / pix_width * page_width
        pdf_y2 = y2 / pix_height * page_height
        msg_pdf_coord = f"PDF/图片坐标: [{pdf_x1:.1f}, {pdf_y1:.1f}, {pdf_x2:.1f}, {pdf_y2:.1f}]"

        print(
            f"检测到框：{class_name}, "
            f"置信度：{confidence:.3f}, "
            f"{msg_image_coord}, "
            f"{msg_pdf_coord}"
        )
        box_coords_image.append((x1, y1, x2, y2, class_name, confidence))

    annotated_frame = det_res[0].plot(pil=True, line_width=5, font_size=20)
    annotated_frame_cv = np.array(annotated_frame)
    annotated_frame_cv = cv2.cvtColor(annotated_frame_cv, cv2.COLOR_RGB2BGR)
    cv2.imwrite("result.jpg", annotated_frame_cv)
    print("推理结果已保存到：result.jpg")

    original_img_bgr = cv2.imread(image_path)
    original_img_rgb = cv2.cvtColor(original_img_bgr, cv2.COLOR_BGR2RGB)

    fig, ax = plt.subplots(figsize=(12, 12))
    ax.imshow(original_img_rgb)
    for (x1, y1, x2, y2, c_name, conf) in box_coords_image:
        width = x2 - x1
        height = y2 - y1
        rect = patches.Rectangle(
            (x1, y1),
            width,
            height,
            linewidth=2,
            edgecolor='red',
            facecolor='none'
        )
        ax.add_patch(rect)
        ax.text(
            x1,
            y1,
            f"{c_name} {conf:.2f}",
            verticalalignment='top',
            fontsize=10,
            color='yellow',
            bbox=dict(facecolor="red", alpha=0.5)
        )

    plt.title("在原图上手动绘制检测框")
    plt.axis('off')
    plt.show()
    print("完成在原图上绘制并展示矩形框。")
