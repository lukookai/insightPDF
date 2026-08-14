from __future__ import annotations

import subprocess
import sys
import os
from pathlib import Path

from .unlimited_ocr import OCRPage, UnlimitedOCRBackend, UnlimitedOCRError


class UnlimitedOCRTransformersRunner:
    """Run baidu/Unlimited-OCR directly with Hugging Face Transformers."""

    def __init__(
        self,
        *,
        model_dir: str = "baidu/Unlimited-OCR",
        runtime_dir: Path | None = None,
        dpi: int = 300,
        first_page: int = 1,
        last_page: int | None = None,
        gpu: str = "0",
    ) -> None:
        self.model_dir = model_dir
        self.runtime_dir = runtime_dir.resolve() if runtime_dir else None
        self.dpi = dpi
        self.first_page = first_page
        self.last_page = last_page
        self.gpu = gpu

    def _activate_runtime(self) -> None:
        if self.runtime_dir:
            if not self.runtime_dir.is_dir():
                raise UnlimitedOCRError(f"OCR runtime 不存在：{self.runtime_dir}")
            sys.path.insert(0, str(self.runtime_dir))

    def _render_pages(self, pdf: Path, page_dir: Path) -> list[tuple[int, Path]]:
        page_dir.mkdir(parents=True, exist_ok=True)
        prefix = page_dir / "page"
        command = [
            "pdftoppm",
            "-png",
            "-r",
            str(self.dpi),
            "-f",
            str(self.first_page),
        ]
        if self.last_page is not None:
            command.extend(["-l", str(self.last_page)])
        command.extend([str(pdf), str(prefix)])
        subprocess.run(command, check=True)
        rendered = sorted(page_dir.glob("page-*.png"))
        return [
            (self.first_page + offset, image)
            for offset, image in enumerate(rendered)
        ]

    def run_pdf(self, pdf: Path, output_dir: Path) -> list[OCRPage]:
        # Configure the physical GPU before importing torch. Inside the process,
        # the selected device is then exposed as cuda:0.
        os.environ["CUDA_VISIBLE_DEVICES"] = self.gpu
        self._activate_runtime()
        try:
            import torch
            from transformers import AutoModel, AutoTokenizer
        except ImportError as exc:
            raise UnlimitedOCRError(
                "缺少 Unlimited-OCR Transformers 运行依赖；"
                "请按 docs/UNLIMITED_OCR_INTEGRATION.md 安装 runtime"
            ) from exc

        if not torch.cuda.is_available():
            raise UnlimitedOCRError("Unlimited-OCR 需要可用的 NVIDIA CUDA GPU")

        pdf = pdf.resolve()
        output_dir = output_dir.resolve()
        output_dir.mkdir(parents=True, exist_ok=True)
        page_dir = output_dir / "pages"
        rendered = self._render_pages(pdf, page_dir)
        if not rendered:
            raise UnlimitedOCRError("PDF 没有渲染出任何页面")

        print(f"加载 Unlimited-OCR 模型：{self.model_dir}", flush=True)
        tokenizer = AutoTokenizer.from_pretrained(
            self.model_dir,
            trust_remote_code=True,
        )
        model = AutoModel.from_pretrained(
            self.model_dir,
            trust_remote_code=True,
            use_safetensors=True,
            dtype=torch.bfloat16,
        )
        model = model.eval().cuda()

        for index, (page_number, image_path) in enumerate(rendered, 1):
            result_file = output_dir / f"{pdf.stem}_page_{page_number:04d}.md"
            if result_file.is_file() and result_file.stat().st_size > 20:
                print(
                    f"[{index}/{len(rendered)}] 第 {page_number} 页使用缓存",
                    flush=True,
                )
                continue
            print(f"[{index}/{len(rendered)}] 解析第 {page_number} 页", flush=True)
            raw_output = model.infer(
                tokenizer,
                prompt="<image>document parsing.",
                image_file=str(image_path),
                output_path=str(output_dir / f"page_{page_number:04d}_artifacts"),
                base_size=1024,
                image_size=640,
                crop_mode=True,
                max_length=32768,
                no_repeat_ngram_size=35,
                ngram_window=128,
                save_results=False,
                eval_mode=True,
                temperature=0.0,
            )
            if not isinstance(raw_output, str) or not raw_output.strip():
                raise UnlimitedOCRError(f"第 {page_number} 页没有返回 OCR 内容")
            result_file.write_text(raw_output.strip(), encoding="utf-8")

        return UnlimitedOCRBackend.load_results(pdf, output_dir)
