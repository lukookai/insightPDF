from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.responses import JSONResponse, FileResponse
import shutil
import os
import tempfile
import json

from utils import timer
from extract_all_pages import extract_all_pages
from backfill import main as backfill_main

app = FastAPI()

base_dir = os.path.join(os.getcwd(), "temp")
dirs = {
    "input_pdf": os.path.join(base_dir, "input_pdf"),
    "output_pdf": os.path.join(base_dir, "output_pdf"),
}
for path in dirs.values():
    os.makedirs(path, exist_ok=True)

def str2bool(v):
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() == "true"
    return False

@app.post("/extract_json")
@timer
async def extract_json(
    pdf_file: UploadFile = File(...),
    enable_image: str = Form("False")
):
    if pdf_file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid file type. Only PDF allowed.")

    enable_image_bool = str2bool(enable_image)

    base_name = os.path.splitext(pdf_file.filename)[0]
    out_dir = os.path.join(dirs["output_pdf"], base_name)
    os.makedirs(out_dir, exist_ok=True)
    out_json_path = os.path.join(out_dir, f"{base_name}_allpages.json")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, pdf_file.filename)
        with open(pdf_path, "wb") as f:
            shutil.copyfileobj(pdf_file.file, f)

        result_dict, out_json_path = extract_all_pages(pdf_path, enable_image=enable_image_bool)
        return JSONResponse(content=result_dict)

@app.post("/backfill_pdf")
@timer
async def backfill_pdf(
    pdf_file: UploadFile = File(...),
    target_lang: str = Form("zh"),
    bilingual_pdf: bool = Form(False),
    enable_image: str = Form("False")
):
    if pdf_file.content_type != "application/pdf":
        raise HTTPException(status_code=400, detail="Invalid PDF file.")

    enable_image_bool = str2bool(enable_image)

    base_name = os.path.splitext(pdf_file.filename)[0]
    out_dir = os.path.join(dirs["output_pdf"], base_name)
    json_path = os.path.join(out_dir, f"{base_name}_allpages.json")
    if not os.path.exists(json_path):
        raise HTTPException(status_code=400, detail=f"JSON not found. Please extract first: {json_path}")

    with tempfile.TemporaryDirectory() as tmpdir:
        pdf_path = os.path.join(tmpdir, pdf_file.filename)
        with open(pdf_path, "wb") as f:
            shutil.copyfileobj(pdf_file.file, f)

        if target_lang == "zh-CN":
            target_lang = "zh"

        backfill_main(pdf_path, target_lang, bilingual_pdf, enable_image=enable_image_bool)

        out_pdf = os.path.join(os.path.dirname(pdf_path), f"{base_name}_{target_lang}.pdf")
        if not os.path.exists(out_pdf):
            raise HTTPException(status_code=500, detail="PDF backfill failed.")

        final_out_pdf = os.path.join(out_dir, f"{base_name}_{target_lang}.pdf")
        shutil.copy(out_pdf, final_out_pdf)

        return FileResponse(
            final_out_pdf,
            media_type="application/pdf",
            filename=os.path.basename(final_out_pdf)
        )

if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8502)
