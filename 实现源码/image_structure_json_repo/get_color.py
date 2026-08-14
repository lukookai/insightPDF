from PIL import Image
import numpy as np

def get_average_color_of_expanded_ring(image, rect, expand=10):
    """
    image: 图片路径 或 PIL.Image.Image 对象
    rect: (left, upper, right, lower) 原始矩形坐标
    expand: 四周扩展像素数
    """
    # 判断 image 类型
    if isinstance(image, str):
        img = Image.open(image).convert('RGB')
    elif isinstance(image, Image.Image):
        img = image.convert('RGB')
    else:
        raise TypeError("image must be a file path or PIL.Image.Image object")
    width, height = img.size

    # 扩展区域，确保不越界
    left = max(int(np.floor(rect[0] - expand)), 0)
    upper = max(int(np.floor(rect[1] - expand)), 0)
    right = min(int(np.ceil(rect[2] + expand)), width)
    lower = min(int(np.ceil(rect[3] + expand)), height)

    if right <= left or lower <= upper:
        # print(rect)
        # print('宽度，高度：',width,height)
        # print(f"原始 rect: {rect}")
        # print(f"扩展后: left={left}, right={right}, upper={upper}, lower={lower}")
        img.save('erro_show.png')  # <--- 新增这一行
        raise ValueError(f"扩展区域坐标顺序错误: left={left}, right={right}, upper={upper}, lower={lower}")

    # 原区域整数化，并clip到扩展区域内
    orig_left = int(np.round(rect[0]))
    orig_upper = int(np.round(rect[1]))
    orig_right = int(np.round(rect[2]))
    orig_lower = int(np.round(rect[3]))

    # 扩展区域裁剪
    crop = img.crop((left, upper, right, lower))
    np_crop = np.array(crop)

    # 得到原区域在扩展区域内的坐标，全部转int并clip
    inner_left = np.clip(orig_left - left, 0, np_crop.shape[1])
    inner_upper = np.clip(orig_upper - upper, 0, np_crop.shape[0])
    inner_right = np.clip(orig_right - left, inner_left, np_crop.shape[1])
    inner_lower = np.clip(orig_lower - upper, inner_upper, np_crop.shape[0])

    if inner_right <= inner_left or inner_lower <= inner_upper:
        raise ValueError(f"内区坐标顺序错误: inner_left={inner_left}, inner_right={inner_right}, inner_upper={inner_upper}, inner_lower={inner_lower}")

    mask_shape = (np_crop.shape[0], np_crop.shape[1])
    # 检查坐标边界
    if inner_left < 0 or inner_right > mask_shape[1] or inner_upper < 0 or inner_lower > mask_shape[0]:
        raise ValueError(f"内区超出裁剪区域: mask_shape={mask_shape}, inner_left={inner_left}, inner_right={inner_right}, inner_upper={inner_upper}, inner_lower={inner_lower}")

    # 创建掩码，True表示需要采样的像素
    mask = np.ones(mask_shape, dtype=bool)
    mask[inner_upper:inner_lower, inner_left:inner_right] = False

    # 只统计外围像素
    ring_pixels = np_crop[mask]
    if ring_pixels.size == 0:
        raise ValueError("Expanded ring has no pixels to sample.")
    avg_color = ring_pixels.reshape(-1, 3).mean(axis=0)
    avg_color = tuple(int(round(c)) for c in avg_color)
    html_color = '#{:02x}{:02x}{:02x}'.format(*avg_color)
    return html_color

# 示例
if __name__ == '__main__':
    image_path = 'img3.png'
    rect = (644.7052001953125, 103.50550842285156, 693.3729248046875, 126.78575897216797)
    try:
        color_code = get_average_color_of_expanded_ring(image_path, rect, expand=5)
        print(f"扩展采样区域(不含原区域)平均背景色: {color_code}")
        print(f"CSS: background-color: {color_code};")
    except Exception as e:
        print("报错信息:", e)
    # 支持Pillow对象
    try:
        img = Image.open(image_path)
        color_code2 = get_average_color_of_expanded_ring(img, rect, expand=5)
        print(f"使用Pillow对象: {color_code2}")
    except Exception as e:
        print("报错信息:", e)
