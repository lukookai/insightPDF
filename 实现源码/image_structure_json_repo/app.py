
try:
    # 包内相对导入
    from .main_image_structure_json import (
        step1_extract_image_text,
        step2_auto_translate_json,
        step3_build_html_and_screenshots,
        step4_pdf_and_convert
    )
except ImportError:
    # 绝对导入
    from main_image_structure_json import (
        step1_extract_image_text,
        step2_auto_translate_json,
        step3_build_html_and_screenshots,
        step4_pdf_and_convert
    )
import os
from fastapi import FastAPI, UploadFile, File, Form, Body
from fastapi.responses import JSONResponse, FileResponse
from typing import List, Optional, Union
from pydantic import BaseModel
import shutil
import uvicorn

from fastapi.middleware.cors import CORSMiddleware  # <-- 新增

# 导入你原有的四大步骤函数

app = FastAPI()

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有域名跨域
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# =========================
# 请求体模型
class Step1Request(BaseModel):
    img_paths: List[str]
    out_json: str
    ocr_works: Optional[int] = 10
    gpu_ids: Optional[List[int]] = None

class Step2Request(BaseModel):
    json_path: str

class Step3Request(BaseModel):
    img_paths: List[str]
    json_path: str
    type: Optional[str] = 'png'
    delete_html: Optional[bool] = True

class Step4Request(BaseModel):
    html_files: List[dict]
    convert_type: Optional[str] = None

# =========================
# 步骤1：图片转json
@app.post("/step1_extract_image_text")
def api_step1(req: Step1Request):
    abs_json = step1_extract_image_text(
        req.img_paths,
        req.out_json,
        req.ocr_works,
        req.gpu_ids
    )
    return {"abs_json": abs_json}

# 步骤2：自动翻译json
@app.post("/step2_auto_translate_json")
def api_step2(req: Step2Request):
    step2_auto_translate_json(req.json_path)
    return {"status": "ok"}

# 步骤3：构建html并截图
@app.post("/step3_build_html_and_screenshots")
def api_step3(req: Step3Request):
    html_files, screenshots = step3_build_html_and_screenshots(
        req.img_paths,
        req.json_path,
        type=req.type,
        delete_html=req.delete_html
    )
    return {"html_files": html_files, "screenshots": screenshots}

# 步骤4：生成pdf和emf/svg
@app.post("/step4_pdf_and_convert")
def api_step4(req: Step4Request):
    pdf_results = step4_pdf_and_convert(req.html_files, convert_type=req.convert_type)
    return {"pdf_results": pdf_results}

# =========================
# 支持文件上传（可选）
@app.post("/upload_img/")
def upload_img(file: UploadFile = File(...)):
    save_dir = "uploaded_imgs"
    os.makedirs(save_dir, exist_ok=True)
    file_path = os.path.join(save_dir, file.filename)
    with open(file_path, "wb") as buffer:
        shutil.copyfileobj(file.file, buffer)
    return {"img_path": os.path.abspath(file_path)}

# =========================
# 启动
if __name__ == "__main__":
    uvicorn.run("app:app", host="0.0.0.0", port=8870)