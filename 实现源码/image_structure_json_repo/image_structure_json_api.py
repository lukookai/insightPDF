import io
import uuid

import asyncio
import logging
from pathlib import Path

import os

from typing import List, Optional, Union

import shutil

import cv2
import numpy as np
from PIL import Image

from fastapi import FastAPI, UploadFile, File
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from contextlib import asynccontextmanager

from playwright.async_api import async_playwright
from fastapi.concurrency import run_in_threadpool

try:
    from configs import modeld_name, local_model_dir, gpu_ids, SESSIONS_PER_GPU
    from reorder_boxes import format_detection_result
    # 包内相对导入
    from .main_image_structure_json import (
        step1_extract_image_text,
        step2_auto_translate_json,
        step3_build_html_and_screenshots,
        step4_pdf_and_convert
    )
    # 引用 det_batch（相对导入）
    from .det_batch import TextDetectorGPU, get_bbox_from_quad

    # 引入 main.py 里的多GPU多session layout 引擎
    from .doclayout_batch import create_layout_engine
except ImportError:
    from configs import model_name, local_model_dir, gpu_ids, SESSIONS_PER_GPU
    from reorder_boxes import format_detection_result
    # 绝对导入
    from main_image_structure_json import (
        step1_extract_image_text,
        step2_auto_translate_json,
        step3_build_html_and_screenshots,
        step4_pdf_and_convert
    )
    from det_batch import TextDetectorGPU, get_bbox_from_quad

    # 引入 main.py 里的多GPU多session layout 引擎
    from doclayout_batch import  create_layout_engine

from fastapi.middleware.cors import CORSMiddleware  # <-- 新增

# ==================
# 日志与常量设置
# ==================
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ==================
# FastAPI 应用与模型预加载
# ==================

# det 模型（保持不变）
model_path = "ch_PP-OCRv5_server_det.onnx"
engine = None  # 不再使用 RapidOCR 做 det，但保留变量避免其它地方引用报错

# det 合批参数（保持不变）
BATCH_MAX_SIZE = 20
BATCH_MAX_WAIT_MS = 50

POST_CFG = {
    "thresh": 0.3,
    "box_thresh": 0.5,
    "max_candidates": 1000,
    "unclip_ratio": 1.6,
    "use_dilation": True,
    "score_mode": "fast",
}

LAYOUT_ONNX_PATH = "./cc_v3.onnx"


class BatchItem:
    __slots__ = ("img_np", "filename", "w", "h", "future")

    def __init__(self, img_np: np.ndarray, filename: str, w: int, h: int, future: asyncio.Future):
        self.img_np = img_np
        self.filename = filename
        self.w = w
        self.h = h
        self.future = future


# ------------------
# OCR DET 合批 worker（原来的 batch_predict_worker，改名避免被覆盖）
# ------------------
async def det_batch_predict_worker():
    q: asyncio.Queue[BatchItem] = app.state.det_queue
    detector: TextDetectorGPU = app.state.detector
    max_wait = BATCH_MAX_WAIT_MS / 1000.0

    while True:
        batch: List[BatchItem] = []
        try:
            first = await q.get()
            batch = [first]
            start_t = asyncio.get_running_loop().time()

            while len(batch) < BATCH_MAX_SIZE:
                remaining = max_wait - (asyncio.get_running_loop().time() - start_t)
                if remaining <= 0:
                    break
                try:
                    item = await asyncio.wait_for(q.get(), timeout=remaining)
                    batch.append(item)
                except asyncio.TimeoutError:
                    break

            imgs = [it.img_np for it in batch]
            det_results = await asyncio.to_thread(detector.__call_batch__, imgs)

            for it, res in zip(batch, det_results):
                try:
                    boxes = [] if (res is None or res.boxes is None) else res.boxes

                    output_list = []
                    for quad in boxes:
                        bbox = get_bbox_from_quad(quad, it.w, it.h)
                        output_list.append({
                            "src_image": it.filename,
                            "image_type": "plain text",
                            "bbox": bbox,
                            "content": "",
                            "translated_content": "",
                            "background-color": "",
                            "content_type": "plain",
                            "is_chil_text": True
                        })

                    if not it.future.done():
                        it.future.set_result(output_list)
                except Exception as e:
                    if not it.future.done():
                        it.future.set_exception(e)

        except asyncio.CancelledError:
            for it in batch:
                if not it.future.done():
                    it.future.set_exception(asyncio.CancelledError("worker cancelled"))
            break
        except Exception as e:
            for it in batch:
                if not it.future.done():
                    it.future.set_exception(e)
            continue


@asynccontextmanager
async def lifespan(app: FastAPI):
    playwright = await async_playwright().start()
    browser = await playwright.chromium.launch()
    app.state.playwright = playwright
    app.state.browser = browser

    # 初始化 det_batch detector（只加载一次）
    print('gpuids', gpu_ids)

    app.state.detector = TextDetectorGPU(
        model_path=model_path,
        gpu_ids=gpu_ids,
        sessions_per_gpu=SESSIONS_PER_GPU,
        limit_side_len=736,
        limit_type="min",
        mean=[0.5, 0.5, 0.5],
        std=[0.5, 0.5, 0.5],
        post_cfg=POST_CFG,
    )
    app.state.det_queue = asyncio.Queue()


    from configs import local_model_dir,model_name
    app.state.layout_engine = create_layout_engine(
        model_path=local_model_dir,
        gpu_ids=gpu_ids,
        num_sessions_per_gpu=SESSIONS_PER_GPU,
        model_name=model_name
    )

    # 启动 det 合批 worker
    det_worker_task = asyncio.create_task(det_batch_predict_worker())

    # 启动 layout 合批 worker（/predict 使用）
    layout_worker_task = asyncio.create_task(layout_batch_predict_worker())

    try:
        yield
    finally:
        det_worker_task.cancel()
        layout_worker_task.cancel()

        try:
            await det_worker_task
        except Exception:
            pass

        try:
            await layout_worker_task
        except Exception:
            pass

        # 清空 det 队列，避免请求悬挂
        q: asyncio.Queue[BatchItem] = app.state.det_queue
        while not q.empty():
            try:
                it = q.get_nowait()
            except Exception:
                break
            if not it.future.done():
                it.future.set_exception(asyncio.CancelledError("server shutting down"))

        # 清空 layout 队列，避免请求悬挂
        async with queue_lock:
            for task in predict_queue:
                if not task.future.done():
                    task.future.set_exception(asyncio.CancelledError("server shutting down"))
            predict_queue.clear()

        # 关闭 layout engine
        try:
            app.state.layout_engine.close()
        except Exception:
            pass

        await browser.close()
        await playwright.stop()


app = FastAPI(lifespan=lifespan)

# 允许跨域
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],  # 允许所有域名跨域
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.post("/ocr_detect")
async def ocr_detect(file: UploadFile = File(...)):
    contents = await file.read()
    img_obj = Image.open(io.BytesIO(contents)).convert("RGB")
    width, height = img_obj.size
    img_np = np.array(img_obj)  # RGB
    logger.info(msg='开始ocrdet')

    loop = asyncio.get_running_loop()
    fut: asyncio.Future = loop.create_future()
    await app.state.det_queue.put(BatchItem(img_np=img_np, filename=file.filename, w=width, h=height, future=fut))
    return await fut


# ==================
# 2. 合批队列和任务定义
# ==================
class PredictTask:
    def __init__(self, id: str, images: List[Image.Image], filenames: List[str], future: asyncio.Future):
        self.id = id
        self.images = images
        self.filenames = filenames
        self.future = future
        self.api_results = []  # 【修改点1】：新增，用来暂存分批切片处理的结果


predict_queue: List[PredictTask] = []
queue_lock = asyncio.Lock()


# ==================
# 4. 合批 worker（/predict 使用）
# ==================
async def layout_batch_predict_worker():
    while True:
        await asyncio.sleep(0.05)

        extracted_imgs = []
        extracted_filenames = []
        batch_tasks_info = []
        max_batch_size = 50  # 【修改点2】：设置安全的合批大小。避免 OOM

        async with queue_lock:
            if not predict_queue:
                continue

            # 【修改点3】：按“图片”数量切片凑够 batch，而不是按“任务”出队
            while predict_queue and len(extracted_imgs) < max_batch_size:
                task = predict_queue[0]
                needed = max_batch_size - len(extracted_imgs)

                # 从当前任务中切出所需的图片
                imgs_to_take = task.images[:needed]
                names_to_take = task.filenames[:needed]

                extracted_imgs.extend(imgs_to_take)
                extracted_filenames.extend(names_to_take)

                # 记录这次从哪个任务切了多少张图，方便一会儿归还结果
                batch_tasks_info.append({
                    "task": task,
                    "count": len(imgs_to_take),
                    "start_idx": len(extracted_imgs) - len(imgs_to_take)
                })

                # 更新任务剩余的图片
                task.images = task.images[needed:]
                task.filenames = task.filenames[needed:]

                # 如果这个任务的图片全被抽走了，再把它从队列里踢出
                if len(task.images) == 0:
                    predict_queue.pop(0)

        if not extracted_imgs:
            continue

        # 将取出的图片转换为模型需要的格式 (BGR格式的 numpy array)
        all_imgs_np: List[np.ndarray] = []
        for img in extracted_imgs:
            rgb = np.array(img, dtype=np.uint8)
            bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
            all_imgs_np.append(bgr)

        # ==========================================
        # 送入模型推理：【单线程一次性提交合批】
        # ==========================================
        try:
            # 修改这里！只需要开 1 个线程，把几十张图的 List 一把梭哈喂给引擎
            # 让 Paddle 底层的 C++ 引擎自己去利用 GPU 并行计算这个 Batch
            outputs = await asyncio.to_thread(
                app.state.layout_engine.run,
                all_imgs_np
            )

        except Exception as e:
            # 异常处理：如果有报错，把牵扯到的所有任务都返回报错，并清空剩余图片防止死锁
            for info in batch_tasks_info:
                t = info["task"]
                t.images = []
                if not t.future.done():
                    t.future.set_result({"results": [], "error": str(e)})
            continue

        # 【修改点4】：将处理结果精确分发回对应的任务“篮子”
        for info in batch_tasks_info:
            t = info["task"]
            count = info["count"]
            start_idx = info["start_idx"]

            task_results = outputs[start_idx: start_idx + count]
            task_filenames = extracted_filenames[start_idx: start_idx + count]

            for i, one_img_boxes in enumerate(task_results):
                t.api_results.append({
                    "filename": task_filenames[i],
                    "boxes": one_img_boxes,
                })

            # 判断：如果这个任务的所有图片都已经处理完毕，则格式化结果并返回给前端
            if len(t.images) == 0:
                formatted = format_detection_result(t.api_results)
                if not t.future.done():
                    t.future.set_result({"results": formatted})


# ==================
# 5. FastAPI 路由
# ==================
@app.post("/predict")
async def predict(files: List[UploadFile] = File(...)):
    imgs = []
    filenames = []
    for file in files:
        contents = await file.read()
        img = Image.open(io.BytesIO(contents)).convert("RGB")
        imgs.append(img)
        filenames.append(file.filename)
    if not imgs:
        return JSONResponse(status_code=400, content={"error": "未上传任何有效图片"})

    loop = asyncio.get_event_loop()
    future = loop.create_future()
    task = PredictTask(
        id=str(uuid.uuid4()),
        images=imgs,
        filenames=filenames,
        future=future
    )
    async with queue_lock:
        predict_queue.append(task)

    result = await future
    return result


@app.get("/ping")
def ping():
    return {"msg": "ok"}

# 启动命令（命令行执行）：






class HtmlOutputPair(BaseModel):
    html: str
    output: str
    width: Optional[int] = 1280
    height: Optional[int] = 720
    type: Optional[str] = "png"

class ScreenshotRequest(BaseModel):
    html_and_output: List[HtmlOutputPair]




# 支持的图片格式映射
PILLOW_FORMATS = {
    "png": "PNG",
    "jpg": "JPEG",
    "jpeg": "JPEG"
    # 你可以扩展支持的格式: "webp": "WEBP", "bmp": "BMP", "tiff": "TIFF", "gif": "GIF"
}

def get_final_format(img_type: str):
    """
    返回最终使用的格式和扩展名。
    不支持的格式一律返回png
    """
    img_type_lower = (img_type or "png").lower()
    if img_type_lower in PILLOW_FORMATS:
        return PILLOW_FORMATS[img_type_lower], img_type_lower
    # 其它全部转为png
    return "PNG", "png"


@app.post("/screenshot")
async def screenshot_fullpage(req: ScreenshotRequest):
    html_and_output = req.html_and_output
    base_dir = Path('.').resolve()
    browser = app.state.browser
    # 建议根据服务器性能适当调低此值，如 20-40，100 并发对显存压力极大
    sem = asyncio.Semaphore(10)

    async def process_html(html_path, output_path, width, height, img_type):
        async with sem:
            temp_html_path = None
            tmp_png_path = None
            final_png_path = None

            # --- 核心修改：显式创建 context ---
            context = await browser.new_context(
                viewport={"width": width, "height": height},
                device_scale_factor=1
            )
            page = await context.new_page()
            # -------------------------------

            try:
                html_path_obj = Path(html_path).absolute()
                output_name = Path(output_path).stem

                temp_dir = base_dir / "temp_file"
                temp_dir.mkdir(exist_ok=True)

                temp_uid = uuid.uuid4().hex
                temp_html_name = f"{output_name}_{temp_uid}.html"
                temp_html_path = temp_dir / temp_html_name
                shutil.copy2(str(html_path_obj), str(temp_html_path))

                html_dir = html_path_obj.parent
                final_output_path = html_dir / output_name

                file_url = temp_html_path.absolute().as_uri()

                await page.goto(file_url, wait_until="load")

                img_type_lower = (img_type or "png").lower()
                result = {
                    "html": html_path,
                    "width": width,
                    "height": height,
                    "type": img_type_lower
                }

                tmp_png_name = f"{output_name}_{temp_uid}.tmp.png"
                tmp_png_path = temp_dir / tmp_png_name
                final_png_name = f"{output_name}_{temp_uid}.png"
                final_png_path = temp_dir / final_png_name

                await page.screenshot(
                    path=str(tmp_png_path),
                    full_page=True,
                    omit_background=True,
                    type='png'
                )

                if not tmp_png_path.exists():
                    # 确保异常退出前销毁资源
                    await context.close()
                    if temp_html_path and temp_html_path.exists():
                        os.remove(temp_html_path)
                    return {"html": html_path, "error": f"screenshot failed, file not found: {tmp_png_path}"}

                try:
                    with Image.open(tmp_png_path) as im:
                        im.save(final_png_path, format="PNG")
                except Exception as e:
                    if tmp_png_path and tmp_png_path.exists():
                        os.remove(tmp_png_path)
                    await context.close()
                    if temp_html_path and temp_html_path.exists():
                        os.remove(temp_html_path)
                    return {"html": html_path, "error": f"Pillow PNG convert failed: {e}"}

                if tmp_png_path.exists():
                    os.remove(tmp_png_path)

                final_png_dest = final_output_path.with_suffix('.png')
                shutil.move(str(final_png_path), str(final_png_dest))
                result["png_file"] = str(final_png_dest)

                if img_type_lower == "pdf":
                    final_pdf_path = temp_dir / f"{output_name}_{temp_uid}.pdf"
                    final_pdf_dest = final_output_path.with_suffix('.pdf')
                    width_in_inch = round(width / 96, 6)
                    height_in_inch = round(height / 96, 6)
                    await page.pdf(
                        path=str(final_pdf_path),
                        width=f"{width_in_inch}in",
                        height=f"{height_in_inch}in",
                        print_background=True
                    )
                    shutil.move(str(final_pdf_path), str(final_pdf_dest))
                    result["pdf_file"] = str(final_pdf_dest)
                    result["status"] = "ok"
                else:
                    pillow_format, ext = get_final_format(img_type_lower)
                    if pillow_format != "PNG":
                        final_path = temp_dir / f"{output_name}_{temp_uid}.{ext}"
                        final_dest = final_output_path.with_suffix(f'.{ext}')
                        try:
                            with Image.open(final_png_dest) as im:
                                if pillow_format == "JPEG":
                                    im = im.convert("RGB")
                                im.save(final_path, format=pillow_format)
                            shutil.move(str(final_path), str(final_dest))
                            result["file"] = str(final_dest)
                        except Exception as e:
                            await context.close()
                            if temp_html_path and temp_html_path.exists():
                                os.remove(temp_html_path)
                            return {"html": html_path, "error": f"Pillow convert failed: {e}"}
                    else:
                        result["file"] = str(final_png_dest)
                    result["status"] = "ok"

                # 正常结束释放
                await context.close()
                if temp_html_path and temp_html_path.exists():
                    os.remove(temp_html_path)
                return result

            except Exception as e:
                # 异常逻辑释放
                try:
                    await context.close()
                    if temp_html_path and temp_html_path.exists():
                        os.remove(temp_html_path)
                    if tmp_png_path and tmp_png_path.exists():
                        os.remove(tmp_png_path)
                    if final_png_path and final_png_path.exists():
                        os.remove(final_png_path)
                except Exception:
                    pass
                return {"html": html_path, "error": str(e)}

    all_results = await asyncio.gather(
        *(process_html(item.html, item.output, item.width, item.height, item.type or "png") for item in html_and_output)
    )
    # 处理完一批后，建议显式触发一次 GC 清理 Pillow 和系统残留
    import gc
    gc.collect()

    return all_results

class Step1Request(BaseModel):
    img_paths: List[str]
    out_json: str
    ocr_works: Optional[int] = 10
    gpu_ids: Optional[List[int]] = None
    extract_child: Optional[bool] = True  # 新增
    filtr_type: Optional[List[str]] = []
    extract_bg_color: Optional[bool] = True  # 新增
    child_type: Optional[List[str]] = []
    ban_type: Optional[List[str]] = []


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
async def api_step1(req: Step1Request):
    logger.info(msg='step1开始处理')
    print(req.ban_type)
    abs_json = await run_in_threadpool(
        step1_extract_image_text,
        img_paths=req.img_paths,
        out_json=req.out_json,
        ocr_works=req.ocr_works,
        gpu_ids=req.gpu_ids,
        extract_child=req.extract_child,
        filtr_type=req.filtr_type,
        extract_bg_color=req.extract_bg_color,
        child_type=req.child_type,
        ban_type=req.ban_type,
    )

    return {"abs_json": abs_json}

# 步骤2：自动翻译json
# 步骤2：自动翻译json
@app.post("/step2_auto_translate_json")
async def api_step2(req: Step2Request):
    await run_in_threadpool(
        step2_auto_translate_json,
        json_path=req.json_path,
    )
    return {"status": "ok"}


# 步骤3：构建html并截图
@app.post("/step3_build_html_and_screenshots")
async def api_step3(req: Step3Request):
    html_files, screenshots = await run_in_threadpool(
        step3_build_html_and_screenshots,
        img_paths=req.img_paths,
        json_path=req.json_path,
        type=req.type,
        delete_html=req.delete_html,
    )
    return {"html_files": html_files, "screenshots": screenshots}


# 步骤4：生成pdf和emf/svg
@app.post("/step4_pdf_and_convert")
async def api_step4(req: Step4Request):
    pdf_results = await run_in_threadpool(
        step4_pdf_and_convert,
        html_files=req.html_files,
        convert_type=req.convert_type,
    )
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

if __name__ == "__main__":
    import uvicorn
    uvicorn.run("image_structure_json_api:app", host="0.0.0.0", port=8870, reload=False)


