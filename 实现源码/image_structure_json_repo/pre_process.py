import copy
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple, Union

import cv2
import numpy as np

InputType = Union[str, np.ndarray, bytes, Path]


class PPDocLayoutPreProcess:
    def __init__(self, img_size: Tuple[int, int]):
        # img_size: (H, W) 或 (800,800) 这种；你的原代码按 (H,W) 用
        self.size = [int(img_size[0]), int(img_size[1])]

        self.mean = [0.0, 0.0, 0.0]
        self.std = [1.0, 1.0, 1.0]
        self.scale = 1 / 255.0
        self.alpha = [self.scale / self.std[i] for i in range(len(self.std))]
        self.beta = [-self.mean[i] / self.std[i] for i in range(len(self.std))]

    def __call__(self, img: Optional[np.ndarray] = None) -> Tuple[Dict[str, Any], List[np.ndarray]]:
        """
        单张图：
          return ori_data(dict), batch_inputs([im_shape(1,2), image(1,3,H,W), scale_factor(1,2)])
        """
        if img is None:
            raise ValueError("img is None.")

        data = self.resize(img)
        data = self.normalize(data)
        data = self.permute(data)
        ori_data = copy.deepcopy(data)
        batch_inputs = self.to_batch(data)  # batch=1
        return ori_data, batch_inputs

    def batch(self, imgs: List[np.ndarray]) -> Tuple[List[Dict[str, Any]], List[np.ndarray]]:
        """
        原生batch：把 N 张图拼成一个 batch 输入给 ONNX。
        return:
          ori_datas: List[ori_data] (len=N)
          batch_inputs: [im_shape(N,2), image(N,3,H,W), scale_factor(N,2)]
        """
        if imgs is None or len(imgs) == 0:
            raise ValueError("imgs is empty.")

        ori_datas: List[Dict[str, Any]] = []
        im_shapes: List[np.ndarray] = []
        images: List[np.ndarray] = []
        scale_factors: List[np.ndarray] = []

        for img in imgs:
            if img is None:
                raise ValueError("one of imgs is None.")
            ori_data, batch_inputs_1 = self(img)  # 复用单图逻辑，得到 (1,...) 的三路输入
            ori_datas.append(ori_data)
            im_shapes.append(batch_inputs_1[0])      # (1,2)
            images.append(batch_inputs_1[1])         # (1,3,H,W)
            scale_factors.append(batch_inputs_1[2])  # (1,2)

        im_shape = np.concatenate(im_shapes, axis=0)
        image = np.concatenate(images, axis=0)
        scale_factor = np.concatenate(scale_factors, axis=0)

        return ori_datas, [im_shape, image, scale_factor]

    def resize(self, img: np.ndarray) -> Dict[str, Any]:
        resize_h, resize_w = self.size
        img_ori_h, img_ori_w = img.shape[:2]

        img = cv2.resize(img, (int(resize_w), int(resize_h)), interpolation=cv2.INTER_CUBIC)
        img_h, img_w = img.shape[:2]
        data = {
            "img": img,
            "img_size": [img_w, img_h],
            "scale_factors": [img_w / img_ori_w, img_h / img_ori_h],
            "ori_img_size": [img_ori_w, img_ori_h],
        }
        return data

    def normalize(self, data: Dict[str, Any]) -> Dict[str, Any]:
        img = data["img"]
        split_im = list(cv2.split(img))
        for c in range(img.shape[2]):
            split_im[c] = split_im[c].astype(np.float32)
            split_im[c] *= self.alpha[c]
            split_im[c] += self.beta[c]

        res = cv2.merge(split_im)
        data["img"] = res
        return data

    def permute(self, data: Dict[str, Any]) -> Dict[str, Any]:
        img = data["img"]
        data["img"] = img.transpose((2, 0, 1))
        return data

    def to_batch(self, data: Dict[str, Any], dtype: np.dtype = np.float32) -> List[np.ndarray]:
        """
        单样本打 batch=1（保持你原逻辑）
        输出顺序对应 onnx 输入：im_shape, image, scale_factor
        """
        result = []
        for key in ["img_size", "img", "scale_factors"]:
            if key == "img_size":
                val = [data[key][::-1]]  # [H,W]
            elif key == "scale_factors":
                val = [data.get(key, [1.0, 1.0])[::-1]]
            else:
                val = [data[key]]
            result.append(np.array(val, dtype=dtype))
        return result