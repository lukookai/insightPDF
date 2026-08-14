from __future__ import annotations

import ast
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path


class UnlimitedOCRError(RuntimeError):
    pass


@dataclass(frozen=True)
class OCRPage:
    page: int
    raw_output: str
    markdown: str
    blocks: list[dict]
    detections: list[dict]
    source_file: str


REF_DET_PATTERN = re.compile(
    r"<\|ref\|>(.*?)<\|/ref\|>\s*<\|det\|>(.*?)<\|/det\|>",
    re.DOTALL,
)
DET_ONLY_PATTERN = re.compile(
    r"<\|det\|>\s*([A-Za-z_][\w-]*)\s*(\[[^>]+\])\s*<\|/det\|>",
    re.DOTALL,
)


def parse_detections(raw_output: str) -> list[dict]:
    """Parse the model's normalized 0..999 detection coordinates."""
    detections: list[dict] = []
    consumed: set[str] = set()
    for match in REF_DET_PATTERN.finditer(raw_output):
        label = match.group(1).strip()
        encoded = match.group(2).strip()
        try:
            boxes = ast.literal_eval(encoded)
            if boxes and isinstance(boxes[0], (int, float)):
                boxes = [boxes]
            valid = [
                [float(value) for value in box]
                for box in boxes
                if isinstance(box, (list, tuple)) and len(box) == 4
            ]
        except (ValueError, SyntaxError, TypeError):
            valid = []
        if valid:
            detections.append(
                {
                    "label": label,
                    "normalized_bboxes": valid,
                    "raw": match.group(0),
                    "offset": match.start(),
                    "end_offset": match.end(),
                }
            )
            consumed.add(match.group(0))
    for match in DET_ONLY_PATTERN.finditer(raw_output):
        if match.group(0) in consumed:
            continue
        try:
            boxes = ast.literal_eval(match.group(2))
            if boxes and isinstance(boxes[0], (int, float)):
                boxes = [boxes]
            valid = [
                [float(value) for value in box]
                for box in boxes
                if isinstance(box, (list, tuple)) and len(box) == 4
            ]
        except (ValueError, SyntaxError, TypeError):
            valid = []
        if valid:
            detections.append(
                {
                    "label": match.group(1).strip(),
                    "normalized_bboxes": valid,
                    "raw": match.group(0),
                    "offset": match.start(),
                    "end_offset": match.end(),
                }
            )
    detections.sort(key=lambda item: item["offset"])
    for index, detection in enumerate(detections):
        next_offset = (
            detections[index + 1]["offset"]
            if index + 1 < len(detections)
            else len(raw_output)
        )
        following = raw_output[detection["end_offset"] : next_offset]
        detection["text"] = clean_model_output(following).strip()
    return detections


def clean_model_output(raw_output: str) -> str:
    """Remove detection tokens while keeping OCR Markdown content."""
    cleaned = REF_DET_PATTERN.sub("", raw_output)
    cleaned = DET_ONLY_PATTERN.sub("", cleaned)
    cleaned = cleaned.replace("<｜end▁of▁sentence｜>", "")
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def markdown_blocks(markdown: str) -> list[dict]:
    """Convert Unlimited-OCR Markdown into lightweight semantic blocks.

    Unlimited-OCR restores content and reading order but does not expose stable
    PDF bounding boxes. Coordinates are merged later from the native geometry
    extractor.
    """
    blocks: list[dict] = []
    buffer: list[str] = []

    def flush() -> None:
        if not buffer:
            return
        text = "\n".join(buffer).strip()
        if text:
            blocks.append({"type": "paragraph", "text": text})
        buffer.clear()

    for raw_line in markdown.splitlines():
        line = raw_line.rstrip()
        stripped = line.strip()
        if not stripped:
            flush()
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            flush()
            blocks.append(
                {
                    "type": "heading",
                    "level": len(heading.group(1)),
                    "text": heading.group(2).strip(),
                }
            )
            continue
        if re.match(r"^(?:[-*+] |\d+[.)]\s+)", stripped):
            flush()
            blocks.append({"type": "list_item", "text": stripped})
            continue
        if stripped.startswith("|") and stripped.endswith("|"):
            flush()
            blocks.append({"type": "table_row", "text": stripped})
            continue
        if stripped.startswith("```"):
            flush()
            blocks.append({"type": "fence", "text": stripped})
            continue
        buffer.append(stripped)
    flush()
    return blocks


class UnlimitedOCRBackend:
    """Adapter for the upstream baidu/Unlimited-OCR ``infer.py`` runner."""

    def __init__(
        self,
        repo_dir: Path,
        *,
        python: str = sys.executable,
        model_dir: str = "baidu/Unlimited-OCR",
        gpu: str = "0",
        concurrency: int = 1,
        image_mode: str = "gundam",
    ) -> None:
        self.repo_dir = repo_dir.resolve()
        self.python = python
        self.model_dir = model_dir
        self.gpu = gpu
        self.concurrency = concurrency
        self.image_mode = image_mode

    @property
    def infer_script(self) -> Path:
        return self.repo_dir / "infer.py"

    def validate(self) -> None:
        if not self.infer_script.is_file():
            raise UnlimitedOCRError(
                f"未找到 Unlimited-OCR infer.py：{self.infer_script}。"
                "请先按 docs/UNLIMITED_OCR_INTEGRATION.md 安装官方仓库。"
            )
        if self.image_mode not in {"gundam", "base"}:
            raise UnlimitedOCRError("image_mode 必须是 gundam 或 base")
        if self.concurrency < 1:
            raise UnlimitedOCRError("concurrency 必须大于 0")

    def run_pdf(self, pdf: Path, output_dir: Path) -> list[OCRPage]:
        self.validate()
        pdf = pdf.resolve()
        if not pdf.is_file():
            raise UnlimitedOCRError(f"PDF 不存在：{pdf}")
        output_dir.mkdir(parents=True, exist_ok=True)
        log_path = output_dir / "sglang_server.log"
        command = [
            self.python,
            str(self.infer_script),
            "--pdf",
            str(pdf),
            "--output_dir",
            str(output_dir.resolve()),
            "--concurrency",
            str(self.concurrency),
            "--gpu",
            self.gpu,
            "--model_dir",
            self.model_dir,
            "--image_mode",
            self.image_mode,
            "--server_log",
            str(log_path.resolve()),
        ]
        completed = subprocess.run(command, cwd=self.repo_dir)
        if completed.returncode != 0:
            raise UnlimitedOCRError(
                f"Unlimited-OCR 运行失败，退出码 {completed.returncode}；"
                f"日志：{log_path}"
            )
        pages = self.load_results(pdf, output_dir)
        if not pages:
            raise UnlimitedOCRError(f"未在 {output_dir} 找到按页 Markdown 输出")
        return pages

    @staticmethod
    def load_results(pdf: Path, output_dir: Path) -> list[OCRPage]:
        prefix = pdf.stem
        pattern = re.compile(rf"^{re.escape(prefix)}_page_(\d{{4}})\.md$")
        pages: list[OCRPage] = []
        for result_file in sorted(output_dir.glob(f"{prefix}_page_*.md")):
            match = pattern.match(result_file.name)
            if not match:
                continue
            raw_output = result_file.read_text(encoding="utf-8", errors="replace")
            markdown = clean_model_output(raw_output)
            pages.append(
                OCRPage(
                    page=int(match.group(1)),
                    raw_output=raw_output,
                    markdown=markdown,
                    blocks=markdown_blocks(markdown),
                    detections=parse_detections(raw_output),
                    source_file=str(result_file),
                )
            )
        return pages
