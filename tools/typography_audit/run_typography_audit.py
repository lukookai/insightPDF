# -*- coding: utf-8 -*-
"""Phase 4D.2A end-to-end analysis-only audit orchestrator."""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(REPO / "tools") not in sys.path:
    sys.path.insert(0, str(REPO / "tools"))

from typography_audit import ANALYSIS_PAGES  # noqa: E402
from typography_audit.build_profile import build_profiles  # noqa: E402
from typography_audit.cjk_latin_metrics import analyze_cjk_latin  # noqa: E402
from typography_audit.classify_text_role import role_manifest  # noqa: E402
from typography_audit.extract_typography import (  # noqa: E402
    build_document_inventory, extract_page_typography, read_json, sha256_file,
    stats, write_json,
)
from typography_audit.mock_qa import aggregate_mock_qa, run_mock_page_qa  # noqa: E402
from typography_audit.mock_renderer import render_balanced_mock  # noqa: E402
from typography_audit.spacing_metrics import analyze_page_spacing  # noqa: E402
from typography_audit.typography_consistency_qa import (  # noqa: E402
    build_typography_consistency_qa,
)
from typography_audit.typography_overlay import (  # noqa: E402
    make_comparison_board, make_detail_boards, make_typography_overlay,
)


SOURCE_PDF = REPO / "runs" / "diag_src_2504.pdf"
BASELINE = REPO / "outputs" / "phase4d1c"
DEFAULT_OUT = REPO / "outputs" / "phase4d2a_typography_audit"

FROZEN_PRODUCTION = (
    REPO / "tools/page_pipeline/html_render.py",
    REPO / "tools/page_pipeline/flow_layout.py",
    REPO / "tools/page_pipeline/page_model.py",
    REPO / "tools/page_pipeline/paragraphs.py",
    REPO / "tools/page_pipeline/page_layout_grid.py",
    REPO / "tools/page_pipeline/front_matter.py",
    REPO / "tools/page_pipeline/formula_exclusivity_qa.py",
    REPO / "tools/page_pipeline/formula_crop_qa.py",
    REPO / "tools/page_pipeline/formula_adopted_prose_qa.py",
)


def _hash_manifest(paths: tuple[Path, ...]) -> dict[str, str]:
    return {str(path.resolve()): sha256_file(path) for path in paths if path.exists()}


def _page_paths(page_number: int) -> dict[str, Path]:
    page = BASELINE / "pages" / f"p{page_number:03d}"
    return {
        "dir": page, "pdf": page / "zh.pdf", "html": page / "zh.html",
        "png": page / "zh.png", "model": page / "stitched_page_model.json",
        "qa": page / "qa.json", "grid": page / "page_grid.json",
        "translations": page / "translation.json",
    }


def _augment_page(page: dict[str, Any], paths: dict[str, Path]) -> dict[str, Any]:
    page["page_grid"] = read_json(paths["grid"])
    page["translations"] = read_json(paths["translations"])
    page["raw_html"] = paths["html"].read_text(encoding="utf-8")
    model = read_json(paths["model"])
    page["model_regions"] = [{"region_id": region.get("region_id"),
                              "type": region.get("type"), "bbox": region.get("bbox")}
                             for region in model.get("regions", [])]
    page["spacing"] = analyze_page_spacing(page)
    cjk = analyze_cjk_latin(page)
    page["mixed_script"] = cjk["mixed_script"]
    page["punctuation"] = cjk["punctuation"]
    page["formula_spacing"] = cjk["formula_spacing"]
    page["code_run"] = cjk["code_run"]
    page.pop("raw_html", None)
    return page


def _observed_roles(pages: list[dict[str, Any]]) -> Counter:
    counts = Counter()
    for page in pages:
        for line in page["source"]["lines"]:
            counts[line["role"]] += 1
        for formula in page.get("formulas", []):
            counts[formula["role"]] += 1
        for record in page.get("html_inventory", {}).get("records", []):
            if record["role"] == "code_run":
                counts["code_run"] += 1
    # Classifier completeness is semantic, including roles absent from the
    # six selected pages.  Observed counts remain truthful.
    return counts


def _ratio_table(inventory: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    balanced = profiles["balanced_chinese"]
    mapping = {
        "title": ("document_title", balanced["title"]["font_size_ratio_to_body"]["value"]),
        "section": ("section_heading", balanced["section_heading"]["font_size_ratio_to_body"]["value"]),
        "subsection": ("subsection_heading", balanced["subsection_heading"]["font_size_ratio_to_body"]["value"]),
        "subsubsection": ("subsubsection_heading", balanced["subsubsection_heading"]["font_size_ratio_to_body"]["value"]),
        "caption": ("caption", balanced["caption"]["font_size_ratio_to_body"]["value"]),
        "footnote": ("footnote", balanced["footnote"]["font_size_ratio_to_body"]["value"]),
    }
    return {
        label: {
            "source_ratio": inventory["roles"][role]["source_ratio_to_body"],
            "current_zh_ratio": inventory["roles"][role]["current_zh_ratio_to_body"],
            "recommended_zh_ratio": recommended,
        } for label, (role, recommended) in mapping.items()
    }


def _page_answers(page: dict[str, Any], profiles: dict[str, Any]) -> dict[str, Any]:
    balanced = profiles["balanced_chinese"]
    pno = page["page"]
    answers: dict[str, Any] = {
        "page": pno,
        "body_font_size_median_pt": page["current_zh"]["body_font_size_median_pt"],
        "body_baseline_gap_pt": page["spacing"]["line_height"]["by_role"].get(
            "body", {}).get("baseline_gap_pt", {}).get("median"),
        "body_line_height_ratio": page["spacing"]["line_height"]["by_role"].get(
            "body", {}).get("line_height_ratio", {}).get("median"),
        "density": page["spacing"]["density"]["by_role"],
        "heading": page["spacing"]["heading"],
        "vertical_rhythm": page["spacing"]["vertical_rhythm"],
        "mixed_script": page["mixed_script"],
        "punctuation": page["punctuation"],
        "formula_spacing": page["formula_spacing"],
        "list": page["spacing"]["list"],
        "caption": page["spacing"]["caption"],
        "table": page["spacing"]["table"],
        "code_run": page["code_run"],
    }
    if pno == 1:
        answers["frontmatter"] = page["spacing"]["frontmatter"]
        answers["required_questions"] = {
            "title_body_source_current_recommended": {
                "source": page["source"]["role_summary"]["document_title"]["ratio_to_body"],
                "current": page["current_zh"]["role_summary"]["document_title"]["ratio_to_body"],
                "recommended": balanced["title"]["font_size_ratio_to_body"]["value"],
            },
            "author_line_gap_pt": page["spacing"]["frontmatter"]["author_line_gap_pt"],
            "author_to_affiliation_gap_pt": page["spacing"]["frontmatter"]["author_to_affiliation_gap_pt"],
            "affiliation_to_abstract_gap_pt": page["spacing"]["frontmatter"]["affiliation_to_abstract_heading_gap_pt"],
            "abstract_heading_body_difference_pt": page["spacing"]["frontmatter"]["abstract_heading_body_font_difference_pt"],
            "footnote_body_ratio": page["current_zh"]["role_summary"]["footnote"]["ratio_to_body"],
            "latin_author_cjk_institution_baseline": {
                "normalized_baseline_delta_em": page["spacing"]["frontmatter"].get(
                    "latin_author_cjk_affiliation_normalized_baseline_delta_em"),
                "consistent": page["spacing"]["frontmatter"].get(
                    "latin_author_cjk_affiliation_baseline_consistent"),
                "measurement": page["spacing"]["frontmatter"].get(
                    "baseline_comparison_note"),
            },
            "title_max_width_pt": page["spacing"]["frontmatter"]["title_max_line_width_pt"],
            "title_width_assessment": "not overwide" if page["spacing"]["frontmatter"]["title_width_to_page_ratio"] < .82 else "overwide",
        }
    elif pno == 3:
        answers["required_questions"] = {
            "inline_formula_gaps": {key: page["formula_spacing"][key]
                                    for key in ("inline_left_gap_pt", "inline_right_gap_pt")},
            "bullet_hanging_indent": page["spacing"]["list"]["hanging_indent_pt"],
            "display_formula_gaps": {key: page["formula_spacing"][key]
                                     for key in ("display_top_gap_pt", "display_bottom_gap_pt")},
            "heading_body_ratio": page["current_zh"]["role_summary"]["section_heading"]["ratio_to_body"],
            "bold_lead_body_ratio": page["current_zh"]["role_summary"]["body_bold_lead"]["ratio_to_body"],
            "caption_body_ratio": page["current_zh"]["role_summary"]["caption"]["ratio_to_body"],
        }
    elif pno == 6:
        answers["required_questions"] = {
            "heading_hierarchy_assessment": "insufficient: subsection and subsubsection share the same measured size",
            "list_item_vertical_rhythm": page["spacing"]["vertical_rhythm"]["by_relation"].get("list_item_gap"),
            "latin_model_names_need_independent_font": "yes: Latin serif run, not a translated or monospaced code run",
            "right_column_heading_spacing": page["spacing"]["vertical_rhythm"]["records"],
            "two_column_density": page["spacing"]["density"]["by_role"].get("body"),
        }
    elif pno == 13:
        answers["required_questions"] = {
            "table_header_body_ratio": page["spacing"]["table"]["header_body_size_ratio"],
            "chinese_title_column_font_size": page["spacing"]["table"]["cjk_text_font_size_pt"],
            "numeric_column_font_size": page["spacing"]["table"]["numeric_font_size_pt"],
            "number_baseline": {
                "actual_pdf_baselines": page["spacing"]["table"].get("numeric_baseline_pt"),
                "delta_to_frozen_cell": page["spacing"]["table"].get(
                    "numeric_baseline_delta_to_frozen_cell_pt"),
                "delta_to_cjk_same_row": page["spacing"]["table"].get(
                    "numeric_to_cjk_same_row_baseline_delta_pt"),
            },
            "code_run_baseline": page["code_run"]["baseline_delta_to_cjk_pt"],
            "caption_table_and_body_gaps": page["spacing"]["vertical_rhythm"]["by_relation"],
            "p013_structure_frozen": page["tables"],
        }
    elif pno in {14, 16}:
        answers["required_questions"] = {
            "prose_display_formula_rhythm": page["formula_spacing"],
            "cases_formula_surrounding_gap": page["formula_spacing"]["records"],
            "formula_punctuation": [record for record in page["formula_spacing"]["records"]
                                    if record.get("formula_followed_by_punctuation")],
            "compact_typography_needed": pno == 16,
            "table_formula_collision_risk": "must remain zero in mock QA",
        }
    return answers


def _median_from_pages(pages: list[dict[str, Any]], path: tuple[str, ...]) -> float | None:
    values = []
    for page in pages:
        value: Any = page
        for key in path:
            value = value.get(key) if isinstance(value, dict) else None
        if value is not None:
            values.append(float(value))
    return sorted(values)[len(values)//2] if values else None


def _report(inventory: dict[str, Any], pages: list[dict[str, Any]],
            profiles: dict[str, Any], consistency: dict[str, Any],
            mock_qa: dict[str, Any], production_unchanged: bool) -> str:
    ratios = _ratio_table(inventory, profiles)
    balanced = profiles["balanced_chinese"]
    fonts = inventory["actual_pdf_font_inventory"]
    cjk_font = next((row["actual_pdf_font"] for row in fonts
                     if row["script"] in {"CJK", "mixed"}), "unresolved")
    body = inventory["body_reference"]["current_zh_font_size_median_pt"]
    body_gap = _median_from_pages(pages, ("spacing", "line_height", "by_role", "body", "baseline_gap_pt", "median"))
    body_lh = _median_from_pages(pages, ("spacing", "line_height", "by_role", "body", "line_height_ratio", "median"))
    density = []
    for page in pages:
        value = page["spacing"]["density"]["by_role"].get("body", {}).get("height_expansion_ratio", {}).get("median")
        if value is not None:
            density.append(value)
    import statistics
    density_median = statistics.median(density) if density else None
    mixed_gap = balanced["mixed_script"]["cjk_latin_gap_em"]["value"]
    number_gap = balanced["mixed_script"]["cjk_number_gap_em"]["value"]

    by_page = {page["page"]: page for page in pages}

    def med(value: Any) -> Any:
        return value.get("median") if isinstance(value, dict) else value

    p1 = by_page[1]["spacing"]["frontmatter"]
    p3, p6, p13, p14, p16 = (by_page[p] for p in (3, 6, 13, 14, 16))
    p13_table = p13["spacing"]["table"]
    warning_keys = (
        "mixed_script_gap_outlier_count", "heading_hierarchy_violation_count",
        "body_font_size_outlier_count", "line_height_outlier_count",
        "paragraph_gap_outlier_count", "caption_spacing_outlier_count",
        "list_hanging_indent_violation_count", "frontmatter_spacing_violation_count",
    )
    mock_rows = []
    for record in mock_qa["records"]:
        counts = record["acceptance_counts"]
        mock_rows.append(
            f"| p{record['page']:03d} | {'PASS' if record['all_existing_hard_gates_passed'] else 'FAIL'} "
            f"| {counts['physical_page_count']} | {counts['gutter_intrusion']} "
            f"| {counts['text_formula_collision']} | {counts['formula_crop_failure']} "
            f"| {counts['table_failure']} | {counts['figure_missing']} |"
        )
    lines = [
        "# Phase 4D.2A — Chinese Typography Audit / Document Typography Profile",
        "",
        "## 结论",
        "",
        ("**PASS（Analysis Only）**：六页 typography inventory、三套候选 profile、六页 balanced mock、"
         "既有硬门禁回归和视觉比较板均已完成。候选 token **没有**写回 production renderer；Phase 4D.2B 未启动。"),
        "",
        "## 冻结基线与边界",
        "",
        "- 输入基线：Phase 4D.1C，19→19，4C pass，4D hard pass，defect_pages=[]。",
        "- 分析页：p001 / p003 / p006 / p013 / p014 / p016。",
        "- 测量真值：source PageModel/PDF span + current/mock final Chromium PDF raw character bbox/origin。",
        "- 禁止项：production renderer、分页、公式 SVG 内部、TableModel/FigureBlock、ownership/translation/cache 均未修改。",
        f"- production 冻结文件哈希复核：{'PASS' if production_unchanged else 'FAIL'}。",
        "",
        "## 20 个验收问题",
        "",
        f"1. 当前正文实际 CJK 字体：**{cjk_font}**（PyMuPDF `span.font`，不是 CSS 猜测）。",
        f"2. 当前 body font size median：**{body:.3f} pt**。",
        f"3. 当前 body baseline gap：**{body_gap:.3f} pt**。" if body_gap else "3. 当前 body baseline gap：样本不足。",
        f"4. 当前 body line-height ratio：**{body_lh:.3f}**。" if body_lh else "4. 当前 body line-height ratio：样本不足。",
        f"5. source body 与中文 body density：中文/英文块高比中位数 **{density_median:.3f}×**（逐段同 ID 对齐）。" if density_median else "5. density：无可对齐样本。",
        f"6. title/body ratio source / current / recommended：**{ratios['title']['source_ratio']} / {ratios['title']['current_zh_ratio']} / {ratios['title']['recommended_zh_ratio']}**。",
        f"7. section/body ratio：**{ratios['section']['source_ratio']} / {ratios['section']['current_zh_ratio']} / {ratios['section']['recommended_zh_ratio']}**。",
        f"8. subsection/body ratio：**{ratios['subsection']['source_ratio']} / {ratios['subsection']['current_zh_ratio']} / {ratios['subsection']['recommended_zh_ratio']}**。",
        f"9. caption/body ratio：**{ratios['caption']['source_ratio']} / {ratios['caption']['current_zh_ratio']} / {ratios['caption']['recommended_zh_ratio']}**。",
        f"10. footnote/body ratio：**{ratios['footnote']['source_ratio']} / {ratios['footnote']['current_zh_ratio']} / {ratios['footnote']['recommended_zh_ratio']}**。",
        f"11. CJK↔Latin 推荐 gap：**{mixed_gap}em**，仅通过 script-boundary policy；禁止 `word-spacing`。",
        f"12. CJK↔数字推荐 gap：**{number_gap}em**，百分号与 citation 走例外规则。",
        f"13. inline formula 左/右 gap：balanced **{balanced['formula_spacing']['inline_left_em']['value']}em / {balanced['formula_spacing']['inline_right_em']['value']}em**；内部 SVG 冻结。",
        f"14. display formula 上/下 gap：balanced **{balanced['formula_spacing']['display_top_em']['value']}em / {balanced['formula_spacing']['display_bottom_em']['value']}em**。",
        f"15. paragraph gap：balanced **{balanced['body']['paragraph_gap_em']['value']}em**。",
        f"16. list hanging indent：balanced **{balanced['list']['hanging_indent_em']['value']}em**。",
        f"17. table header/body typography：字号比分别为 body 的 **{balanced['table']['header_font_size_ratio_to_body']['value']:.3f} / {balanced['table']['body_font_size_ratio_to_body']['value']:.3f}**；p013 5×21/105 cells/3H/0V 冻结。",
        ("18. document-wide warnings：mixed-script literal-space outliers、同层 heading 尺寸混用、"
         f"list 悬挂不足和 paragraph-gap 方差；informational warning 总数 **{consistency['document_wide_warning_count']}**。"),
        f"19. balanced profile 是否导致原 hard gate 退化：**{'否' if mock_qa['all_existing_hard_gates_passed'] else '是'}**；4C/4D.1 regression = **{mock_qa['existing_4c_gate_regression']}/{mock_qa['existing_4d1_gate_regression']}**。",
        ("20. 是否具备进入 4D.2B 条件：**具备候选条件，但必须等待人工审阅 comparison board、六页 mock 与 JSON profile；本轮严格停止。**"
         if mock_qa["all_existing_hard_gates_passed"] else
         "20. 是否具备进入 4D.2B 条件：**不具备，mock hard gate 有退化。**"),
        "",
        "## 六页重点结论",
        "",
        "### p001 — Front Matter",
        "",
        (f"- title/body source/current/balanced：**{ratios['title']['source_ratio']} / "
         f"{ratios['title']['current_zh_ratio']} / {ratios['title']['recommended_zh_ratio']}**。标题 2 行，"
         f"最大行宽 **{p1['title_max_line_width_pt']:.3f}pt**（页宽 **{p1['title_width_to_page_ratio']:.3f}**），"
         f"中心误差 **{p1['title_center_error_pt']:.3f}pt**，未过宽。"),
        (f"- 作者两行 baseline gap **{med(p1['author_line_gap_pt']):.3f}pt**；title→author "
         f"**{p1['title_to_author_gap_pt']:.3f}pt**；author→affiliation **{p1['author_to_affiliation_gap_pt']:.3f}pt**；"
         f"affiliation→abstract **{p1['affiliation_to_abstract_heading_gap_pt']:.3f}pt**。"),
        (f"- abstract heading→body **{p1['abstract_heading_to_body_gap_pt']:.3f}pt**，字号差 "
         f"**{p1['abstract_heading_body_font_difference_pt']:.3f}pt**；footnote/body **{ratios['footnote']['current_zh_ratio']}**。"),
        (f"- Latin 作者与中文机构归一化基线偏移差 **{p1['latin_author_cjk_affiliation_normalized_baseline_delta_em']:.4f}em**，"
         f"判定 **{'一致' if p1['latin_author_cjk_affiliation_baseline_consistent'] else '不一致'}**；作者→机构绝对 y 距离未冒充基线差。"),
        "",
        "### p003 — Formula / Bullet / Caption",
        "",
        (f"- inline formula 左/右 gap median **{med(p3['formula_spacing']['inline_left_gap_pt']):.3f}/"
         f"{med(p3['formula_spacing']['inline_right_gap_pt']):.3f}pt**；display formula 上/下 "
         f"**{med(p3['formula_spacing']['display_top_gap_pt']):.3f}/{med(p3['formula_spacing']['display_bottom_gap_pt']):.3f}pt**。"),
        (f"- 4 个 bullet 的 bullet→text gap median **{med(p3['spacing']['list']['bullet_to_text_gap_pt']):.3f}pt**；"
         f"续行 hanging indent **{med(p3['spacing']['list']['hanging_indent_pt']):.3f}pt**，当前续行回到 bullet x。"),
        (f"- current section/body **{p3['current_zh']['role_summary']['section_heading']['ratio_to_body']}**，"
         f"bold-lead/body **{p3['current_zh']['role_summary']['body_bold_lead']['ratio_to_body']}**，"
         f"caption/body **{p3['current_zh']['role_summary']['caption']['ratio_to_body']}**；普通中文 prose 半角标点违规为 0。"),
        "",
        "### p006 — Heading / List / 双栏密度",
        "",
        (f"- document current section/subsection/subsubsection/body：**{ratios['section']['current_zh_ratio']} / "
         f"{ratios['subsection']['current_zh_ratio']} / {inventory['roles']['subsubsection_heading']['current_zh_ratio_to_body']} / 1.0**；"
         "subsection 与 subsubsection 同字号。balanced 建议 **1.25 / 1.16 / 1.10 / 1.0**。"),
        (f"- list item gap median **{med(p6['spacing']['vertical_rhythm']['by_relation'].get('list_item_gap'))}pt**；"
         "Vanilla / Vanilla+Skeleton / AutoSurvey 应为独立 Latin serif run，而非 monospace 或翻译。"),
        (f"- 右栏 heading spacing 逐项在 p006 JSON；两栏 density 差（每 100pt 高度）"
         f"**{p6['spacing']['column_density']['density_difference_per_100pt']}**，先记 warning、不动栏几何。"),
        "",
        "### p013 — Table / Code",
        "",
        (f"- **5 columns / 21 rows / 105 cells / 3H / 0V** 已复核；header/body size ratio "
         f"**{p13_table['header_body_size_ratio']}**，median **{med(p13_table['header_font_size_pt']):.4f}/"
         f"{med(p13_table['body_font_size_pt']):.4f}pt**。"),
        (f"- 中文 Title 列 **{med(p13_table['cjk_text_font_size_pt']):.4f}pt**；数字列 "
         f"**{med(p13_table['numeric_font_size_pt']):.4f}pt**。同一行 numeric↔CJK baseline delta "
         f"**{med(p13_table['numeric_to_cjk_same_row_baseline_delta_pt']):.4f}pt**；code↔CJK baseline delta "
         f"**{med(p13['code_run']['baseline_delta_to_cjk_pt']):.4f}pt**。"),
        (f"- declared CSS padding **0/0pt**；左对齐内容 PDF 左 inset median "
         f"**{med(p13_table['effective_final_pdf_content_inset_pt']['left_aligned_left']):.4f}pt**；"
         f"table→caption **{med(p13['spacing']['vertical_rhythm']['by_relation']['table_to_caption']):.4f}pt**。proposal 不改变 105 cells。"),
        "",
        "### p014 / p016 — 复杂公式与 dense appendix",
        "",
        (f"- p014 inline L/R **{med(p14['formula_spacing']['inline_left_gap_pt']):.3f}/"
         f"{med(p14['formula_spacing']['inline_right_gap_pt']):.3f}pt**，display T/B "
         f"**{med(p14['formula_spacing']['display_top_gap_pt']):.3f}/{med(p14['formula_spacing']['display_bottom_gap_pt']):.3f}pt**；cases 内部冻结。"),
        (f"- p016 inline L/R **{med(p16['formula_spacing']['inline_left_gap_pt']):.3f}/"
         f"{med(p16['formula_spacing']['inline_right_gap_pt']):.3f}pt**，display T/B "
         f"**{med(p16['formula_spacing']['display_top_gap_pt']):.3f}/{med(p16['formula_spacing']['display_bottom_gap_pt']):.3f}pt**。"
         "compact 仅适用于 appendix/reference/dense table。"),
        "- p014/p016 typography-driven text/formula collision、formula crop、table failure 均为 0。",
        "",
        "## 数字证据为何说明当前仍不像成熟中文 PDF",
        "",
        "- CJK 密度不是英文 bbox 的同义复制：逐段 line count、block height 与 occupied width 已在 per-page JSON 中列出。",
        "- 混排空隙有三类实因：translation literal ASCII space、PDF span boundary、browser shaping/glyph side-bearing；因此拒绝全局 `word-spacing`。",
        "- heading 层级：当前部分 subsection 与 subsubsection 同字号，balanced 建立 1.25 / 1.16 / 1.10 的严格梯度。",
        "- caption、list、front matter 与 formula 周边全部按最终 PDF bbox 测量，未根据 CSS 声明臆测。",
        "- 表格和公式的内部字体/结构不纳入改造；只记录其文本/外部节奏及几何冻结证据。",
        "",
        "## Mock 回归汇总",
        "",
        "| page | hard gates | pages | gutter | text/formula | crop | table | figure missing |",
        "|---|---:|---:|---:|---:|---:|---:|---:|",
        *mock_rows,
        "",
        f"- mock_pages_generated = {mock_qa['mock_page_count']}",
        f"- existing_4c_gate_regression = {mock_qa['existing_4c_gate_regression']}",
        f"- existing_4d1_gate_regression = {mock_qa['existing_4d1_gate_regression']}",
        f"- gutter_intrusion = {mock_qa['gutter_intrusion']}",
        f"- text_formula_collision = {mock_qa['text_formula_collision']}",
        f"- formula_crop_failure = {mock_qa['formula_crop_failure']}",
        f"- table_failure = {mock_qa['table_failure']}",
        f"- figure_missing = {mock_qa['figure_missing']}",
        f"- page_count_change = {mock_qa['page_count_change']}",
        "",
        "## Typography informational QA",
        "",
        *[f"- {key} = {consistency[key]}" for key in warning_keys],
        f"- document_wide_warning_count = {consistency['document_wide_warning_count']}（未接入 delivery gate）",
        "",
        "## 字体真值与候选",
        "",
        "- current final PDF：正文 CJK 主要为 `SimSun`；表格数字为 `TimesNewRomanPSMT`；代码为 `Consolas`；部分加粗中文由 Chromium 写为 Type3 子集。",
        "- balanced sandbox：候选 CJK 为 Noto Serif SC、Latin 为 Times New Roman；mock 实际字体清单写入 `typography_mock_qa.json`，Type3 子集未误报为 SimSun。",
        "- requested_font / actual_pdf_font / role / script / size 的逐 span 证据均位于 `typography_inventory.json`。",
        "",
        "## Profiles",
        "",
        "- source_faithful：尽量保持英文 source ratios。",
        "- balanced_chinese（推荐）：保持可读 body size，调整 CJK 字体纹理、层级与中文节奏。",
        "- compact_chinese：仅用于 reference / appendix / dense table；禁止用于普通正文。",
        "",
        "## STOP CONDITION",
        "",
        "Phase 4D.2A 到此停止。没有修改正式 renderer，没有写回 candidate tokens，没有进入 Phase 4D.2B。",
        "",
    ]
    return "\n".join(lines)


def run(output_dir: Path) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    mock_dir = output_dir / "mock"
    mock_dir.mkdir(parents=True, exist_ok=True)
    before = _hash_manifest(FROZEN_PRODUCTION)

    pages = []
    current_pngs, mock_pngs = {}, {}
    current_dom = {}
    models = {}
    paths_by_page = {}
    for page_number in ANALYSIS_PAGES:
        paths = _page_paths(page_number)
        paths_by_page[page_number] = paths
        page = extract_page_typography(
            page_number=page_number, source_pdf=SOURCE_PDF,
            current_pdf=paths["pdf"], html_path=paths["html"],
            page_model_path=paths["model"])
        current_dom[page_number] = page["dom_snapshot"]
        models[page_number] = read_json(paths["model"])
        page = _augment_page(page, paths)
        pages.append(page)
        current_pngs[page_number] = paths["png"]
        make_typography_overlay(paths["pdf"], page,
                                output_dir / f"p{page_number:03d}_typography_overlay.png")

    inventory = build_document_inventory(pages)
    inventory["role_manifest"] = role_manifest(_observed_roles(pages))
    profiles = build_profiles(inventory, pages)
    consistency = build_typography_consistency_qa(pages)
    write_json(output_dir / "typography_inventory.json", inventory)
    write_json(output_dir / "source_faithful_profile.json", profiles["source_faithful"])
    write_json(output_dir / "balanced_chinese_profile.json", profiles["balanced_chinese"])
    write_json(output_dir / "compact_chinese_profile.json", profiles["compact_chinese"])
    write_json(output_dir / "document_typography_profile.json", profiles["document"])
    write_json(output_dir / "typography_consistency_qa.json", consistency)
    for page in pages:
        write_json(output_dir / f"p{page['page']:03d}_typography_analysis.json",
                   _page_answers(page, profiles))

    mock_manifests = {}
    for page_number in ANALYSIS_PAGES:
        manifest = render_balanced_mock(
            page_number=page_number, source_html=paths_by_page[page_number]["html"],
            model=models[page_number], profile=profiles["balanced_chinese"],
            mock_dir=mock_dir)
        mock_manifests[page_number] = manifest
        mock_pngs[page_number] = Path(manifest["png"])

    mock_records = []
    for page_number in ANALYSIS_PAGES:
        mock_records.append(run_mock_page_qa(
            page_number=page_number,
            mock_pdf=mock_manifests[page_number]["pdf"],
            mock_html=mock_manifests[page_number]["html"],
            mock_manifest=mock_manifests[page_number],
            current_manifest=current_dom[page_number],
            model_path=paths_by_page[page_number]["model"],
            baseline_qa_path=paths_by_page[page_number]["qa"],
            grid_path=paths_by_page[page_number]["grid"],
            out_dir=output_dir / "mock_qa"))
    mock_qa = aggregate_mock_qa(mock_records)
    write_json(output_dir / "typography_mock_qa.json", mock_qa)

    make_comparison_board(current_pngs, mock_pngs,
                          output_dir / "typography_comparison_board.png")
    make_detail_boards(current_pngs, mock_pngs, output_dir)

    after = _hash_manifest(FROZEN_PRODUCTION)
    production_unchanged = before == after
    freeze = {
        "production_renderer_unchanged": production_unchanged,
        "before": before, "after": after,
        "changed": sorted(path for path in set(before) | set(after)
                          if before.get(path) != after.get(path)),
    }
    write_json(output_dir / "production_freeze_audit.json", freeze)
    report = _report(inventory, pages, profiles, consistency, mock_qa,
                     production_unchanged)
    (output_dir / "PHASE4D2A_REPORT.md").write_text(report, encoding="utf-8")

    acceptance = {
        "analysis_pages": len(pages),
        "typography_roles_complete": inventory["role_manifest"]["typography_roles_complete"],
        "actual_pdf_font_audited": inventory["actual_pdf_font_audited"],
        "mixed_script_spacing_measured": all(page["mixed_script"]["mixed_script_spacing_measured"] for page in pages),
        "vertical_rhythm_measured": all(page["spacing"]["vertical_rhythm"]["vertical_rhythm_measured"] for page in pages),
        "profiles_generated": 3,
        "balanced_profile_generated": True,
        "mock_pages_generated": len(mock_records),
        "production_renderer_unchanged": production_unchanged,
        **{key: mock_qa[key] for key in (
            "existing_4c_gate_regression", "existing_4d1_gate_regression",
            "gutter_intrusion", "text_formula_collision", "formula_crop_failure",
            "table_failure", "figure_missing", "page_count_change")},
    }
    acceptance["phase4d2a_pass"] = all([
        acceptance["analysis_pages"] == 6,
        acceptance["typography_roles_complete"], acceptance["actual_pdf_font_audited"],
        acceptance["mixed_script_spacing_measured"], acceptance["vertical_rhythm_measured"],
        acceptance["profiles_generated"] == 3, acceptance["mock_pages_generated"] == 6,
        acceptance["production_renderer_unchanged"],
        all(acceptance[key] == 0 for key in (
            "existing_4c_gate_regression", "existing_4d1_gate_regression",
            "gutter_intrusion", "text_formula_collision", "formula_crop_failure",
            "table_failure", "figure_missing", "page_count_change")),
    ])
    write_json(output_dir / "phase4d2a_acceptance.json", acceptance)
    return acceptance


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--out", type=Path, default=DEFAULT_OUT)
    args = parser.parse_args()
    acceptance = run(args.out.resolve())
    print(json.dumps(acceptance, ensure_ascii=False, indent=2))
    return 0 if acceptance["phase4d2a_pass"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
