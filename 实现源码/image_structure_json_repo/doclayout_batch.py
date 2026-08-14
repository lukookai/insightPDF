import os
from typing import Any, List, Union

import cv2
import numpy as np
from PIL import Image
from paddleocr import LayoutDetection


# ==========================================
# 高效批量推理引擎 (纯数据内存返回)
# ==========================================
class LayoutEngine:
    """
    基于 paddleocr.LayoutDetection 的批量推理引擎。
    直接利用底层自带的 batch 处理能力，全内存流转，无磁盘 I/O 开销。
    """

    def __init__(
            self,
            model_dir: str = r"./layout2",
            model_name: str = "PP-DocLayoutV3",
            gpu_ids: list = [],
            num_sessions_per_gpu: int = 1  # 兼容以前的参数格式
    ):
        self.model_dir = model_dir
        self.model_name = model_name
        self.gpu_ids = gpu_ids

        # 决定设备
        self.device_str = "gpu:" + ",".join(str(i) for i in self.gpu_ids) if self.gpu_ids else 'cpu'
        self.use_mkldnn = False
        if not gpu_ids:
            print("使用 CPU 进行推理")
            self.use_mkldnn = True

        # 初始化单实例模型即可，batching 会自动吃满算力
        self.model = LayoutDetection(
            model_dir=self.model_dir,
            model_name=self.model_name,
            device=self.device_str,
            enable_mkldnn=self.use_mkldnn
        )

    def close(self):
        # 兼容 app.py 中的清理逻辑
        pass

    def run(self, imgs: Union[List[Any], Any], *args, **kwargs):
        """
        接收单张或批量内存图片数据，执行原生批量推理 (纯内存操作)
        """
        # 统一将输入转为 List，方便批量处理
        if not isinstance(imgs, list):
            imgs = [imgs]

        input_imgs_np = []

        # 1. 统一内存数据格式为 numpy.ndarray (BGR)
        for img_data in imgs:
            if isinstance(img_data, Image.Image):
                # 如果是 PIL Image (RGB)，转为 numpy 数组 (BGR，符合 OpenCV/Paddle 默认习惯)
                img_np = cv2.cvtColor(np.array(img_data), cv2.COLOR_RGB2BGR)
                input_imgs_np.append(img_np)
            elif isinstance(img_data, np.ndarray):
                # 如果已经是 numpy 数组，直接放入
                input_imgs_np.append(img_data)
            else:
                raise TypeError(f"不支持的图片数据格式: {type(img_data)}")

        # 2. 核心：调用原生 API 实现真正的合批推理！直接喂内存数据！
        # 直接传入 List[np.ndarray]
        outputs = self.model.predict(input_imgs_np, batch_size=len(input_imgs_np), layout_nms=True)

        # 3. 批量处理坐标裁剪逻辑
        batch_final_results = []

        for res in outputs:
            all_boxes = []
            for box in res['boxes']:
                all_boxes.append({
                    'label': box['label'],
                    'coordinate': box['coordinate'],
                    'score': box['score'],
                    'cls_id': box.get('cls_id', -1)
                })

            title_boxes = [b for b in all_boxes if b['label'].lower() == 'paragraph_title']
            final_boxes = []

            for box in all_boxes:
                if box['label'].lower() == 'text':
                    t_rect = box['coordinate']
                    tx1, ty1, tx2, ty2 = t_rect
                    y_ranges = [[ty1, ty2]]
                    was_cut = False

                    for title in title_boxes:
                        px1, py1, px2, py2 = title['coordinate']
                        if tx1 <= px1 and tx2 >= px2 and ty1 <= py1 and ty2 >= py2:
                            new_ranges = []
                            for y_start, y_end in y_ranges:
                                if py2 <= y_start or py1 >= y_end:
                                    new_ranges.append([y_start, y_end])
                                else:
                                    if py1 > y_start:
                                        new_ranges.append([y_start, py1])
                                    if py2 < y_end:
                                        new_ranges.append([py2, y_end])
                            if new_ranges != y_ranges:
                                was_cut = True
                            y_ranges = new_ranges

                    for y_start, y_end in y_ranges:
                        if was_cut:
                            y_start2 = y_start + 2
                            y_end2 = y_end - 0
                            if y_end2 > y_start2:
                                final_boxes.append({
                                    'label': 'text',
                                    'coordinate': [tx1, y_start2, tx2, y_end2],
                                    'score': box['score'],
                                    'cls_id': box['cls_id']
                                })
                        else:
                            final_boxes.append({
                                'label': 'text',
                                'coordinate': [tx1, y_start, tx2, y_end],
                                'score': box['score'],
                                'cls_id': box['cls_id']
                            })
                else:
                    final_boxes.append(box)

            batch_final_results.append(final_boxes)

        # 返回包含 N 张图结果的大列表
        return batch_final_results


# ==========================================
# 供 app.py 调用的接口封装
# ==========================================
def create_layout_engine(model_path: str, gpu_ids: list = None, num_sessions_per_gpu: int = 1,
                         model_name: str = "PP-DocLayoutV3"):
    """供外部 app.py 一键创建引擎使用"""
    return LayoutEngine(
        model_dir=model_path,
        model_name=model_name,
        gpu_ids=gpu_ids or [],
        num_sessions_per_gpu=num_sessions_per_gpu
    )


# =========================
# 本地测试代码
# =========================
if __name__ == "__main__":
    test_img_path = "page_21.png"

    if os.path.exists(test_img_path):
        img_np = cv2.imread(test_img_path)

        engine = create_layout_engine(
            model_path=r"./layout2",
            gpu_ids=[]
        )

        try:
            # 测试单张或批量输入 (内存数据直接送入)
            results = engine.run([img_np, img_np])  # 模拟送入2张图的 Batch
            print(f"成功获取 {len(results)} 张图的结果")
            print(f"第一张图识别到的版面元素数量: {len(results[0])}")
            print(results)
        except Exception as e:
            print(f"处理报错: {e}")
        finally:
            engine.close()
    else:
        print(f"找不到测试图片: {test_img_path}")