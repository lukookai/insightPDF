from __future__ import annotations

import ast
from pathlib import Path


class DocLayoutYoloDetector:
    """Local ONNX DocLayout-YOLO inference without model-hub access."""

    def __init__(self, model_path: Path):
        import onnx
        import onnxruntime

        self.model_path = Path(model_path).resolve()
        if not self.model_path.is_file():
            raise FileNotFoundError(f"DocLayout-YOLO 模型不存在：{self.model_path}")
        model = onnx.load(str(self.model_path))
        metadata = {item.key: item.value for item in model.metadata_props}
        self.names = ast.literal_eval(metadata["names"])
        self.session = onnxruntime.InferenceSession(
            model.SerializeToString(), providers=["CPUExecutionProvider"]
        )

    @staticmethod
    def _prepare(image, size: int = 1024):
        import cv2
        import numpy as np

        height, width = image.shape[:2]
        ratio = min(size / height, size / width)
        resized_height = int(round(height * ratio))
        resized_width = int(round(width * ratio))
        resized = cv2.resize(
            image, (resized_width, resized_height), interpolation=cv2.INTER_LINEAR
        )
        pad_width = size - resized_width
        pad_height = size - resized_height
        top = pad_height // 2
        bottom = pad_height - top
        left = pad_width // 2
        right = pad_width - left
        padded = cv2.copyMakeBorder(
            resized,
            top,
            bottom,
            left,
            right,
            cv2.BORDER_CONSTANT,
            value=(114, 114, 114),
        )
        tensor = np.transpose(padded, (2, 0, 1)).astype("float32") / 255.0
        return tensor[None, ...], ratio, left, top

    def predict(self, image, confidence: float = 0.25) -> list[dict]:
        import numpy as np

        tensor, ratio, pad_x, pad_y = self._prepare(image)
        predictions = self.session.run(None, {"images": tensor})[0][0]
        predictions = predictions[predictions[..., 4] > confidence]
        regions = []
        for row in predictions:
            x1, y1, x2, y2 = row[:4]
            x1 = max(0.0, (float(x1) - pad_x) / ratio)
            y1 = max(0.0, (float(y1) - pad_y) / ratio)
            x2 = min(float(image.shape[1]), (float(x2) - pad_x) / ratio)
            y2 = min(float(image.shape[0]), (float(y2) - pad_y) / ratio)
            class_id = int(row[-1])
            regions.append(
                {
                    "bbox": [round(x1, 3), round(y1, 3), round(x2, 3), round(y2, 3)],
                    "label": self.names[class_id],
                    "confidence": round(float(row[-2]), 4),
                }
            )
        regions.sort(key=lambda item: item["confidence"], reverse=True)
        return regions
