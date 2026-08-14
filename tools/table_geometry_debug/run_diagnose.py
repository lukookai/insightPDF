#!/usr/bin/env python3
"""PDF 表格几何诊断工具（独立，不侵入主 Pipeline）。

用法:
    python tools/table_geometry_debug/run_diagnose.py \
        --pdf test.pdf --page 3 --out output/

对指定页执行两路解析并产出:
    output/page_003_original.png
    output/page_003_babeldoc_overlay.png
    output/page_003_geometry_overlay.png
    output/page_003_babeldoc.json
    output/page_003_geometry.json
    output/page_003_compare.json
以及终端对比报告。

路径 A (BabelDOC 原生解析) 只运行翻译前的阶段，不发任何翻译请求。
路径 B (PyMuPDF 几何) 复用项目已有的 pymupdf，无需新增依赖。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

# Make the project's ``实现源码`` importable (babeldoc / pdf_translator) and let
# the sibling modules import each other when this file is run directly.
HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
IMPL = REPO / "实现源码"
for _p in (str(REPO), str(IMPL), str(HERE)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

import pymupdf  # noqa: E402

from path_babeldoc import extract_babeldoc  # noqa: E402
from path_geometry import extract_geometry  # noqa: E402
from visualize import make_overlays  # noqa: E402
from compare import build_compare, print_report  # noqa: E402


def _default_cache_home() -> Path:
    cand = Path.home() / ".cache" / "babeldoc"
    if cand.is_dir():
        return cand
    return Path("C:/Users/74496/.cache/babeldoc")


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="PDF 表格几何诊断工具")
    ap.add_argument("--pdf", required=True, type=Path, help="输入英文 PDF")
    ap.add_argument("--page", required=True, type=int, help="页码（从 1 开始）")
    ap.add_argument("--out", required=True, type=Path, help="输出目录")
    ap.add_argument(
        "--babeldoc-cache-home",
        type=Path,
        default=_default_cache_home(),
        help="BabelDOC 模型/字体缓存根目录（默认 ~/.cache/babeldoc）",
    )
    ap.add_argument(
        "--doclayout-model",
        type=Path,
        default=None,
        help="DocLayout ONNX 模型路径（默认自动加载）",
    )
    args = ap.parse_args(argv)

    if not args.pdf.is_file():
        print(f"[错误] PDF 不存在: {args.pdf}", file=sys.stderr)
        return 2
    args.out.mkdir(parents=True, exist_ok=True)
    nn = f"{args.page:03d}"

    # page rotation (for Q7 in the report)
    doc = pymupdf.open(args.pdf)
    if not (1 <= args.page <= doc.page_count):
        print(f"[错误] 页码超出范围 1..{doc.page_count}", file=sys.stderr)
        doc.close()
        return 2
    page_rotation = int(doc[args.page - 1].rotation)
    doc.close()

    print(f"[1/4] 路径 B：PyMuPDF 几何解析 {args.pdf.name} p{args.page} ...")
    geometry_json = extract_geometry(args.pdf, args.page)
    (args.out / f"page_{nn}_geometry.json").write_text(
        json.dumps(geometry_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[2/4] 路径 A：BabelDOC 原生解析（不翻译）...")
    try:
        babeldoc_json = extract_babeldoc(
            args.pdf,
            args.page,
            babeldoc_cache_home=args.babeldoc_cache_home,
            doclayout_model=args.doclayout_model,
            work_dir=args.out / "_babeldoc_work",
        )
    except Exception as exc:  # keep the tool usable even if BabelDOC path fails
        import traceback

        traceback.print_exc()
        print(f"[警告] BabelDOC 解析失败：{exc}", file=sys.stderr)
        babeldoc_json = {
            "page": geometry_json["page"],
            "texts": [],
            "horizontal_lines": [],
            "vertical_lines": [],
            "rectangles": [],
            "detected_tables": [],
            "_meta": {"parser": "babeldoc-native", "error": str(exc)},
        }
    (args.out / f"page_{nn}_babeldoc.json").write_text(
        json.dumps(babeldoc_json, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print(f"[3/4] 生成叠加可视化 ...")
    overlays = make_overlays(
        args.pdf, args.page, babeldoc_json, geometry_json, args.out
    )

    print(f"[4/4] 对比分析 ...")
    compare = build_compare(babeldoc_json, geometry_json, page_rotation=page_rotation)
    (args.out / f"page_{nn}_compare.json").write_text(
        json.dumps(compare, ensure_ascii=False, indent=2), encoding="utf-8"
    )

    print()
    print_report(compare)
    print()
    print("输出文件：")
    for k, v in overlays.items():
        if isinstance(v, str):
            print(f"  {k}: {v}")
    print(f"  babeldoc_json: {args.out / f'page_{nn}_babeldoc.json'}")
    print(f"  geometry_json: {args.out / f'page_{nn}_geometry.json'}")
    print(f"  compare_json:  {args.out / f'page_{nn}_compare.json'}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
