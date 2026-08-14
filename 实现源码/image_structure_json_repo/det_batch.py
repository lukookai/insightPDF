import json
import time
from pathlib import Path
from queue import Queue
from typing import List, Tuple, Optional, Union

import numpy as np
import onnxruntime as ort
from PIL import Image

from ppdet.ch_ppocr_det.utils import DBPostProcess, DetPreProcess, TextDetOutput


# ======= 你只需要改这里 =======
MODEL_PATH = "ch_PP-OCRv5_server_det.onnx"   # det onnx模型路径

# 单图demo用
IMAGE_PATH = "t3.jpg"

# 多 GPU：比如 [0,1,2]
GPU_IDS = [0]  # <- 改这里

# 每个 GPU 上创建多少个 session（并发请求用；如果你主要用 batch，一般 1 就够）
SESSIONS_PER_GPU = 1  # <- 改这里

SAVE_JSON = True
OUT_JSON_PATH = "rapidocr_det_result.json"

# DB 后处理参数（和 rapidocr main.py 默认基本一致）
THRESH = 0.3
BOX_THRESH = 0.5
MAX_CANDIDATES = 1000
UNCLIP_RATIO = 1.6
USE_DILATION = True
SCORE_MODE = "fast"
# ==============================


def get_bbox_from_quad(quad, width: int, height: int):
    quad = np.asarray(quad, dtype=np.float32)
    x_coords = quad[:, 0]
    y_coords = quad[:, 1]

    x1 = float(np.clip(np.floor(np.min(x_coords)), 0, width - 1))
    y1 = float(np.clip(np.floor(np.min(y_coords)), 0, height - 1))
    x2 = float(np.clip(np.ceil(np.max(x_coords)), 0, width - 1))
    y2 = float(np.clip(np.ceil(np.max(y_coords)), 0, height - 1))

    if x2 <= x1:
        x2 = min(x1 + 1.0, width - 1)
    if y2 <= y1:
        y2 = min(y1 + 1.0, height - 1)

    return [x1, y1, x2, y2]


def _pad_to_hw(x: np.ndarray, target_h: int, target_w: int) -> np.ndarray:
    """x: [1,3,h,w] -> [1,3,target_h,target_w]，右下 padding 0"""
    if x.ndim != 4:
        raise ValueError(f"expect 4D tensor [1,C,H,W], got {x.shape}")
    n, c, h, w = x.shape
    if n != 1:
        raise ValueError(f"expect N=1 per sample before batch, got {x.shape}")

    out = np.zeros((1, c, target_h, target_w), dtype=x.dtype)
    out[:, :, :h, :w] = x
    return out


class ORTMultiGPUSessionPool:
    """ONNXRuntime 多 GPU 多 session 池（线程安全复用）

    - gpu_ids: 例如 [0,1,2]
    - sessions_per_gpu: 每个 GPU 上建多少个 session（用于并发）
    - 调度策略：全局共享一个队列，谁空闲就用谁（天然负载均衡）
    """

    def __init__(self, model_path: str, gpu_ids: Optional[List[int]] = None, sessions_per_gpu: int = 1):
        self.model_path = model_path
        self.gpu_ids = gpu_ids or []
        self.sessions_per_gpu = max(1, int(sessions_per_gpu))

        so = ort.SessionOptions()
        so.intra_op_num_threads = 1
        so.inter_op_num_threads = 1

        # # === 新增：限制显存霸占的终极杀手锏 ===
        # so.enable_mem_pattern = False
        # so.enable_cpu_mem_arena = False
        so.enable_cpu_mem_arena = False
        # so.add_session_config_entry("session.disable_prepacking", "1")
        # # ==================================

        available = ort.get_available_providers()

        self.sessions: List[ort.InferenceSession] = []
        self.session_gpu_ids: List[Optional[int]] = []

        # 1) 优先尝试 GPU（ROCm/CUDA），否则 CPU
        if ("ROCMExecutionProvider" in available or "CUDAExecutionProvider" in available) and len(self.gpu_ids) > 0:
            for gid in self.gpu_ids:
                for _ in range(self.sessions_per_gpu):
                    providers = []
                    if "ROCMExecutionProvider" in available:
                        # ROCm: device_id 常见是可用的；不同版本 ORT 可能差异，如果不认会报错
                        providers.append(("ROCMExecutionProvider", {"device_id": int(gid)}))
                    elif "CUDAExecutionProvider" in available:
                        providers.append(("CUDAExecutionProvider", {
                            "device_id": int(gid),
                            "cudnn_conv_algo_search": "HEURISTIC",
                            "arena_extend_strategy": "kNextPowerOfTwo",
                            "gpu_mem_limit": 0,
                        }))
                    providers.append(("CPUExecutionProvider", {}))

                    sess = ort.InferenceSession(model_path, sess_options=so, providers=providers)
                    self.sessions.append(sess)
                    self.session_gpu_ids.append(int(gid))
        else:
            # CPU fallback
            providers = [("CPUExecutionProvider", {})]
            for _ in range(self.sessions_per_gpu):  # CPU 下 sessions_per_gpu 就当 num_sessions
                sess = ort.InferenceSession(model_path, sess_options=so, providers=providers)
                self.sessions.append(sess)
                self.session_gpu_ids.append(None)

        if not self.sessions:
            raise RuntimeError("No ORT sessions created.")

        # 基本信息
        self.input_name = self.sessions[0].get_inputs()[0].name
        self.output_names = [o.name for o in self.sessions[0].get_outputs()]

        # 全局可用 session 队列：谁空闲用谁
        self.q: Queue[int] = Queue()
        for i in range(len(self.sessions)):
            self.q.put(i)

        # 打印一下实际 provider（便于确认是否真走 GPU）
        print("ORT available providers:", available)
        print("ORT session[0] providers:", self.sessions[0].get_providers())
        print("Total sessions:", len(self.sessions), "session_gpu_ids:", self.session_gpu_ids)

    def run(self, input_tensor: np.ndarray) -> List[np.ndarray]:
        idx = self.q.get()
        try:
            sess = self.sessions[idx]
            return sess.run(self.output_names, {self.input_name: input_tensor})
        finally:
            self.q.put(idx)


class TextDetectorGPU:
    """复用 rapidocr 的 DetPreProcess/DBPostProcess，仅替换推理为 ORT（支持 batch）"""

    def __init__(
        self,
        model_path: str,
        gpu_ids: Optional[List[int]] = None,
        sessions_per_gpu: int = 1,
        limit_side_len: int = 736,
        limit_type: str = "min",
        mean=None,
        std=None,
        post_cfg=None,
    ):
        if mean is None:
            mean = [0.5, 0.5, 0.5]
        if std is None:
            std = [0.5, 0.5, 0.5]
        if post_cfg is None:
            post_cfg = {}

        self.limit_side_len = limit_side_len
        self.limit_type = limit_type
        self.mean = mean
        self.std = std

        self.pool = ORTMultiGPUSessionPool(model_path, gpu_ids=gpu_ids, sessions_per_gpu=sessions_per_gpu)

        self.postprocess_op = DBPostProcess(
            thresh=post_cfg.get("thresh", 0.3),
            box_thresh=post_cfg.get("box_thresh", 0.5),
            max_candidates=post_cfg.get("max_candidates", 1000),
            unclip_ratio=post_cfg.get("unclip_ratio", 1.6),
            use_dilation=post_cfg.get("use_dilation", True),
            score_mode=post_cfg.get("score_mode", "fast"),
        )

    def get_preprocess(self, max_wh: int) -> DetPreProcess:
        if self.limit_type == "min":
            limit_side_len = self.limit_side_len
        elif max_wh < 960:
            limit_side_len = 960
        elif max_wh < 1500:
            limit_side_len = 1500
        else:
            limit_side_len = 2000
        return DetPreProcess(limit_side_len, self.limit_type, self.mean, self.std)

    @staticmethod
    def sorted_boxes(dt_boxes: np.ndarray):
        num_boxes = dt_boxes.shape[0]
        sorted_boxes = sorted(dt_boxes, key=lambda x: (x[0][1], x[0][0]))
        _boxes = list(sorted_boxes)

        for i in range(num_boxes - 1):
            for j in range(i, -1, -1):
                if (
                    abs(_boxes[j + 1][0][1] - _boxes[j][0][1]) < 10
                    and _boxes[j + 1][0][0] < _boxes[j][0][0]
                ):
                    _boxes[j], _boxes[j + 1] = _boxes[j + 1], _boxes[j]
                else:
                    break
        return _boxes

    def __call__(self, img: np.ndarray) -> TextDetOutput:
        return self.__call_batch__([img])[0]

    def __call_batch__(self, imgs: List[np.ndarray]) -> List[TextDetOutput]:
        """外部可调用的 batch 接口：输入多张 RGB ndarray，输出等长 TextDetOutput 列表。"""
        t0 = time.perf_counter()

        if imgs is None or len(imgs) == 0:
            return []

        ori_shapes: List[Tuple[int, int]] = [
            (im.shape[0], im.shape[1]) if im is not None else (0, 0) for im in imgs
        ]

        pre_tensors: List[Optional[np.ndarray]] = []
        hw_list: List[Tuple[int, int]] = []

        for im in imgs:
            if im is None:
                pre_tensors.append(None)
                hw_list.append((0, 0))
                continue

            preprocess_op = self.get_preprocess(max(im.shape[0], im.shape[1]))
            t = preprocess_op(im)  # [1,3,h,w]
            if t is None:
                pre_tensors.append(None)
                hw_list.append((0, 0))
            else:
                if t.ndim != 4:
                    raise ValueError(f"DetPreProcess output must be 4D [1,3,h,w], got {t.shape}")
                pre_tensors.append(t)
                hw_list.append((t.shape[2], t.shape[3]))

        valid_idx = [i for i, t in enumerate(pre_tensors) if t is not None]
        results: List[TextDetOutput] = [TextDetOutput() for _ in imgs]

        if not valid_idx:
            return results

        max_h = max(hw_list[i][0] for i in valid_idx)
        max_w = max(hw_list[i][1] for i in valid_idx)

        batch = np.concatenate(
            [_pad_to_hw(pre_tensors[i], max_h, max_w) for i in valid_idx],
            axis=0,
        )  # [B,3,max_h,max_w]

        outs = self.pool.run(batch)
        pred = outs[0]

        if pred.ndim == 3:
            pred = pred[:, None, :, :]
        elif pred.ndim != 4:
            raise ValueError(f"unexpected pred shape: {pred.shape}")

        for bi, src_i in enumerate(valid_idx):
            pred_i = pred[bi:bi + 1]
            boxes, scores = self.postprocess_op(pred_i, ori_shapes[src_i])

            if len(boxes) < 1:
                results[src_i] = TextDetOutput()
                continue

            boxes = self.sorted_boxes(boxes)
            results[src_i] = TextDetOutput(
                imgs[src_i],
                np.array(boxes),
                tuple(scores),
                elapse=(time.perf_counter() - t0),
            )

        return results


# ========== 外部可调用的便捷函数 ==========
def detect_batch(
    detector: TextDetectorGPU,
    images: List[Union[str, Path, np.ndarray, Image.Image]],
    rgb: bool = True,
) -> List[TextDetOutput]:
    """外部调用 batch 检测的便捷函数。
    images: 可以是 路径/Path/np.ndarray/PIL.Image
    rgb: True 表示输入给模型的是 RGB；若你的模型需要 BGR，可在这里统一转换
    """
    imgs_np: List[np.ndarray] = []
    for im in images:
        if isinstance(im, (str, Path)):
            arr = np.array(Image.open(str(im)).convert("RGB"))
        elif isinstance(im, Image.Image):
            arr = np.array(im.convert("RGB"))
        elif isinstance(im, np.ndarray):
            arr = im
        else:
            raise TypeError(f"unsupported image type: {type(im)}")

        if not rgb:
            arr = arr[:, :, ::-1].copy()
        imgs_np.append(arr)

    return detector.__call_batch__(imgs_np)


def main():
    model_path = Path(MODEL_PATH)
    img_path = Path(IMAGE_PATH)

    if not model_path.exists():
        raise FileNotFoundError(f"模型不存在: {model_path.resolve()}")
    if not img_path.exists():
        raise FileNotFoundError(f"图片不存在: {img_path.resolve()}")

    detector = TextDetectorGPU(
        model_path=str(model_path),
        gpu_ids=GPU_IDS,
        sessions_per_gpu=SESSIONS_PER_GPU,
        limit_side_len=736,
        limit_type="min",
        mean=[0.5, 0.5, 0.5],
        std=[0.5, 0.5, 0.5],
        post_cfg={
            "thresh": THRESH,
            "box_thresh": BOX_THRESH,
            "max_candidates": MAX_CANDIDATES,
            "unclip_ratio": UNCLIP_RATIO,
            "use_dilation": USE_DILATION,
            "score_mode": SCORE_MODE,
        },
    )

    # demo：batch 里放多张（这里用同一张图重复举例）
    batch_inputs = [img_path, img_path]  # 你可以换成多个路径
    results = detect_batch(detector, batch_inputs, rgb=True)

    # demo：只把第一张结果存 json（你也可以循环每张分别保存）
    img0 = Image.open(str(img_path)).convert("RGB")
    w, h = img0.size

    res0 = results[0]
    boxes0 = [] if res0.boxes is None else res0.boxes

    output_list = []
    for quad in boxes0:
        bbox = get_bbox_from_quad(quad, w, h)
        output_list.append(
            {
                "src_image": img_path.name,
                "image_type": "plain text",
                "bbox": bbox,
                "content": "",
                "translated_content": "",
                "background-color": "",
                "content_type": "plain",
                "is_chil_text": True,
            }
        )

    print("=== det-only result (batch inference, multi-gpu scheduling) ===")
    print(json.dumps(output_list, ensure_ascii=False, indent=2))

    if SAVE_JSON:
        outp = Path(OUT_JSON_PATH)
        outp.parent.mkdir(parents=True, exist_ok=True)
        outp.write_text(json.dumps(output_list, ensure_ascii=False, indent=2), encoding="utf-8")
        print(f"已保存: {outp.resolve()}")


if __name__ == "__main__":
    main()