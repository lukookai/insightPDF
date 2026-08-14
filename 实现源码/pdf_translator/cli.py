from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

from pdf_translator.layout.pdf_geometry import (
    extract_pdf_geometry,
    merge_unlimited_ocr,
    save_layout,
)
from pdf_translator.layout.align_segments import attach_translations
from pdf_translator.ocr.unlimited_ocr import UnlimitedOCRBackend
from pdf_translator.ocr.unlimited_transformers import UnlimitedOCRTransformersRunner
from pdf_translator.layout.preview import load_and_build
from pdf_translator.layout.aligned_html import load_and_build as build_aligned_html
from pdf_translator.layout.segment_regions import load_and_build as build_region_segments
from pdf_translator.translation import DEFAULT_BASE_URL, get_translation_api_key


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(description="PDF 翻译与对齐流水线")
    commands = root.add_subparsers(dest="command", required=True)

    geometry = commands.add_parser("extract-geometry", help="提取 PDF 精确坐标")
    geometry.add_argument("pdf", type=Path)
    geometry.add_argument("-o", "--output", type=Path, required=True)

    ocr = commands.add_parser("run-unlimited-ocr", help="运行 baidu/Unlimited-OCR")
    ocr.add_argument("pdf", type=Path)
    ocr.add_argument("--repo", type=Path, default=Path("third_party/Unlimited-OCR"))
    ocr.add_argument("--output-dir", type=Path, required=True)
    ocr.add_argument(
        "--backend", choices=("transformers", "upstream"), default="transformers"
    )
    ocr.add_argument("--python", default="python3.12")
    ocr.add_argument("--model-dir", default="baidu/Unlimited-OCR")
    ocr.add_argument(
        "--runtime", type=Path, default=Path("third_party/Unlimited-OCR/runtime")
    )
    ocr.add_argument("--gpu", default="0")
    ocr.add_argument("--concurrency", type=int, default=1)
    ocr.add_argument("--image-mode", choices=("gundam", "base"), default="gundam")
    ocr.add_argument("--dpi", type=int, default=300)
    ocr.add_argument("--first-page", type=int, default=1)
    ocr.add_argument("--last-page", type=int)

    merge = commands.add_parser("merge-ocr", help="将 OCR Markdown 合入坐标 JSON")
    merge.add_argument("pdf", type=Path)
    merge.add_argument("--geometry", type=Path, required=True)
    merge.add_argument("--ocr-dir", type=Path, required=True)
    merge.add_argument("-o", "--output", type=Path, required=True)

    align = commands.add_parser("align-translations", help="将结构化译文匹配到 PDF 坐标")
    align.add_argument("--geometry", type=Path, required=True)
    align.add_argument("--translations", type=Path, required=True)
    align.add_argument("-o", "--output", type=Path, required=True)

    segments = commands.add_parser("prepare-segments", help="按 OCR 版面区域生成翻译段落")
    segments.add_argument("--document", type=Path, required=True)
    segments.add_argument("-o", "--output", type=Path, required=True)

    preview = commands.add_parser("preview", help="生成坐标与 OCR 框的 HTML 预览")
    preview.add_argument("--document", type=Path, required=True)
    preview.add_argument(
        "--background-pattern",
        required=True,
        help="页面背景路径模板，例如 ../work/page-{page:02d}.png",
    )
    preview.add_argument("-o", "--output", type=Path, required=True)

    html_command = commands.add_parser("build-html", help="从对齐 JSON 生成中文 HTML")
    html_command.add_argument("--document", type=Path, required=True)
    html_command.add_argument("-o", "--output", type=Path, required=True)
    html_command.add_argument("--fallback-background-pattern")

    pipeline = commands.add_parser("run-pipeline", help="运行单个 PDF 的完整可恢复流水线")
    pipeline.add_argument("pdf", type=Path)
    pipeline.add_argument("--work-dir", type=Path, required=True)
    pipeline.add_argument("--model-dir", default="third_party/Unlimited-OCR/model")
    pipeline.add_argument(
        "--runtime", type=Path, default=Path("third_party/Unlimited-OCR/runtime")
    )
    pipeline.add_argument("--gpu", default="0")
    pipeline.add_argument("--base-url", default=DEFAULT_BASE_URL)

    batch = commands.add_parser("run-batch", help="批量运行目录内所有 PDF")
    batch.add_argument("directory", type=Path)
    batch.add_argument("--output-root", type=Path, required=True)
    batch.add_argument("--pattern", default="*.pdf")
    batch.add_argument("--model-dir", default="third_party/Unlimited-OCR/model")
    batch.add_argument(
        "--runtime", type=Path, default=Path("third_party/Unlimited-OCR/runtime")
    )
    batch.add_argument("--gpu", default="0")
    batch.add_argument("--base-url", default=DEFAULT_BASE_URL)

    render = commands.add_parser(
        "render-cached", help="只用已有 OCR 和译文重新对齐并生成 HTML/PDF"
    )
    render.add_argument("work_dir", type=Path)

    render_batch = commands.add_parser(
        "render-cached-batch", help="批量执行纯排版，不启动 OCR 或翻译"
    )
    render_batch.add_argument("directory", type=Path)
    render_batch.add_argument("--pattern", default="*_work")

    offline = commands.add_parser(
        "run-native-rules",
        help="用 PDF 原生规则、HTML 和自有翻译 API 生成中文 PDF",
    )
    offline.add_argument("pdf", type=Path)
    offline.add_argument("--output-dir", type=Path, required=True)
    offline.add_argument(
        "--layout-mode", choices=("native", "doclayout"), required=True
    )
    offline.add_argument(
        "--translation-cache",
        type=Path,
        default=Path("models/lawai_translation_cache.json"),
    )
    offline.add_argument("--base-url", default=DEFAULT_BASE_URL)
    offline.add_argument("--doclayout-model", type=Path)
    offline.add_argument("--page-limit", type=int, default=2)

    babeldoc_html = commands.add_parser(
        "run-babeldoc-html",
        help="复用 double6/BabelDOC 全部前置规则，仅用 HTML/CSS 完成最终排版",
    )
    babeldoc_html.add_argument("pdf", type=Path)
    babeldoc_html.add_argument("--output-dir", type=Path, required=True)
    babeldoc_html.add_argument("--base-url", default=DEFAULT_BASE_URL)
    babeldoc_html.add_argument(
        "--skill-root",
        type=Path,
        default=Path("/home/qiuhongyun/.codex/skills/double6-pdf-translation"),
    )
    babeldoc_html.add_argument(
        "--page-limit",
        type=int,
        help="只处理前 N 页；不传则处理全文",
    )
    babeldoc_html.add_argument(
        "--doclayout-model",
        type=Path,
        help="本地 DocLayout ONNX；不传时优先复用 double6 缓存",
    )
    babeldoc_html.add_argument(
        "--babeldoc-cache-home",
        type=Path,
        default=Path(
            "/home/qiuhongyun/.cache/double6-pdf-translation/pdf2zh-home"
        ),
        help="BabelDOC 模型和字体缓存根目录",
    )
    babeldoc_html.add_argument(
        "--concurrency",
        type=int,
        default=8,
        help="翻译并发数；默认 8",
    )

    cached_babeldoc = commands.add_parser(
        "render-babeldoc-html",
        help="复用已完成的 BabelDOC 翻译 IL，只运行 HTML 排版和 PDF 生成",
    )
    cached_babeldoc.add_argument("--source-pdf", type=Path, required=True)
    cached_babeldoc.add_argument("--source-il", type=Path, required=True)
    cached_babeldoc.add_argument("--translated-il", type=Path, required=True)
    cached_babeldoc.add_argument("--output-dir", type=Path, required=True)
    cached_babeldoc.add_argument("--output-stem", required=True)
    cached_babeldoc.add_argument("--base-url", default=DEFAULT_BASE_URL)
    cached_babeldoc.add_argument(
        "--repair-placeholder-order",
        action="store_true",
        help="用当前翻译 API 补译公式占位符移位的项目符号段落",
    )

    production_bridge = commands.add_parser(
        "run-production-bridge",
        help="运行真实生产 pdf2json 规则、结构桥接和坐标回填基线",
    )
    production_bridge.add_argument("pdf", type=Path)
    production_bridge.add_argument("--output-dir", type=Path, required=True)
    production_bridge.add_argument("--base-url", default=DEFAULT_BASE_URL)
    production_bridge.add_argument(
        "--production-repo",
        type=Path,
        default=Path("/data/qhy/cqnu/pdf2json_repo"),
    )
    production_bridge.add_argument("--concurrency", type=int, default=4)

    hybrid = commands.add_parser(
        "run-production-doclayout-html",
        help="生产规则解析、DocLayout 纠错、分块翻译和 HTML 自适应排版",
    )
    hybrid.add_argument("pdf", type=Path)
    hybrid.add_argument("--output-dir", type=Path, required=True)
    hybrid.add_argument("--base-url", default=DEFAULT_BASE_URL)
    hybrid.add_argument(
        "--production-repo",
        type=Path,
        default=Path("/data/qhy/cqnu/pdf2json_repo"),
    )
    hybrid.add_argument(
        "--doclayout-model",
        type=Path,
        default=Path(
            "/home/qiuhongyun/.cache/double6-pdf-translation/pdf2zh-home/"
            ".cache/babeldoc/models/doclayout_yolo_docstructbench_imgsz1024.onnx"
        ),
    )
    hybrid.add_argument("--page-limit", type=int)
    hybrid.add_argument("--split-limit", type=int, default=620)
    hybrid.add_argument("--concurrency", type=int, default=4)
    return root


def main() -> int:
    args = parser().parse_args()
    if args.command == "extract-geometry":
        save_layout(extract_pdf_geometry(args.pdf), args.output)
        print(f"坐标数据已保存：{args.output}")
        return 0
    if args.command == "run-unlimited-ocr":
        if args.backend == "transformers":
            backend = UnlimitedOCRTransformersRunner(
                model_dir=args.model_dir,
                runtime_dir=args.runtime,
                dpi=args.dpi,
                first_page=args.first_page,
                last_page=args.last_page,
                gpu=args.gpu,
            )
        else:
            backend = UnlimitedOCRBackend(
                args.repo,
                python=args.python,
                model_dir=args.model_dir,
                gpu=args.gpu,
                concurrency=args.concurrency,
                image_mode=args.image_mode,
            )
        pages = backend.run_pdf(args.pdf, args.output_dir)
        print(f"Unlimited-OCR 已解析 {len(pages)} 页：{args.output_dir}")
        return 0
    if args.command == "merge-ocr":
        geometry = json.loads(args.geometry.read_text(encoding="utf-8"))
        merged = merge_unlimited_ocr(geometry, args.pdf, args.ocr_dir)
        save_layout(merged, args.output)
        print(f"OCR 与坐标已合并：{args.output}")
        return 0
    if args.command == "align-translations":
        geometry = json.loads(args.geometry.read_text(encoding="utf-8"))
        translations = json.loads(args.translations.read_text(encoding="utf-8"))
        aligned = attach_translations(geometry, translations)
        save_layout(aligned, args.output)
        print(f"译文坐标已保存：{args.output}")
        return 0
    if args.command == "prepare-segments":
        build_region_segments(args.document, args.output)
        print(f"版面翻译段落已保存：{args.output}")
        return 0
    if args.command == "preview":
        load_and_build(args.document, args.background_pattern, args.output)
        print(f"对齐预览已保存：{args.output}")
        return 0
    if args.command == "build-html":
        build_aligned_html(
            args.document,
            args.output,
            args.fallback_background_pattern,
        )
        print(f"中文 HTML 已保存：{args.output}")
        return 0
    if args.command == "run-pipeline":
        from pdf_translator.pipeline import run_pipeline

        report = run_pipeline(
            args.pdf,
            args.work_dir,
            model_dir=args.model_dir,
            runtime_dir=args.runtime,
            gpu=args.gpu,
            token=get_translation_api_key(args.base_url),
            base_url=args.base_url,
        )
        print(f"完整流水线已完成：{report['output_pdf']}")
        return 0
    if args.command == "run-batch":
        from pdf_translator.pipeline import run_pipeline

        pdfs = sorted(args.directory.glob(args.pattern))
        if not pdfs:
            raise SystemExit(f"目录中没有匹配的 PDF：{args.directory}/{args.pattern}")
        token = get_translation_api_key(args.base_url)
        for index, pdf in enumerate(pdfs, 1):
            print(f"\n[{index}/{len(pdfs)}] 开始处理：{pdf.name}", flush=True)
            report = run_pipeline(
                pdf,
                args.output_root / pdf.stem,
                model_dir=args.model_dir,
                runtime_dir=args.runtime,
                gpu=args.gpu,
                token=token,
                base_url=args.base_url,
            )
            print(f"[{index}/{len(pdfs)}] 已完成：{report['output_pdf']}", flush=True)
        return 0
    if args.command == "render-cached":
        from pdf_translator.pipeline import render_cached_layout

        report = render_cached_layout(args.work_dir)
        print(f"纯排版已完成：{report['output_pdf']}")
        if not report["automatic_quality_pass"]:
            print(f"质量检查未通过：{args.work_dir / 'quality.json'}")
            return 1
        return 0
    if args.command == "render-cached-batch":
        from pdf_translator.pipeline import render_cached_layout

        work_dirs = sorted(
            path
            for path in args.directory.glob(args.pattern)
            if path.is_dir()
        )
        if not work_dirs:
            raise SystemExit(
                f"目录中没有匹配的工作目录：{args.directory}/{args.pattern}"
            )
        failures = []
        for index, work_dir in enumerate(work_dirs, 1):
            print(f"[{index}/{len(work_dirs)}] 纯排版：{work_dir.name}", flush=True)
            try:
                report = render_cached_layout(work_dir)
            except Exception as exc:
                failures.append((work_dir.name, str(exc)))
                print(f"[{index}/{len(work_dirs)}] 失败：{exc}", flush=True)
                continue
            if not report["automatic_quality_pass"]:
                failures.append((work_dir.name, "quality.json 自动质量门未通过"))
                print(
                    f"[{index}/{len(work_dirs)}] 已输出，但质量检查未通过",
                    flush=True,
                )
                continue
            print(
                f"[{index}/{len(work_dirs)}] 完成：{report['output_pdf']}",
                flush=True,
            )
        if failures:
            for name, reason in failures:
                print(f"失败 {name}: {reason}")
            return 1
        return 0
    if args.command == "run-native-rules":
        from pdf_translator.offline_pipeline import run_native_rule_pipeline

        manifest = run_native_rule_pipeline(
            args.pdf,
            args.output_dir,
            layout_mode=args.layout_mode,
            translation_cache=args.translation_cache,
            token=get_translation_api_key(args.base_url),
            base_url=args.base_url,
            doclayout_model=args.doclayout_model,
            page_limit=args.page_limit,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "ok" else 1
    if args.command == "run-babeldoc-html":
        from pdf_translator.babeldoc_html import run_babeldoc_html_pipeline

        output_dir = args.output_dir.resolve()
        fyresults = Path(
            os.environ.get("FYRESULTS_DIR", "E:/Code/fanyi/runs/fyresults")
        ).resolve()
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
        manifest = run_babeldoc_html_pipeline(
            args.pdf,
            output_dir,
            token=get_translation_api_key(args.base_url),
            base_url=args.base_url,
            skill_root=args.skill_root,
            doclayout_model=args.doclayout_model,
            babeldoc_cache_home=args.babeldoc_cache_home,
            concurrency=args.concurrency,
            page_limit=args.page_limit,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "ok" else 1
    if args.command == "render-babeldoc-html":
        from pdf_translator.babeldoc_html import render_cached_babeldoc_il

        output_dir = args.output_dir.resolve()
        fyresults = Path(
            os.environ.get("FYRESULTS_DIR", "E:/Code/fanyi/runs/fyresults")
        ).resolve()
        if not output_dir.exists():
            output_dir.mkdir(parents=True, exist_ok=True)
        manifest = render_cached_babeldoc_il(
            args.source_pdf,
            args.source_il,
            args.translated_il,
            output_dir,
            output_stem=args.output_stem,
            repair_placeholder_order=args.repair_placeholder_order,
            token=get_translation_api_key(args.base_url),
            base_url=args.base_url,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "ok" else 1
    if args.command == "run-production-bridge":
        from pdf_translator.production_hybrid import run_production_bridge_pipeline

        manifest = run_production_bridge_pipeline(
            args.pdf,
            args.output_dir,
            token=get_translation_api_key(args.base_url),
            base_url=args.base_url,
            production_repo=args.production_repo,
            concurrency=args.concurrency,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "ok" else 1
    if args.command == "run-production-doclayout-html":
        from pdf_translator.production_hybrid import (
            run_production_doclayout_html_pipeline,
        )

        manifest = run_production_doclayout_html_pipeline(
            args.pdf,
            args.output_dir,
            token=get_translation_api_key(args.base_url),
            base_url=args.base_url,
            production_repo=args.production_repo,
            doclayout_model=args.doclayout_model,
            page_limit=args.page_limit,
            split_limit=args.split_limit,
            concurrency=args.concurrency,
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2))
        return 0 if manifest["status"] == "ok" else 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
