import os

#
# OCR_URL =  "https://api.siliconflow.cn/v1" #"https://api.siliconflow.cn/v1"
# ocr_detect_api = "http://localhost:8870/ocr_detect"
# doclayout_api = "http://127.0.0.1:8870/predict"
# OCR_MODEL_NAME = "Pro/Qwen/Qwen2.5-VL-7B-Instruct"#'Pro/Qwen/Qwen2.5-VL-7B-Instruct'
# # OCR_MODEL_NAME = 'CapRL-Eval-3B'
# OCR_API_KEY = os.environ.get("OCR_API_KEY", "")
# screen_shoot_api = "http://127.0.0.1:8870/screenshot"
# local_model_dir = r"./layout"
# model_name="PP-DocLayoutV2"
# gpu_ids =[0]
# ocr_max_works = 10
# child_type=["table",]
# ban_type = ['table'] # 不同type是否提取text开关,如果child_type包括table，ban_type必须包括table
# SESSIONS_PER_GPU = 1
# LABELS = ['abstract', 'algorithm', 'aside_text', 'chart', 'content', 'display_formula', 'doc_title', 'figure_title', 'footer', 'footer_image', 'footnote', 'formula_number', 'header', 'header_image', 'image', 'inline_formula', 'number', 'paragraph_title', 'reference', 'reference_content', 'seal', 'table', 'text', 'vertical_text', 'vision_footnote']
#
# 生产服务ocr_url http://127.0.0.1:7867/v1/models


OCR_URL =  "http://117.50.44.212:7867/v1" #"https://api.siliconflow.cn/v1"
ocr_detect_api = "http://localhost:8870/ocr_detect"
doclayout_api = "http://127.0.0.1:8870/predict"
OCR_MODEL_NAME = "PaddleOCR-VL"#'Pro/Qwen/Qwen2.5-VL-7B-Instruct'
ocr_max_works = 100
mode=1
if mode == 2:
    OCR_URL = "https://api.siliconflow.cn/v1"  # ""
    OCR_MODEL_NAME = "Qwen/Qwen3-VL-32B-Instruct"  # ''
    ocr_max_works = 10

OCR_API_KEY = os.environ.get("OCR_API_KEY", "")
screen_shoot_api = "http://127.0.0.1:8870/screenshot"
local_model_dir = r"./layout"
model_name="PP-DocLayoutV2"
gpu_ids =[]

child_type=["table",]
ban_type = ['table'] # 不同type是否提取text开关,如果child_type包括table，ban_type必须包括table
SESSIONS_PER_GPU = 1
LABELS = ['abstract', 'algorithm', 'aside_text', 'chart', 'content', 'display_formula', 'doc_title', 'figure_title', 'footer', 'footer_image', 'footnote', 'formula_number', 'header', 'header_image', 'image', 'inline_formula', 'number', 'paragraph_title', 'reference', 'reference_content', 'seal', 'table', 'text', 'vertical_text', 'vision_footnote']


