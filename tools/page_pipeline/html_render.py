# -*- coding: utf-8 -*-
"""Unified page HTML renderer for Phase 4B - paragraph flow.

One Page HTML Renderer orchestrates:
* text     -> ONE .paragraph-block per ParagraphModel (position:absolute at
              the column box, white-space:normal, overflow:visible) so
              Chinese reflows inside the column width.  Source lines are
              geometry evidence only - never the layout unit.
              Inline formula placeholders ({{FORMULA_Bn}}) become
              inline-block wrappers (fixed w/h + page-SVG crop) that flow
              with the paragraph text.
* table    -> reuses the existing locked table renderer's cell divs + rules
              (extracted from build_zh_html output - adapter, no copy);
* formula  -> display formulas stay geometry locked (page-level SVG asset +
              per-RenderSegment clip-path <img> at the original bbox);
* figure   -> original region clipped as PNG.
All coordinates are PDF pt; @page is fixed; no responsive layout.
"""
from __future__ import annotations

import html as _html
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for p in (HERE, REPO / "tools" / "table_html_render",
          REPO / "tools" / "formula_html_render",
          REPO / "实现源码" / "pdf_translator"):
    if str(p) not in sys.path:
        sys.path.insert(0, str(p))

from cjk_fonts import detect_cjk_serif  # noqa: E402
from render_zh import build_zh_html  # noqa: E402
from svg_formula import page_svg, crop_svg  # noqa: E402
from script_spacing import insert_script_gaps, script_boundary_audit  # noqa: E402

LINE_HEIGHT = 1.3  # em, used by the flow estimate and the CSS line-height


def _esc(t):
    return _html.escape(t or "", quote=True)


def _extract_table_parts(table_model, page_w, page_h, cjk_family):
    """Adapter: reuse the locked table renderer, then extract its cell divs
    and rule SVGs for embedding into the unified page."""
    html = build_zh_html(table_model, page_w, page_h, cjk_family,
                         semantic=False)
    cells = re.findall(r'<div class="translated-cell"[^>]*>.*?</div>',
                       html, flags=re.S)
    rules = re.findall(r'<svg[^>]*>.*?</svg>', html, flags=re.S)
    return cells, rules


def _inline_formula_map(page_model):
    """token -> list of render_segment bboxes for INLINE formulas."""
    out = {}
    for r in page_model["regions"]:
        if r["type"] != "formula":
            continue
        fm = r["payload"]
        if fm.get("placement") != "inline":
            continue
        segs = [seg["render_viewbox"] or seg["layout_bbox"]
                for seg in fm.get("render_segments", [])]
        out["{{FORMULA_%s}}" % fm["formula_id"]] = segs
    return out


def _display_formula_boxes(page_model):
    """DisplayFormulaBlocks (flow_locked display formulas, Phase 4C.2R).

    Returns a list of dicts, one per display / complex_display / cases
    formula:

        {"formula_id": "B11",
         "bbox": [x0, y0, x1, y1],      # enclosure bbox (layout_bbox)
         "anchor_y": y0,                 # preferred_anchor_y (source y)
         "segments": [[x0,y0,x1,y1], ...],  # per-RenderSegment boxes
         "height": h}                    # enclosure height

    The formula is flow_locked: internal geometry (x, width, glyph layout,
    segment relative positions) is unchanged; only the container y is
    decided by ColumnFlow (preferred_anchor_y is a hint, not a hard y).
    Inline formulas are NOT included (they stay in the text flow).
    """
    out = []
    for r in page_model["regions"]:
        if r["type"] != "formula":
            continue
        fm = r["payload"]
        if fm.get("placement") == "inline":
            continue
        b = fm.get("layout_bbox")
        if not b or len(b) != 4:
            continue
        bbox = [float(v) for v in b]
        segs = []
        for seg in fm.get("render_segments", []):
            sb = seg.get("render_viewbox") or seg.get("layout_bbox")
            if sb and len(sb) == 4:
                segs.append([float(v) for v in sb])
        if not segs:
            segs = [bbox]
        # Phase 4C.2R.1: the reserved box must cover the formula's FULL
        # visual footprint -- the per-segment render_viewboxes already
        # include adopted condition ink and equation numbers plus the crop
        # safety margin.  Reserving only the math layout bbox lets flowed
        # body text land on the condition rows (p005 B19: the translated
        # paragraph overlapped the adopted "And we select top-k the next
        # layer After L layers," rows).
        fx0 = min(s[0] for s in segs)
        fy0 = min(s[1] for s in segs)
        fx1 = max(s[2] for s in segs)
        fy1 = max(s[3] for s in segs)
        bbox = [fx0, fy0, fx1, fy1]
        out.append({
            "formula_id": fm["formula_id"],
            "bbox": bbox,
            "anchor_y": bbox[1],
            "segments": segs,
            "height": bbox[3] - bbox[1],
        })
    return out


def _obstacle_boxes(page_model):
    """geometry-locked regions that flow must not enter (table/image/figure)."""
    out = []
    for r in page_model["regions"]:
        if r["type"] in ("table", "image", "figure"):
            out.append([float(v) for v in r["bbox"]])
    return out


def _inline_wrapper(token, segs, svg_name, page_w, page_h, font_size,
                    out_dir=None, full_svg=None, gap_l=0.0, gap_r=0.0):
    """Inline-block wrapper holding a per-segment cropped SVG.

    ``segs``: list of segment render boxes (multiple segments -> multiple
    inline-blocks placed adjacently, flowing with the text).

    ``gap_l`` / ``gap_r``: outer inline-formula horizontal rhythm (em)
    applied as a margin on the wrapper ONLY -- the internal SVG / viewBox /
    glyph geometry is untouched (Phase 4D.2B formula-surrounding gap).

    Phase 4C.2R: each segment gets its own cropped SVG file (crop_svg:
    same glyphs, viewBox = segment bbox).  The <img> is a plain 1:1 placed
    image -- NO clip-path / overflow:hidden / transform, because Chromium
    print loses those on multiple whole-page SVG <img> elements and leaks
    the rest of the page SVG (p015 rendered the English source body).
    """
    if out_dir is None or full_svg is None:
        # fallback (callers that do not provide a crop context)
        return _inline_wrapper_legacy(token, segs, svg_name, page_w, page_h,
                                      font_size)
    parts = []
    for b in segs:
        w = max(b[2] - b[0], 1.0)
        h = max(b[3] - b[1], 1.0)
        va = 0.0
        if font_size > 0:
            va = -0.25  # slight raise for optical centering
        seg_key = token.strip("{}")
        seg_name = "inline_%s_%d.svg" % (seg_key, len(parts))
        seg_svg = crop_svg(full_svg, b)
        (out_dir / seg_name).write_text(seg_svg, encoding="utf-8")
        margin = ""
        if gap_l or gap_r:
            margin = "margin-left:%.3fem;margin-right:%.3fem;" % (gap_l, gap_r)
        parts.append(
            '<span class="formula-inline" data-formula="%s" '
            'style="display:inline-block;width:%.3fpt;height:%.3fpt;'
            'vertical-align:%.3fem;position:relative;%s">'
            '<img src="%s" style="width:%.3fpt;height:%.3fpt;max-width:none;"/>'
            '</span>'
            % (seg_key, w, h, va, margin, seg_name, w, h))
    return "".join(parts)


def _inline_wrapper_legacy(token, segs, svg_name, page_w, page_h, font_size):
    """Fallback wrapper used when no crop context is available."""
    parts = []
    for b in segs:
        w = max(b[2] - b[0], 1.0)
        h = max(b[3] - b[1], 1.0)
        va = 0.0
        if font_size > 0:
            va = -0.25
        clip = "inset(%.3fpt %.3fpt %.3fpt %.3fpt)" % (
            b[1], page_w - b[2], page_h - b[3], b[0])
        parts.append(
            '<span class="formula-inline" data-formula="%s" '
            'style="display:inline-block;width:%.3fpt;height:%.3fpt;'
            'vertical-align:%.3fem;position:relative;">'
            '<img src="%s" style="position:absolute;left:0;top:0;'
            'width:%.3fpt;height:%.3fpt;max-width:none;'
            'transform:translate(%.3fpt,%.3fpt);clip-path:%s;"/>'
            '</span>'
            % (token.strip("{}"), w, h, va, svg_name, page_w, page_h,
               -b[0], -b[1], clip))
    return "".join(parts)


def _render_paragraph_text(zh, para, inline_map, svg_name, page_w, page_h,
                           font_size, out_dir=None, full_svg=None,
                           latin_gap=0.0, number_gap=0.0,
                           inline_gap_l=0.0, inline_gap_r=0.0,
                           gap_collector=None):
    """Restore formula, CodeRun and inline-style placeholders to HTML.

    ``latin_gap`` / ``number_gap``: balanced script-boundary gap (em) applied
    to the plain prose ONLY -- never inside a protected term / code run /
    formula placeholder (Phase 4D.2B).
    ``inline_gap_l`` / ``inline_gap_r``: outer inline-formula margin (em).
    ``gap_collector``: optional list to accumulate ``(text, gaps)`` pairs for
    the ScriptBoundaryQA report.
    """
    protected = para.get("protected_runs") or {}
    token_re = re.compile(r"\{\{[A-Z_0-9]+\}\}")
    # Phase 4C.2E: long protected model identifiers must never break across
    # lines inside the paragraph (Chromium's overflow-wrap:anywhere would
    # otherwise split them).  Wrapped in a nowrap span.
    _PROTECTED_TERMS = (
        "LLM×MapReduce-V2", "AutoSurvey", "SurveyEval", "DeepSeek-R1",
        "Gemini-2.0-flash-thinking-exp-1219", "Qwen2.5-72B-Instruct-AWQ-YARN-128k",
        "nomic-embed-text-v1", "FactScore", "Self-Refinement", "Best-of-N",
        "Entropy-Driven", "Topology-Aware", "Digest-Based", "Test-Time",
        "Omni-Think", "SurveySum", "SciReviewGen",
    )
    def _protect_text(text):
        out_parts = []
        rest = text
        for term in sorted(_PROTECTED_TERMS, key=len, reverse=True):
            if term in rest:
                segs = rest.split(term)
                for i, seg in enumerate(segs):
                    if i > 0:
                        out_parts.append(
                            '<span class="protected-token">%s</span>'
                            % _esc(term))
                    out_parts.append(seg)
                rest = "".join(out_parts)
                out_parts = []
                break
        return rest if not out_parts else "".join(out_parts)

    def _space_and_protect(text):
        # script-boundary spacing on the plain prose, THEN protect model
        # names; the pairwise gap rule never splits a Latin-only term anyway,
        # and the protect pass keeps them atomic for line-breaking.
        if not text:
            return text
        if latin_gap or number_gap:
            spaced, gaps = insert_script_gaps(text, latin_gap, number_gap)
            if gap_collector is not None and gaps:
                gap_collector.append((text, gaps))
            return _protect_text(spaced)
        return _protect_text(text)

    out = []
    pos = 0
    for m in token_re.finditer(zh):
        plain = _space_and_protect(zh[pos:m.start()])
        out.append(plain)
        token = m.group(0)
        if token in inline_map:
            segs = inline_map[token]
            out.append(_inline_wrapper(token, segs, svg_name, page_w,
                                       page_h, font_size, out_dir, full_svg,
                                       gap_l=inline_gap_l, gap_r=inline_gap_r))
        elif token in protected:
            out.append('<code class="code-run" data-code-token="%s">%s</code>'
                       % (_esc(token.strip("{}")), _esc(protected[token])))
        elif token.startswith("{{END_BOLD_"):
            out.append("</strong>")
        elif token.startswith("{{BOLD_"):
            out.append('<strong class="inline-bold">')
        elif token.startswith("{{END_ITALIC_"):
            out.append("</em>")
        elif token.startswith("{{ITALIC_"):
            out.append('<em class="inline-italic">')
        # Unknown protected tokens are intentionally not printed.
        pos = m.end()
    out.append(_space_and_protect(zh[pos:]))
    return "".join(out)


_INSTITUTION_ZH_RE = re.compile(r"[\u4e00-\u9fff]+?(?:大学|学院|研究院|学校|研究所|集团|公司|中心|实验室)")


def _split_author_affiliation(text):
    """Split a mixed "author names + affiliations" translation at the
    first institution token (p001 DLP00010: "Zhiyuan Liu1 Maosong Sun†
    清华大学 北京邮电大学 南洋理工大学").  Render-level split only -- the
    translation unit is untouched.  Leading superscript placeholders
    ("{{FORMULA_B7}} {{FORMULA_B8}}北京交通大学 ...") stay with the
    institution part."""
    m = _INSTITUTION_ZH_RE.search(text or "")
    if not m:
        return text, ""
    author = text[:m.start()].strip()
    aff = text[m.start():].strip()
    if author and not re.search(r"[A-Za-z\u4e00-\u9fff]",
                                re.sub(r"\{\{[A-Z_0-9]+\}\}", "", author)):
        aff = (author + " " + aff).strip()  # pure placeholders -> aff marker
        author = ""
    return author, aff


def _assign_frontmatter_paras(frontmatter, para_regions):
    """para_id -> front-matter role, matched by y band + style role.

    Band matching is x-constrained: abstract_body must sit inside the LEFT
    track (p001's right-column body at y 489 was wrongly absorbed), and
    rotated/marginal text (the vertical arXiv stamp at x 10.9) is never a
    front-matter block.
    """
    fm = frontmatter or {}
    if not fm:
        return {}
    fg = fm.get("grid") or {}
    cf = fg.get("content_frame") or {}
    gutter = fg.get("gutter")
    cf_x0 = cf.get("x0", -999.0)
    gut_x0 = gutter["x0"] if gutter else cf_x0 + 500.0
    bands = []
    if fm.get("title") and fm["title"].get("bbox"):
        b = fm["title"]["bbox"]
        bands.append(("title", b[1] - 3, b[3] + 3))
    ab = (fm.get("authors") or {}).get("bbox")
    if ab:
        bands.append(("authors", ab["y0"] - 3, ab["y1"] + 3))
    fb = (fm.get("affiliations") or {}).get("bbox")
    if fb:
        bands.append(("affiliations", fb["y0"] - 3, fb["y1"] + 3))
    abh = fm.get("abstract_heading_bbox")
    if abh:
        bands.append(("abstract_heading", abh[1] - 3, abh[3] + 3))
    abb = (fm.get("abstract_body") or {}).get("bbox")
    if abb:
        bands.append(("abstract_body", abb["y0"] - 3, abb["y1"] + 3,
                      True))  # x-constrained
    fn_bboxes = [f["bbox"] for f in (fm.get("footnotes") or [])]
    out = {}
    for r in para_regions:
        para = r["payload"]
        bb = para.get("bbox") or [0, 0, 0, 0]
        cy = (bb[1] + bb[3]) / 2.0
        role = para.get("style_role") or "body"
        pid = para.get("paragraph_id")
        # marginal/rotated text (arXiv stamp) is never front matter
        if bb[0] < cf_x0 - 8:
            continue
        if role == "caption":
            out[pid] = "caption"
            continue
        if role == "list_item" and fn_bboxes:
            for fb2 in fn_bboxes:
                if fb2[1] - 6 <= cy <= fb2[3] + 6:
                    out[pid] = "footnote"
                    break
            if pid in out:
                continue
        for band in bands:
            name, y0, y1 = band[0], band[1], band[2]
            x_ok = (len(band) < 4 or bb[0] < gut_x0)
            if x_ok and y0 <= cy <= y1:
                out[pid] = name
                break
    return out


def _render_frontmatter_blocks(para_map, roles, translations, grid, fm,
                               ty, inline_map, svg_name, page_w, page_h,
                               out_dir, full_svg,
                               bottom_reserved_regions=None,
                               latin_gap=0.0, number_gap=0.0,
                               inline_gap_l=0.0, inline_gap_r=0.0,
                               gap_collector=None, body_family=None):
    """HTML for Title / Author / Affiliation / Abstract / Caption /
    Footnote blocks.  Geometry: full-width blocks span the content frame;
    the abstract keeps its source inset inside the left track; caption is
    bound to the figure; footnotes sit at the page bottom."""
    parts = []
    cf = grid["content_frame"]
    cf_w = max(cf["x1"] - cf["x0"], 1.0)
    track0 = grid["columns"][0]
    track0_w = max(track0["x1"] - track0["x0"], 1.0)

    def para_bbox(pid):
        return para_map[pid]["payload"].get("bbox") or [0, 0, 0, 0]

    def inner_zh(pid):
        zh = translations.get(pid, "")
        if not zh.strip():
            return ""
        para = para_map[pid]["payload"]
        fsize = para.get("base_font_size") or 10.0
        return _render_paragraph_text(zh, para, inline_map, svg_name,
                                      page_w, page_h, fsize,
                                      out_dir, full_svg,
                                      latin_gap=latin_gap, number_gap=number_gap,
                                      inline_gap_l=inline_gap_l,
                                      inline_gap_r=inline_gap_r,
                                      gap_collector=gap_collector)

    def block(pid, cls, left, top, width, extra_style=""):
        inner = inner_zh(pid)
        if not inner:
            return ""
        return ('<div class="%s" data-para="%s" '
                'style="position:absolute;left:%.3fpt;'
                'top:%.3fpt;width:%.3fpt;%s">%s</div>'
                % (cls, pid, left, top, width, extra_style, inner))

    # ---- TitleBlock: full width, centered, source y ----
    title_ids = [pid for pid, role in roles.items() if role == "title"]
    if title_ids:
        pid = sorted(title_ids, key=lambda p: para_bbox(p)[1])[0]
        fm_title = (fm or {}).get("title") or {}
        top = fm_title.get("bbox", para_bbox(pid))[1]
        parts.append(block(pid, "title-block", cf["x0"], top, cf_w))

    # ---- AuthorBlock / AffiliationBlock: merged, centered, full width.
    #      A mixed paragraph ("Zhiyuan Liu1 Maosong Sun† 清华大学 ...")
    #      contributes its author part to the author block and its
    #      institution part to the affiliation block (render-level split).
    fm_para_ids = {pid for pid, role in roles.items()
                   if role in ("authors", "affiliations")}

    def _clean_part(text):
        """Drop parts that are only placeholder markers / whitespace."""
        if not text:
            return ""
        stripped = re.sub(r"\{\{[A-Z_0-9]+\}\}|\{\{(?:END_)?[A-Z]+_\d+\}\}",
                          "", text).strip()
        return text if (re.search(r"[\u4e00-\u9fffA-Za-z]", stripped)) else ""

    def _render_part(pid, part):
        part = _clean_part(part)
        if not part:
            return None
        para = para_map[pid]["payload"]
        fsize = para.get("base_font_size") or 10.0
        html = _render_paragraph_text(part, para, inline_map, svg_name,
                                      page_w, page_h, fsize,
                                      out_dir, full_svg,
                                      latin_gap=latin_gap, number_gap=number_gap,
                                      inline_gap_l=inline_gap_l,
                                      inline_gap_r=inline_gap_r,
                                      gap_collector=gap_collector)
        # each logical paragraph keeps its own data-para for the visual QA
        return ('<span data-para="%s" data-role="%s">%s</span>'
                % (pid, para.get("style_role") or "body", html))

    author_html = []
    aff_html = []
    for pid in sorted(fm_para_ids,
                      key=lambda p: (para_bbox(p)[1], para_bbox(p)[0])):
        zh = translations.get(pid, "")
        if not zh:
            continue
        a_part, f_part = _split_author_affiliation(zh)
        for html_list, part in ((author_html, a_part), (aff_html, f_part)):
            h = _render_part(pid, part)
            if h:
                html_list.append(h)
    if author_html:
        fm_auth = ((fm or {}).get("authors") or {}).get("bbox") or {}
        top = fm_auth.get("y0")
        if top is None and fm_para_ids:
            top = para_bbox(sorted(fm_para_ids,
                                   key=lambda p: para_bbox(p)[1])[0])[1]
        parts.append(
            '<div class="author-block" style="position:absolute;'
            'left:%.3fpt;top:%.3fpt;width:%.3fpt;">%s</div>'
            % (cf["x0"], top or 121.7, cf_w, " ".join(author_html)))
    if aff_html:
        fm_aff = ((fm or {}).get("affiliations") or {}).get("bbox") or {}
        # the affiliation block must clear the (possibly multi-line)
        # author block: top = max(source y, author top + est author height)
        auth_top = (fm_auth.get("y0") if fm_auth else None)
        aff_top = fm_aff.get("y0")
        if auth_top is not None:
            text = " ".join(author_html)
            em = sum(1.0 if "\u4e00" <= c <= "\u9fff" else 0.55 for c in
                     re.sub(r"<[^>]+>", "", text))
            lines = max(1, int(em / max(cf_w / ty["author_font_size"], 1.0)) + 1)
            est_bottom = auth_top + lines * ty["author_line_height"]
            aff_top = max(aff_top or 0.0, est_bottom + 6.0)
        parts.append(
            '<div class="affiliation-block" style="position:absolute;'
            'left:%.3fpt;top:%.3fpt;width:%.3fpt;">%s</div>'
            % (cf["x0"], aff_top, cf_w, " ".join(aff_html)))

    # ---- AbstractBlock: heading + body in the left track, body keeps the
    #      source inset (front matter band: left=AbstractBlock) ----
    ab_ids = [pid for pid, role in roles.items()
              if role == "abstract_heading"]
    if ab_ids:
        pid = ab_ids[0]
        top = para_bbox(pid)[1]
        parts.append(block(pid, "abstract-heading", track0["x0"], top,
                           track0_w, "text-align:center;"))
    body_ids = [pid for pid, role in roles.items()
                if role == "abstract_body"]
    if body_ids:
        pid = body_ids[0]
        bb = para_bbox(pid)
        inset_l = max(bb[0] - track0["x0"], 0.0)
        inset_r = max(track0["x1"] - bb[2], 0.0)
        left = track0["x0"] + inset_l
        width = max(track0_w - inset_l - inset_r, 1.0)
        fam = ("font-family:%s;" % body_family) if body_family else ""
        parts.append(block(pid, "abstract-body", left, bb[1], width,
                           "font-size:%.2fpt;line-height:%.2fpt;%s"
                           % (ty["body_font_size"], ty["body_line_height"], fam)))

    # ---- CaptionBlock: bound to the figure (same x, figure width) ----
    cap_ids = [pid for pid, role in roles.items() if role == "caption"]
    if cap_ids and fm:
        pid = cap_ids[0]
        top = para_bbox(pid)[1]
        fig = (fm.get("figure") or {}).get("bbox")
        if fig:
            left = fig[0]
            width = max(fig[2] - fig[0], 1.0)
        else:
            left = para_bbox(pid)[0]
            width = cf_w
        parts.append(block(pid, "caption-block", left, top, width))

    # ---- FootnoteBlock: page-bottom band, footnote typography ----
    fn_ids = sorted([pid for pid, role in roles.items()
                     if role == "footnote"], key=lambda p: para_bbox(p)[1])
    if fn_ids:
        reserved = next((r for r in (bottom_reserved_regions or [])
                         if r.get("owner") == "footnote"), None)
        for pid in fn_ids:
            bb = para_bbox(pid)
            top = (float(reserved["y0"]) if reserved else
                   max(bb[1], page_h * 0.72))
            parts.append(block(pid, "footnote-block", cf["x0"], top, cf_w,
                               "font-size:%.2fpt;line-height:%.2fpt;"
                               % (ty["footnote_font_size"],
                                  ty["footnote_line_height"])))
    return parts


def build_unified_html(page_model, translations, pdf, out_dir, *,
                       table_model=None, flows=None, grid=None,
                       frontmatter=None, typography=None,
                       bottom_reserved_regions=None, resolver=None,
                       gap_collector=None):
    """Return the unified page HTML (str) for one page.

    ``translations``: {paragraph_id: zh} (inline placeholders already
    preserved in the zh text).
    ``flows``: list of ColumnFlowModel from flow_layout.build_column_flow
    (used for the paragraph flow_y).
    ``grid``: optional PageLayoutGrid (Phase 4D.1) -- column tracks.
    ``frontmatter``: optional FrontMatterModel for the page -- when
    present, the front-matter paragraphs render as dedicated blocks
    (TitleBlock / AuthorBlock / AffiliationBlock / AbstractBlock /
    CaptionBlock / FootnoteBlock) instead of ordinary paragraph flow.
    ``typography``: optional Typography token dict.
    ``resolver``: optional TypographyResolver (Phase 4D.2B).  When the token
    dict carries a ``_profile``, a resolver is derived automatically; it only
    changes the TEXT layer (font family / heading hierarchy / list indent /
    script spacing / formula surrounding gap) -- table / figure / formula
    geometry and the column flow stay frozen.
    ``gap_collector``: optional list accumulating ``(text, gaps)`` for the
    ScriptBoundaryQA report.
    """
    from typography import build_typography, typography_css
    page_w = page_model["width"]
    page_h = page_model["height"]
    page_idx = page_model["page"] - 1
    cjk = detect_cjk_serif()
    table_cjk = cjk["chosen"] or "SimSun"
    ty = typography or build_typography()
    if resolver is None and isinstance(ty, dict) and ty.get("_profile") is not None:
        from typography_resolver import TypographyResolver
        resolver = TypographyResolver(ty["_profile"])
    balanced = resolver is not None
    if balanced:
        body_family = resolver.body_font_family()
        font_face = resolver.font_face_css()
        body_size = ty.get("body_font_size") or resolver.profile.body_font_size_pt
    else:
        body_family = "%s,serif" % table_cjk
        font_face = ""
        body_size = ty.get("body_font_size") or 10.8
    parts = []

    # ---------- page-level SVG asset (crop source for formulas/figures) ---
    full_svg = None
    formula_regions = [r for r in page_model["regions"] if r["type"] == "formula"]
    figure_regions = [r for r in page_model["regions"] if r["type"] == "figure"]
    if formula_regions or figure_regions:
        full_svg = page_svg(pdf, page_idx, text_as_path=True)
        svg_name = "page%03d_full.svg" % page_model["page"]
        (out_dir / svg_name).write_text(full_svg, encoding="utf-8")

    inline_map = _inline_formula_map(page_model)

    # ---------- display formula regions: flow_locked (Phase 4C.2R) --------
    # Internal geometry unchanged; container y comes from ColumnFlow.  Each
    # RenderSegment gets its OWN cropped SVG (crop_svg: same glyph paths,
    # viewBox = segment bbox, geometry 1:1 in PDF pt).  The <img> is placed
    # at the flowed position with NO clip-path / overflow / transform -- the
    # Chromium print clipping instabilities (multiple whole-page SVG <img>
    # losing clip-path / overflow:hidden) are avoided entirely.
    formula_flow = {}   # formula_id -> {flow_y, row_members, ...}
    if flows:
        for flow in flows:
            for item in flow.get("items", []):
                if item.get("kind") != "formula":
                    continue
                for m in item.get("row_members", []):
                    formula_flow[m["formula_id"]] = {
                        "flow_y": item["flow_y"],
                        "anchor_y": item.get("anchor_y", item["flow_y"]),
                        "bbox": item["bbox"],
                        "member": m,
                    }
    for r in formula_regions:
        fm = r["payload"]
        if fm.get("placement") == "inline":
            continue
        ff = formula_flow.get(fm["formula_id"])
        row_anchor = (ff["anchor_y"] if ff else
                      (fm.get("layout_bbox") or [0, 0, 0, 0])[1])
        for seg in fm.get("render_segments", []):
            b = seg["render_viewbox"] or seg["layout_bbox"]
            dy = 0.0
            if ff is not None:
                dy = ff["flow_y"] - row_anchor
            w = max(b[2] - b[0], 1.0)
            h = max(b[3] - b[1], 1.0)
            seg_svg_name = "formula_%s_%s.svg" % (fm["formula_id"],
                                                  seg["segment_id"])
            seg_svg = crop_svg(full_svg, b)
            (out_dir / seg_svg_name).write_text(seg_svg, encoding="utf-8")
            parts.append(
                '<img class="formula-seg" data-formula="%s" data-segment="%s" '
                'data-layout="%.3f,%.3f,%.3f,%.3f" '
                'src="%s" style="position:absolute;left:%.3fpt;top:%.3fpt;'
                'width:%.3fpt;height:%.3fpt;"/>'
                % (fm["formula_id"], seg["segment_id"],
                   b[0], b[1] + dy, b[2], b[3] + dy,
                   seg_svg_name, b[0], b[1] + dy, w, h))

    # ---------- table region: existing locked renderer parts ----------
    table_models = (table_model if isinstance(table_model, list)
                    else ([table_model] if table_model is not None else []))
    for one_table in table_models:
        cells, rules = _extract_table_parts(one_table, page_w, page_h,
                                            table_cjk)
        parts.extend(rules)
        parts.extend(cells)

    # ---------- text regions: ONE .paragraph-block per paragraph ----------
    # Render physical FlowFragments.  A cross-column LogicalParagraph still
    # has exactly one translation entry but may have two DOM fragments.
    flow_items = []
    for flow in flows or []:
        for it in flow["items"]:
            if it["kind"] == "paragraph":
                flow_items.append((flow, it))

    para_regions = [r for r in page_model["regions"] if r["type"] == "text"]
    para_by_id = {r["payload"]["paragraph_id"]: r for r in para_regions}

    # Phase 4D.1: front-matter paragraphs render as dedicated blocks; they
    # are skipped in the ordinary paragraph flow (the flow still carries
    # their y for cursor continuity -- only the HTML output changes).
    fm_roles = _assign_frontmatter_paras(frontmatter, para_regions) \
        if frontmatter else {}
    fm_skip = set(fm_roles.keys())
    fm_parts = []
    if frontmatter and grid:
        fm_parts = _render_frontmatter_blocks(
            para_by_id, fm_roles, translations, grid, frontmatter, ty,
            inline_map, svg_name, page_w, page_h, out_dir, full_svg,
            bottom_reserved_regions=bottom_reserved_regions,
            latin_gap=ty.get("cjk_latin_gap_em", 0.0) if balanced else 0.0,
            number_gap=ty.get("cjk_number_gap_em", 0.0) if balanced else 0.0,
            inline_gap_l=ty.get("inline_formula_left_em", 0.0) if balanced else 0.0,
            inline_gap_r=ty.get("inline_formula_right_em", 0.0) if balanced else 0.0,
            gap_collector=gap_collector,
            body_family=body_family if balanced else None)

    if not flow_items:
        for r in para_regions:
            p = r["payload"]
            flow_items.append((
                {"col_x0": p.get("col_x0", r["bbox"][0]),
                 "col_x1": p.get("col_x1", r["bbox"][2])},
                {"paragraph_id": p["paragraph_id"],
                 "flow_fragment_id": p["paragraph_id"] + "-F0",
                 "fragment_index": 0, "continuation": False,
                 "flow_y": p.get("anchor_y", r["bbox"][1]),
                 "render_text": translations.get(p["paragraph_id"], p["source_text"])}))
    for flow, fl in flow_items:
        if fl["paragraph_id"] in fm_skip:
            continue  # front-matter block renders separately
        r = para_by_id[fl["paragraph_id"]]
        para = r["payload"]
        pid = para["paragraph_id"]
        zh = fl.get("render_text") or translations.get(pid, para.get("source_text", ""))
        if not zh.strip():
            continue
        top = fl.get("flow_y", para.get("anchor_y", r["bbox"][1]))
        left = flow.get("col_x0", para.get("col_x0", r["bbox"][0]))
        width = max(flow.get("col_x1", left + para.get("col_width", 1.0)) - left, 1.0)
        fsize = fl.get("base_font_size") or para.get("base_font_size") or 10.0
        line_height_scale = fl.get("line_height_scale", 1.0)
        role = para.get("style_role", "body")

        # Phase 4D.2B: balanced per-role typography (heading hierarchy, list
        # hanging indent, uniform body size) applied to the TEXT layer only --
        # the flow box / column geometry stay frozen.
        weight = 400
        line_height = fsize * LINE_HEIGHT * line_height_scale
        weight_css = ""
        list_indent = ""
        font_audit = ""
        typo_role = role
        heading_level = None
        if balanced:
            typo_role, heading_level = resolver.paragraph_role(para)
            if typo_role in ("body", "body_bold_lead", "abstract_body",
                             "reference", "list_item"):
                # Body / list text keeps its per-paragraph SOURCE size: the
                # balanced profile's body size is the document MEDIAN, not a
                # per-paragraph override.  Forcing a uniform size widens some
                # paragraphs (e.g. 10.909 -> 11.0175) and overflows the frozen
                # column into the gutter.  Only the font family + line-height
                # rhythm change for these roles.
                fsize = fl.get("base_font_size") or para.get("base_font_size") or body_size
                # visual-v01 (fixed-canvas): the visual route may ship a
                # region-local line-height_scale from the LocalFitStrategy.
                # Default 1.0 keeps the shared-base behaviour byte-identical
                # when no visual flows are supplied.
                lh_scale = float(fl.get("line_height_scale") or 1.0)
                line_height = fsize * resolver.profile.body_line_height_ratio \
                    * lh_scale
                weight = 400
            else:
                tk = resolver.tokens_for(typo_role, heading_level, body_size)
                fsize = tk["font_size"]
                line_height = tk["line_height"]
                if tk["font_weight"] != 400:
                    weight = tk["font_weight"]
                    weight_css = "font-weight:%d;" % weight
            if typo_role == "list_item" and ty.get("list_hanging_indent_em"):
                ind = ty["list_hanging_indent_em"]
                list_indent = ("padding-left:%.3fem;text-indent:-%.3fem;"
                               % (ind, ind))
            font_audit = ('data-typo-role="%s" data-heading-level="%s" '
                          'data-requested-font="%s" data-resolved-font="%s" '
                          % (typo_role or "", heading_level or "",
                             resolver.requested_cjk, resolver.resolved_cjk))
        else:
            # headings slightly larger, keep the same flow box
            if role == "heading" and fsize < 14:
                fsize = min(fsize * 1.15, 14.0)

        if fl.get("continuation"):
            zh = re.sub(r"^\s*[•·∙▪●]\s*", "", zh)

        lgap = ty.get("cjk_latin_gap_em", 0.0) if balanced else 0.0
        ngap = ty.get("cjk_number_gap_em", 0.0) if balanced else 0.0
        igl = ty.get("inline_formula_left_em", 0.0) if balanced else 0.0
        igr = ty.get("inline_formula_right_em", 0.0) if balanced else 0.0
        inner = _render_paragraph_text(zh, para, inline_map,
                                       svg_name if (formula_regions or figure_regions) else "",
                                       page_w, page_h, fsize,
                                       out_dir, full_svg,
                                       latin_gap=lgap, number_gap=ngap,
                                       inline_gap_l=igl, inline_gap_r=igr,
                                       gap_collector=gap_collector)
        # vertical (rotated) source text, e.g. the arXiv sidebar header on
        # p001: keep it vertical so it never reflows into a narrow column
        vertical_style = ""
        if fl.get("is_vertical"):
            vertical_style = ("writing-mode:vertical-rl;"
                              "white-space:nowrap;overflow-wrap:normal;"
                              "word-break:keep-all;")
        family = body_family if balanced else ("%s,serif" % table_cjk)
        parts.append(
            '<div class="paragraph-block" data-para="%s" data-flow-fragment="%s" '
            'data-fragment-index="%d" data-continuation="%s" data-role="%s" '
            '%s'
            'style="position:absolute;left:%.3fpt;top:%.3fpt;'
            'width:%.3fpt;white-space:normal;overflow:visible;'
            'overflow-wrap:anywhere;word-break:normal;hyphens:none;line-break:auto;'
            '%s%s%s'
            'font-family:%s;font-size:%.3fpt;'
            'line-height:%.3fpt;color:#000;">%s</div>'
            % (pid, fl.get("flow_fragment_id", pid + "-F0"),
               int(fl.get("fragment_index", 0)), str(bool(fl.get("continuation"))).lower(),
               role, font_audit, left, top, width, vertical_style,
               list_indent, weight_css, family, fsize, line_height, inner))

    # ---------- FigureRegion: own cropped SVG (Phase 4C.2R) ---------------
    # No clip-path / overflow:hidden / transform -- Chromium print loses
    # those on multiple whole-page SVG <img> elements.  A cropped SVG with
    # viewBox = figure bbox keeps the original vector geometry 1:1.
    for r in figure_regions:
        bbox = r["bbox"]
        fig_name = "figure_%s.svg" % r["region_id"]
        fig_svg = crop_svg(full_svg, bbox)
        (out_dir / fig_name).write_text(fig_svg, encoding="utf-8")
        parts.append(
            '<span class="figure-region" data-region="%s" data-figure="%s" '
            'style="position:absolute;left:%.3fpt;top:%.3fpt;'
            'width:%.3fpt;height:%.3fpt;">'
            '<img src="%s" style="width:%.3fpt;height:%.3fpt;max-width:none;"/></span>'
            % (r["region_id"], r["payload"].get("figure_id", r["region_id"]),
               bbox[0], bbox[1], bbox[2] - bbox[0], bbox[3] - bbox[1],
               fig_name, bbox[2] - bbox[0], bbox[3] - bbox[1]))

    # ---------- raster image regions ----------
    for r in page_model["regions"]:
        if r["type"] != "image":
            continue
        bbox = r["bbox"]
        png = out_dir / ("img_p%03d_%s.png"
                         % (page_model["page"], r["region_id"]))
        doc = pymupdf.open(pdf)
        pix = doc[page_idx].get_pixmap(matrix=pymupdf.Matrix(3, 3),
                                       clip=pymupdf.Rect(*bbox))
        pix.save(str(png))
        doc.close()
        parts.append(
            '<img class="figure-img" data-region="%s" src="%s" '
            'style="position:absolute;left:%.3fpt;top:%.3fpt;'
            'width:%.3fpt;height:%.3fpt;"/>'
            % (r["region_id"], png.name, bbox[0], bbox[1],
               bbox[2] - bbox[0], bbox[3] - bbox[1]))

    body = "".join(parts) + "".join(fm_parts)
    # Phase 4D.2B: balanced CJK @font-face (pinned file, never silent), a
    # script-gap base rule, and the front-matter CSS resolved against the
    # balanced body family (table keeps its own SimSun stack).
    doc_html = (
        "<!doctype html><html lang=\"zh-CN\"><head><meta charset=\"utf-8\">"
        "<style>"
        "@page{size:%.3fpt %.3fpt;margin:0;}"
        "*{margin:0;padding:0;box-sizing:border-box;}"
        "html,body{width:%.3fpt;height:%.3fpt;overflow:hidden;}"
        ".paragraph-block{overflow:visible;}"
        ".abstract-body{white-space:normal;overflow-wrap:anywhere;"
        "word-break:normal;hyphens:none;}"
        ".code-run{font-family:Consolas,'Courier New',monospace;white-space:nowrap;"
        "word-break:keep-all;overflow-wrap:normal;font-style:normal;}"
        ".protected-token{white-space:nowrap;word-break:keep-all;"
        "overflow-wrap:normal;}"
        ".inline-bold{font-weight:700}.inline-italic{font-style:italic}"
        ".script-gap{display:inline-block;}"
        ".paragraph-block[data-role='heading']{font-weight:700;}"
        "%s"
        "%s"
        "</style></head><body>" % (page_w, page_h, page_w, page_h,
                                   font_face,
                                   typography_css(ty, body_family if balanced
                                                  else table_cjk))
        + body + "</body></html>"
    )
    return doc_html
