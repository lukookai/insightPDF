import base64
from openai import OpenAI
from PIL import Image
import io
import os
import time
from configs import OCR_URL, OCR_API_KEY, OCR_MODEL_NAME

client = OpenAI(
    api_key=OCR_API_KEY,
    base_url=OCR_URL
)


def image_to_png_base64(image_input):
    """
    支持三种输入：
    1. str: 本地图片路径
    2. PIL.Image.Image: Pillow图片对象
    3. 已经是base64字符串data url（自动判断）
    如有任一边小于28像素，则等比例放大，最小边为28像素
    """
    # 1) 已经是 data url
    if isinstance(image_input, str) and image_input.strip().startswith("data:image/"):
        return image_input.split(",")[-1]

    img = None
    is_local_opened = False  # 标记是否是我们自己打开的本地文件

    # 2) 路径
    if isinstance(image_input, str):
        if not os.path.exists(image_input):
            raise FileNotFoundError(f"图片路径不存在: {image_input}")
        img = Image.open(image_input)
        is_local_opened = True
    # 3) PIL 对象
    elif isinstance(image_input, Image.Image):
        img = image_input
    else:
        raise TypeError("image_input 必须是图片路径、Pillow对象或者base64字符串")

    try:
        # 确保兼容 PNG
        min_side = min(img.width, img.height)
        if min_side < 28:
            scale = 28 / min_side
            new_width = int(round(img.width * scale))
            new_height = int(round(img.height * scale))

            # resize 会生成一个新图片对象
            resized_img = img.resize((new_width, new_height), Image.LANCZOS)

            # 如果新对象的边仍然小于 28 (罕见情况防死角)
            if min(resized_img.width, resized_img.height) < 28:
                scale_2 = 28 / min(resized_img.width, resized_img.height)
                final_img = resized_img.resize(
                    (int(round(resized_img.width * scale_2)), int(round(resized_img.height * scale_2))),
                    Image.LANCZOS
                )
                resized_img.close()  # 及时释放中间产物
                resized_img = final_img

            # 如果我们自己打开了原始图，且生成了放大图，原始图就可以丢了
            if is_local_opened:
                img.close()
                is_local_opened = False
            img = resized_img

        # 转 PNG 并 base64
        # 使用 with 确保 BytesIO 内存用完立刻销毁
        with io.BytesIO() as byte_arr:
            img.save(byte_arr, format="PNG", compress_level=0, optimize=False)
            b64_str = base64.b64encode(byte_arr.getvalue()).decode("utf-8")
            return b64_str

    finally:
        # 无论发生什么（成功或报错），确保释放掉我们主动打开的本地图片
        if is_local_opened and img is not None:
            img.close()

TASKS = {
    "ocr": "OCR:",
    "table": "Table Recognition:",
    "formula": "Formula Recognition:",
    "chart": "Chart Recognition:",
    "spotting": "Spotting:",
    "seal": "Seal Recognition:",
}
print(TASKS["ocr"])
def extract_text_from_image(image_input, max_tokens=1000, image_type=None):
    system_prompt = (
        "You are a professional OCR text extraction model."
        "Users will submit images; your only task is to recognize and output the text content in the images, with no explanations."
        "You must not output any text unrelated to the image content."
    )

    try:
        base64_image = image_to_png_base64(image_input)
    except Exception as e:
        return f"错误信息: {str(e)}"

    ocr_text = TASKS["table"] if image_type == "table" else TASKS["ocr"]

    max_retries = 3
    for attempt in range(max_retries):
        try:
            print('发送ocr')
            response = client.chat.completions.create(
                model=OCR_MODEL_NAME,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:image/png;base64,{base64_image}"
                                }
                            },
                            {"type": "text", "text": ocr_text}
                        ]
                    }
                ],
                stream=False,
                max_tokens=max_tokens
            )
            content = response.choices[0].message.content
            return content.replace("\n", "").replace("\r", "")
        except Exception as e:
            if attempt < max_retries - 1:
                time.sleep(1)
            else:
                return f"OCR错误信息: {str(e)}"


if __name__ == "__main__":
    image_input = "./img.png"
    text = extract_text_from_image(image_input)
    print("\n识别结果：\n", text)