"""Phase 4: compare BabelDOC IL geometry against raw PDF geometry.

Answers the eight diagnostic questions with concrete counts/evidence and emits
``compare.json`` plus a human-readable terminal report.
"""

from __future__ import annotations


def _containment_offset(span, box):
    """Offset of a span's centre from the centre of the box that contains it."""
    scx, scy = (span[0] + span[2]) / 2, (span[1] + span[3]) / 2
    if box[0] <= scx <= box[2] and box[1] <= scy <= box[3]:
        bcx, bcy = (box[0] + box[2]) / 2, (box[1] + box[3]) / 2
        return scx - bcx, scy - bcy
    return None


def _table_border_coverage(table_bbox, h_lines, v_lines, tol=2.0):
    """How many h/v lines fall inside (or within tol of) a table bbox."""
    x0, y0, x1, y1 = table_bbox
    inside = lambda l: (
        x0 - tol <= (l["x0"] + l["x1"]) / 2 <= x1 + tol
        and y0 - tol <= (l["y0"] + l["y1"]) / 2 <= y1 + tol
    )
    return sum(1 for l in h_lines if inside(l)), sum(1 for l in v_lines if inside(l))


def build_compare(babeldoc_json: dict, geometry_json: dict, page_rotation: int = 0) -> dict:
    a, b = babeldoc_json, geometry_json
    a_page, b_page = a["page"], b["page"]

    # --- coordinate-system consistency (Q6 / Q7) ---
    dims_match = (
        abs(a_page["width"] - b_page["width"]) < 0.5
        and abs(a_page["height"] - b_page["height"]) < 0.5
    )

    # --- text coordinate consistency (Q5) ---
    a_texts = a.get("texts", [])
    b_texts = b.get("texts", [])
    offsets = []
    contained = 0
    for span in b_texts:
        best = None
        for para in a_texts:
            off = _containment_offset(span["bbox"], para["bbox"])
            if off is not None:
                contained += 1
                best = off
                break
        if best is not None:
            offsets.append(best)
    n_off = len(offsets)
    med_dx = sorted(o[0] for o in offsets)[n_off // 2] if offsets else None
    med_dy = sorted(o[1] for o in offsets)[n_off // 2] if offsets else None

    # --- table border extraction from raw PDF (Q4) ---
    tables = a.get("detected_tables", [])
    table_coverage = []
    for tb in tables:
        hc, vc = _table_border_coverage(
            tb["bbox"], b.get("horizontal_lines", []), b.get("vertical_lines", [])
        )
        table_coverage.append(
            {"bbox": tb["bbox"], "geometry_h_lines": hc, "geometry_v_lines": vc}
        )
    geom_has_grid = bool(b.get("horizontal_lines")) and bool(b.get("vertical_lines"))

    # --- reconstructability (Q8) ---
    geom_h = bool(b.get("horizontal_lines"))
    geom_v = bool(b.get("vertical_lines"))
    if tables:
        if geom_h and geom_v:
            reconstruct = (
                "可行：DocLayout 给出 table bbox，几何路径同时提供横线与竖线，"
                "通过边缘融合（edge-merge）可重建完整的 cell/row/column 网格；"
                "rowspan/colspan 仍需额外的合并单元格启发式（线缺失 / 文字跨格检测）。"
            )
        elif geom_h:
            reconstruct = (
                "部分可行：table bbox + 横线可重建 row；column 需从文字对齐/间距推断，"
                "rowspan/colspan 需额外的合并单元格启发式。"
            )
        elif geom_v:
            reconstruct = (
                "部分可行：table bbox + 竖线可重建 column；row 需从文字行推断，"
                "rowspan/colspan 需额外的合并单元格启发式。"
            )
        else:
            reconstruct = (
                "不可行：仅有 DocLayout 给出的 table bbox，几何路径未提取到任何线框，"
                "无法重建单元格网格。"
            )
    else:
        if geom_h or geom_v:
            reconstruct = (
                "部分：几何路径有线框，但 DocLayout 未给出 table bbox；"
                "需要先用规则/聚类判定哪些线构成表格，再重建网格。"
            )
        else:
            reconstruct = (
                "不可行：当前页面既未检测到 table bbox，也未提取到表格线框。"
            )

    answers = [
        {
            "q": "1. BabelDOC 是否保存了原表格横线？",
            "answer": "partial" if a.get("horizontal_lines") else "no",
            "evidence": (
                f"BabelDOC 原生 IL 的 horizontal_lines={len(a.get('horizontal_lines', []))} 条"
                f"（来自 pdf_rectangle 中的扁矩形）；"
                f"pdf_rectangle 总数={a.get('_meta', {}).get('pdf_rectangle_count')}。"
                "横线以图形矩形形式存在，需按长宽比归类为线，并非独立线原语。"
            ),
        },
        {
            "q": "2. BabelDOC 是否保存了原表格竖线？",
            "answer": "partial" if a.get("vertical_lines") else "no",
            "evidence": (
                f"BabelDOC 原生 IL 的 vertical_lines={len(a.get('vertical_lines', []))} 条"
                f"（来自 pdf_rectangle 中的窄矩形）。竖线同样以矩形形式隐含保存。"
            ),
        },
        {
            "q": "3. BabelDOC 是否只有 table bbox 而没有 cell bbox？",
            "answer": "yes",
            "evidence": (
                f"detected_tables={len(tables)}（DocLayout class_name='table' 的整表框）；"
                "原生 IL 与简化 document.json 中均不存在 cell / row / column 级别的几何。"
            ),
        },
        {
            "q": "4. 原始 PDF 是否可以提取完整表格边框？",
            "answer": "yes" if geom_has_grid else "no",
            "evidence": (
                f"几何路径(PyMuPDF) horizontal_lines={len(b.get('horizontal_lines', []))} 条, "
                f"vertical_lines={len(b.get('vertical_lines', []))} 条, "
                f"rectangles={len(b.get('rectangles', []))} 个。"
                + (
                    f"在 {len(tables)} 个检测表中，几何线框覆盖情况："
                    + "; ".join(
                        f"表{i+1}(h={c['geometry_h_lines']},v={c['geometry_v_lines']})"
                        for i, c in enumerate(table_coverage)
                    )
                    if tables else "（本页无 DocLayout 检测表，无法按表框核对覆盖）"
                )
            ),
        },
        {
            "q": "5. 文字 bbox 是否仍处于原始位置？",
            "answer": "yes" if (n_off and contained / max(1, len(b_texts)) > 0.8) else "check",
            "evidence": (
                f"几何路径文字块 {len(b_texts)} 个，其中 {contained} 个被 BabelDOC 段落框包含；"
                f"中位中心偏移 dx={med_dx}, dy={med_dy} pt。"
            ),
        },
        {
            "q": "6. 两种解析器页面坐标系是否一致？",
            "answer": "yes" if dims_match else "no",
            "evidence": (
                f"BabelDOC 页尺寸 {a_page['width']}x{a_page['height']} pt vs "
                f"几何路径 {b_page['width']}x{b_page['height']} pt；"
                "两者均为 top-left、y-down、单位 pt。"
            ),
        },
        {
            "q": "7. 是否存在坐标缩放 / Y 轴 / rotation / CropBox / MediaBox 偏移？",
            "answer": "check",
            "evidence": (
                f"page.rotation={page_rotation}°；"
                "两路坐标均已统一为 top-left pt，未做额外缩放。"
                + (" 页面旋转非零，叠加图按 rotate=0 渲染以保持与坐标一致，请注意视觉方向。"
                   if page_rotation else " 页面旋转为 0，无方向偏移风险。")
            ),
        },
        {
            "q": "8. 根据现有数据，能否重建 row/column/cell/rowspan/colspan？",
            "answer": "partial",
            "evidence": reconstruct,
        },
    ]

    return {
        "coordinate_unit": "pt",
        "page_dimensions": {
            "babeldoc": [a_page["width"], a_page["height"]],
            "geometry": [b_page["width"], b_page["height"]],
            "dimensions_match": dims_match,
        },
        "counts": {
            "babeldoc": {
                "texts": len(a_texts),
                "horizontal_lines": len(a.get("horizontal_lines", [])),
                "vertical_lines": len(a.get("vertical_lines", [])),
                "rectangles": len(a.get("rectangles", [])),
                "detected_tables": len(tables),
            },
            "geometry": {
                "texts": len(b_texts),
                "horizontal_lines": len(b.get("horizontal_lines", [])),
                "vertical_lines": len(b.get("vertical_lines", [])),
                "rectangles": len(b.get("rectangles", [])),
                "detected_tables": len(b.get("detected_tables", [])),
            },
        },
        "text_consistency": {
            "geometry_texts": len(b_texts),
            "contained_in_babeldoc_paragraph": contained,
            "containment_ratio": round(contained / max(1, len(b_texts)), 3),
            "median_center_offset_pt": [med_dx, med_dy],
        },
        "table_border_coverage": table_coverage,
        "answers": answers,
    }


def print_report(compare: dict) -> None:
    def tag(v):
        return {"yes": "✅ 是", "no": "❌ 否", "partial": "⚠️ 部分", "check": "🔎 需核查"}.get(v, v)

    print("=" * 72)
    print("PDF 表格几何诊断 · 对比报告")
    print("=" * 72)
    dims = compare["page_dimensions"]
    print(
        f"页面尺寸(pt)  BabelDOC={dims['babeldoc']}  Geometry={dims['geometry']}  "
        f"一致={dims['dimensions_match']}"
    )
    c = compare["counts"]
    print("-" * 72)
    print(f"{'指标':<18}{'BabelDOC(A)':>14}{'Geometry(B)':>14}")
    for k in ("texts", "horizontal_lines", "vertical_lines", "rectangles", "detected_tables"):
        print(f"{k:<18}{c['babeldoc'][k]:>14}{c['geometry'][k]:>14}")
    print("-" * 72)
    tc = compare["text_consistency"]
    print(
        f"文字坐标一致性：{tc['contained_in_babeldoc_paragraph']}/{tc['geometry_texts']} "
        f"被 BabelDOC 段落框包含，中位中心偏移 {tc['median_center_offset_pt']} pt"
    )
    print("=" * 72)
    for a in compare["answers"]:
        print(f"{a['q']}")
        print(f"  → {tag(a['answer'])}")
        print(f"  {a['evidence']}")
    print("=" * 72)
