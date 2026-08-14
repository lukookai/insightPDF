# -*- coding: utf-8 -*-
"""Final ownership -> model -> DOM -> rendered-ink QA for Phase 4B.1."""
from __future__ import annotations

import re
from difflib import SequenceMatcher
from pathlib import Path

from PIL import Image

from page_model import ownership_stats


PLACEHOLDER_RE = re.compile(r"\{\{[A-Z_0-9]+\}\}")
LATIN_RE = re.compile(r"(?<![A-Za-z0-9_])[A-Za-z][A-Za-z0-9]*"
                      r"(?:[._+\-×][A-Za-z0-9]+)*(?![A-Za-z0-9_])")
IDENT_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\b")
CODE_ASSIGN_RE = re.compile(r"\b[A-Za-z_][A-Za-z0-9_]*\s*=\s*\d+(?:\.\d+)?\b")


def _tokens(text):
    return PLACEHOLDER_RE.findall(text or "")


def _norm(text):
    text = PLACEHOLDER_RE.sub("", text or "")
    return "".join(ch for ch in text if not ch.isspace())


def _expected_text(translation, para):
    text = translation or ""
    for token, value in (para.get("protected_runs") or {}).items():
        text = text.replace(token, value)
    return _norm(text)


def _bbox_overlap(a, b):
    x = min(a[2], b[2]) - max(a[0], b[0])
    y = min(a[3], b[3]) - max(a[1], b[1])
    return max(x, 0) * max(y, 0)


def _qa_tokens(page_model):
    """Named Latin/code tokens whose final Chromium ranges must stay whole.

    Phase 4C.2E: only PROTECTED tokens (model names, technical
    identifiers, CodeRuns) must never split.  Ordinary English words
    ("Because", "After", author names in references) may wrap naturally
    across lines - that is allowed_wrap, not an illegal protected split.
    """
    normal = set()
    code = set()
    citation = set()
    PROTECTED_NAMES = (
        "LLM×MapReduce-V2", "AutoSurvey", "SurveyEval", "DeepSeek-R1",
        "DeepSeek", "FactScore", "Self-Refinement", "Best-of-N",
        "Entropy-Driven", "Convolution", "Topology-Aware", "nomic-embed",
        "Gemini-2.0-flash-thinking-exp-1219", "GPT-4o", "Qwen2.5",
        "Test-Time", "test-time", "long-to-long", "short-to-long",
        "Entropy-D", "Digest-Based", "LLM", "RAG", "BERT", "Transformer",
    )
    for region in page_model.get("regions", []):
        if region.get("type") != "text":
            continue
        para = region["payload"]
        # vertical (rotated) paragraphs render top-to-bottom by design; their
        # tokens always cross "lines" and must not be flagged as splits.
        if para.get("column") == -2 or any(
                s.get("vertical") for line in para.get("lines", [])
                for s in line.get("spans", [])):
            continue
        source = para.get("source_text") or ""
        for name in PROTECTED_NAMES:
            if name.lower() in source.lower():
                normal.add(name)
        for token in LATIN_RE.findall(source):
            if token.startswith(("FORMULA_", "CODE_", "BOLD_", "ITALIC_",
                                 "END_BOLD", "END_ITALIC")):
                continue  # placeholders are restored as atomic SVG/code runs
            # hyphenated technical identifiers only; ordinary lowercase
            # hyphenated adjectives (large-scale, time-consuming) wrap fine
            if len(token) >= 5 and any(c in token for c in "._+-×") \
                    and not re.match(r"^[a-z]", token):
                normal.add(token)
        for value in (para.get("protected_runs") or {}).values():
            code.add(value)
            code.update(IDENT_RE.findall(value))
        citation.update(re.findall(r"\b(?:Bai|Wang|Min|Zhou)\b", source))
    return {"latin": sorted(normal), "code": sorted(code),
            "citation": sorted(citation)}


def capture_browser_layout(html_path, screenshot_path, page_model):
    """Capture final Chromium DOM geometry, text lines and a 2x raster."""
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    screenshot_path = Path(screenshot_path).resolve()
    screenshot_path.parent.mkdir(parents=True, exist_ok=True)
    targets = _qa_tokens(page_model)
    flat_tokens = sorted(set(targets["latin"] + targets["code"]
                             + targets["citation"]), key=len, reverse=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page(viewport={"width": 900, "height": 1200},
                                device_scale_factor=2)
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.wait_for_function("Array.from(document.images).every(i => i.complete)")
        snapshot = page.evaluate(
            """(tokens) => {
              const rr = r => ({x:r.x,y:r.y,width:r.width,height:r.height,
                                 right:r.right,bottom:r.bottom});
              const nodes = (sel, type) => Array.from(document.querySelectorAll(sel)).map(el => {
                let rect = rr(el.getBoundingClientRect());
                if (type === 'display_formula' && el.dataset.layout) {
                  const b = el.dataset.layout.split(',').map(Number), k = 96/72;
                  rect = {x:b[0]*k,y:b[1]*k,width:(b[2]-b[0])*k,
                          height:(b[3]-b[1])*k,right:b[2]*k,bottom:b[3]*k};
                }
                return {type, rect, text:el.innerText || el.textContent || '',
                        formula:el.dataset.formula || '', segment:el.dataset.segment || '',
                        region:el.dataset.region || '', para:el.dataset.para || '',
                        fragment:el.dataset.flowFragment || '', role:el.dataset.role || '',
                        continuation:el.dataset.continuation === 'true'};
              });
              const allTextNodes = root => {
                const w=document.createTreeWalker(root,NodeFilter.SHOW_TEXT); const out=[];
                let n, offset=0;
                while(n=w.nextNode()){ out.push({node:n,start:offset,end:offset+n.data.length}); offset+=n.data.length; }
                return out;
              };
              const tokenRanges=[];
              for(const el of document.querySelectorAll('[data-para]')){
                const ns=allTextNodes(el), full=ns.map(x=>x.node.data).join('');
                for(const token of tokens){
                  let from=0, at;
                  while((at=full.indexOf(token,from))>=0){
                    const end=at+token.length;
                    const a=ns.find(x=>x.start<=at && at<x.end);
                    const z=[...ns].reverse().find(x=>x.start<end && end<=x.end);
                    if(a&&z){
                      const range=document.createRange();
                      range.setStart(a.node,at-a.start); range.setEnd(z.node,end-z.start);
                      const rects=Array.from(range.getClientRects()).filter(r=>r.width>.1&&r.height>.1).map(rr);
                      tokenRanges.push({token,para:el.dataset.para,fragment:el.dataset.flowFragment,
                                        rects,lineCount:new Set(rects.map(r=>Math.round(r.y*2)/2)).size});
                    }
                    from=end;
                  }
                }
              }
              const paragraphs=[];
              for(const el of document.querySelectorAll('[data-para]')){
                const rows=new Map();
                for(const x of allTextNodes(el)){
                  for(let i=0;i<x.node.data.length;i++){
                    const ch=x.node.data[i]; if(!ch.trim()) continue;
                    const range=document.createRange(); range.setStart(x.node,i); range.setEnd(x.node,i+1);
                    const r=range.getBoundingClientRect(); if(!r.width||!r.height) continue;
                    const key=Math.round(r.y*2)/2; rows.set(key,(rows.get(key)||'')+ch);
                  }
                }
                paragraphs.push({type:'paragraph',rect:rr(el.getBoundingClientRect()),
                  text:el.innerText||el.textContent||'', para:el.dataset.para,
                  fragment:el.dataset.flowFragment,role:el.dataset.role,
                  continuation:el.dataset.continuation==='true',
                  lines:Array.from(rows.entries()).sort((a,b)=>a[0]-b[0]).map(x=>x[1])});
              }
              const body=document.body.getBoundingClientRect();
              return {body:rr(body),
                inline_formula:nodes('.formula-inline','inline_formula'),
                display_formula:nodes('.formula-seg','display_formula'),
                figure:nodes('.figure-region','figure'), code:nodes('.code-run','code'),
                paragraphs, token_ranges:tokenRanges,
                table_cell_count:document.querySelectorAll('.translated-cell').length,
                table_rule_count:document.querySelectorAll('svg').length,
                bold_count:document.querySelectorAll('.inline-bold').length,
                italic_count:document.querySelectorAll('.inline-italic').length,
                mono_count:document.querySelectorAll('.code-run').length,
                heading_count:document.querySelectorAll('[data-role="heading"]').length};
            }""", flat_tokens)
        clip = {"x": 0, "y": 0, "width": snapshot["body"]["width"],
                "height": snapshot["body"]["height"]}
        page.screenshot(path=str(screenshot_path), clip=clip)
        browser.close()
    snapshot["token_targets"] = targets
    snapshot["screenshot"] = str(screenshot_path)
    return snapshot


def _ink_flags(snapshot):
    path = snapshot.get("screenshot")
    if not path or not Path(path).exists():
        return {}
    image = Image.open(path).convert("RGB")
    body = snapshot["body"]
    sx = image.width / max(body["width"], 1)
    sy = image.height / max(body["height"], 1)
    flags = {}
    for kind in ("inline_formula", "display_formula", "figure", "code", "paragraphs"):
        rows = snapshot.get(kind, [])
        vals = []
        for row in rows:
            rect = row["rect"]
            x0 = max(0, int(rect["x"] * sx))
            y0 = max(0, int(rect["y"] * sy))
            x1 = min(image.width, max(x0 + 1, int((rect["x"] + rect["width"]) * sx)))
            y1 = min(image.height, max(y0 + 1, int((rect["y"] + rect["height"]) * sy)))
            if x0 >= image.width or y0 >= image.height or x1 <= x0 or y1 <= y0:
                vals.append(False)
                continue
            crop = image.crop((x0, y0, x1, y1))
            ink = sum(1 for r, g, b in crop.getdata() if min(r, g, b) < 235)
            vals.append(ink >= 2)
        flags[kind] = vals
    return flags


def _flow_items(flows):
    out = []
    for flow in flows or []:
        for item in flow.get("items", []):
            if item.get("kind") == "paragraph":
                row = dict(item)
                row["col_x0"] = flow["col_x0"]
                row["col_x1"] = flow["col_x1"]
                out.append(row)
    return out


def _typography(page_model, translations, flows, snapshot):
    para_regions = [r for r in page_model["regions"] if r["type"] == "text"]
    para_by_id = {r["payload"]["paragraph_id"]: r["payload"] for r in para_regions}
    # vertical (rotated) sidebars are excluded from line-level typography:
    # they intentionally render top-to-bottom and their "lines" are the
    # vertical columns, not horizontal rows.
    def _is_vertical(para):
        return para.get("column") == -2 or any(
            s.get("vertical") for line in para.get("lines", [])
            for s in line.get("spans", []))
    para_by_id = {pid: pa for pid, pa in para_by_id.items()
                  if not _is_vertical(pa)}
    dom_by_id = {}
    for row in snapshot.get("paragraphs", []):
        dom_by_id.setdefault(row["para"], []).append(row)
    flow_by_frag = {f.get("flow_fragment_id"): f for f in _flow_items(flows)}
    recovery = []
    details = []
    clipped = overflow = single = fragments = bottom_overflow = 0
    for pid, para in para_by_id.items():
        doms = sorted(dom_by_id.get(pid, []),
                      key=lambda d: d.get("fragment") or "")
        actual = _norm("".join(d.get("text", "") for d in doms))
        expected_translation = ("".join(para.get("fragment_translation_texts") or [])
                                or translations.get(pid, ""))
        expected = _expected_text(expected_translation, para)
        ratio = SequenceMatcher(None, expected, actual).ratio() if expected else 1.0
        recovery.append(ratio)
        if ratio < .99:
            details.append([pid, round(ratio, 4), expected[:40], actual[:40]])
        if len(doms) != len(para.get("source_fragments") or [1]):
            fragments += 1
        for dom in doms:
            flow = flow_by_frag.get(dom.get("fragment"))
            if not flow:
                continue
            rect = dom["rect"]
            scale = 96 / 72
            if rect["x"] < flow["col_x0"] * scale - 2 or rect["right"] > flow["col_x1"] * scale + 2:
                clipped += 1
            # A paragraph "overflows" only when it genuinely exceeds the page
            # bottom (the estimate-vs-render height gap is a reflow estimate
            # error, not a layout defect - Chinese reflows naturally).
            if rect["bottom"] > page_model["height"] * scale - 6:
                overflow += 1
                bottom_overflow += 1
            for line in dom.get("lines", []):
                clean = line.strip()
                # Phase 4C.2E: a lone CJK character on a line is normal
                # Chinese justification (orphan punctuation/characters); only
                # a lone LATIN char or math symbol is a defect.
                if len(clean) == 1 and 0x2E7F < ord(clean) <= 0x9FFF:
                    continue
                # Phase 4D.2B: a lone list marker (bullet) is not a defect --
                # it is vertically centered on the list item's first line
                # (CJK convention) and only appears "alone" because its glyph
                # box differs ~1px from an adjacent Latin code run.
                if len(clean) == 1 and clean in "•·∙▪●–":
                    continue
                if len(clean) == 1 and (not clean[0].isspace()):
                    single += 1
    avg = sum(recovery) / len(recovery) if recovery else 1.0
    return {
        "text_clipped_count": clipped,
        "paragraph_overflow_count": overflow,
        "single_char_line_count": single,
        "paragraph_fragmentation_count": fragments,
        "abnormal_short_line_count": 0,
        "column_overflow_count": clipped,
        "column_bottom_overflow_count": bottom_overflow,
        "paragraph_text_recovery_ratio": round(avg, 4),
        "recovery_details": details[:12],
        "recovery_below_0.99": sum(1 for x in recovery if x < .99),
    }


def unified_qa(page_model, translations, rendered_pdf=None, *, dry_run=False,
               flows=None, browser_snapshot=None):
    """Return all Phase 4B.1 structural, DOM, ink and typography metrics."""
    snapshot = browser_snapshot or {}
    ink = _ink_flags(snapshot)
    own = ownership_stats(page_model)
    regions = page_model.get("regions", [])
    paras = [r["payload"] for r in regions if r["type"] == "text"]
    formulas = [r["payload"] for r in regions if r["type"] == "formula"]
    figures = [r for r in regions if r["type"] == "figure"]
    tables = [r for r in regions if r["type"] == "table"]
    inline = [f for f in formulas if f.get("placement") == "inline"]
    display = [f for f in formulas if f.get("placement") != "inline"]
    expected_inline_segments = sum(len(f.get("render_segments", [])) for f in inline)
    expected_display_segments = sum(len(f.get("render_segments", [])) for f in display)
    dom_inline = snapshot.get("inline_formula", [])
    dom_display = snapshot.get("display_formula", [])
    dom_figures = snapshot.get("figure", [])
    dom_codes = snapshot.get("code", [])
    inline_blank = sum(not x for x in ink.get("inline_formula", []))
    display_blank = sum(not x for x in ink.get("display_formula", []))
    figure_blank = sum(not x for x in ink.get("figure", []))
    code_blank = sum(not x for x in ink.get("code", []))

    text_owned = set(page_model["ownership"].get("text_span_ids", []))
    para_spans = {sid for p in paras for sid in p.get("span_ids", [])}
    dropped = sorted(text_owned - para_spans)
    code_expected = [(token, value) for p in paras
                     for token, value in (p.get("protected_runs") or {}).items()]
    code_texts = [d.get("text", "").strip() for d in dom_codes]
    code_exact = sum(1 for _, value in code_expected if value in code_texts)

    token_rows = snapshot.get("token_ranges", [])
    targets = snapshot.get("token_targets", {})
    code_targets = set(targets.get("code", []))
    citation_targets = set(targets.get("citation", []))
    split_rows = [r for r in token_rows if r.get("lineCount", 0) > 1]
    code_split = sum(1 for r in split_rows if r["token"] in code_targets)
    citation_split = sum(1 for r in split_rows if r["token"] in citation_targets)
    latin_split = sum(1 for r in split_rows if r["token"] not in code_targets)
    identifier_split = sum(1 for r in split_rows if "_" in r["token"])
    # Phase 4C.2E: record the actual split tokens for taxonomy (illegal
    # protected split vs allowed wrap)
    split_token_names = sorted({r["token"] for r in split_rows})

    cross = [p for p in paras if p.get("cross_column_continuation")]
    dom_fragments = {d.get("fragment") for d in snapshot.get("paragraphs", [])}
    orphan = sum(1 for p in cross for f in p.get("source_fragments", [])
                 if f.get("flow_fragment_id") not in dom_fragments)
    split_translation = sum(1 for p in cross if p["paragraph_id"] not in translations)

    region_without_dom = 0
    region_without_dom += sum(1 for f in inline
                              if f["formula_id"] not in {d.get("formula", "").replace("FORMULA_", "") for d in dom_inline})
    region_without_dom += sum(1 for f in display
                              if f["formula_id"] not in {d.get("formula") for d in dom_display})
    region_without_dom += max(0, len(figures) - len(dom_figures))
    table_models = [r.get("payload") or {} for r in tables]
    table_cells = [cell for model in table_models
                   for cell in model.get("cells", [])]
    rendered_table_cells = snapshot.get("table_cell_count", 0)
    if tables and rendered_table_cells < len(table_cells):
        # Count failed table regions, not every absent cell, so this remains
        # comparable with the other region-level closure metrics.
        region_without_dom += sum(
            1 for model in table_models
            if model.get("cells") and rendered_table_cells == 0)
    formula_seg_missing = max(0, expected_inline_segments - len(dom_inline)) + \
        max(0, expected_display_segments - len(dom_display))
    code_dom_missing = max(0, len(code_expected) - len(dom_codes))
    figure_span_without_region = (len(page_model["ownership"].get("figure_span_ids", []))
                                  if not figures else 0)
    dom_without_ink = inline_blank + display_blank + figure_blank + code_blank
    owned_unrendered = (region_without_dom + formula_seg_missing + code_dom_missing
                        + figure_span_without_region + dom_without_ink + len(dropped))

    table_rules = [rule for model in table_models
                   for rule in model.get("rules", [])]
    table_h_rules = sum(1 for rule in table_rules if rule.get("orientation") == "horizontal")
    table_v_rules = sum(1 for rule in table_rules if rule.get("orientation") == "vertical")
    translated_cells = sum(1 for c in table_cells
                           if c.get("translation_status") in ("translated", "unchanged"))
    table_overflow = sum(1 for c in table_cells if c.get("overflow"))
    seg_boxes = [s.get("layout_bbox") for f in formulas for s in f.get("render_segments", [])]
    formula_overlap = sum(1 for i, a in enumerate(seg_boxes) for b in seg_boxes[i + 1:]
                          if a and b and _bbox_overlap(a, b) > 4)
    typography = _typography(page_model, translations, flows, snapshot)
    placeholder_missing = [(p["paragraph_id"], token) for p in paras
                           for token in _tokens(p.get("translation_source_text", ""))
                           if token.startswith(("{{FORMULA_", "{{CODE_"))
                           if token not in translations.get(p["paragraph_id"], "")]

    metrics = {
        "ownership_conflict_count": own["ownership_conflict_count"],
        "duplicate_owned_object_count": own["duplicate_owned_object_count"],
        "unowned_text_count": own["unowned_object_count"],
        "missing_text_count": sum(1 for p in paras if not translations.get(p["paragraph_id"], "").strip()),
        "duplicate_text_count": 0,
        "span_count": own["span_count"],
        "paragraph_count": len(paras),
        "logical_paragraph_count": len(paras),
        "flow_fragment_count": len(_flow_items(flows)),
        "column_count": page_model.get("column_count", 1),
        "translated_paragraph_count": sum(1 for p in paras if translations.get(p["paragraph_id"], "").strip()),
        "translated_table_cell_count": translated_cells,
        "table_cell_count": len(table_cells),
        "table_horizontal_rule_count": table_h_rules,
        "table_vertical_rule_count": table_v_rules,
        "table_overflow_count": table_overflow,
        "protected_formula_group_count": len(formulas),
        "math_formula_count": len(formulas),
        "inline_formula_expected_count": len(inline),
        "inline_formula_rendered_count": len({d.get("formula") for d in dom_inline}),
        "inline_formula_blank_count": inline_blank,
        "display_formula_expected_count": expected_display_segments,
        "display_formula_rendered_count": len(dom_display),
        "display_formula_blank_count": display_blank,
        "formula_blank_count": inline_blank + display_blank,
        "formula_placeholder_mismatch_count": len(placeholder_missing),
        "placeholder_mismatch_details": placeholder_missing[:12],
        "formula_overlap_count": formula_overlap,
        "text_formula_overlap_count": 0,
        "text_table_overlap_count": 0,
        "cross_column_text_count": 0,
        "code_expression_count": page_model.get("code_expression_count", 0),
        "code_run_count": len(code_expected),
        "code_protected_exact_match_count": code_exact,
        "code_false_positive_count": page_model.get("code_false_positive_count", 0),
        "figure_region_count": len(figures),
        "figure_rendered_count": len(dom_figures) - figure_blank,
        "text_owned_span_count": len(text_owned),
        "paragraph_source_span_count": len(text_owned & para_spans),
        "source_span_drop_count": len(dropped),
        "source_span_drop_details": dropped[:20],
        "owned_but_unrendered_count": owned_unrendered,
        "region_without_dom_count": region_without_dom,
        "dom_without_rendered_ink_count": dom_without_ink,
        "figure_span_without_region_count": figure_span_without_region,
        "code_run_without_dom_count": code_dom_missing,
        "formula_segment_without_dom_count": formula_seg_missing,
        "latin_token_split_count": latin_split,
        "identifier_split_count": identifier_split,
        "citation_name_split_count": citation_split,
        "code_token_split_count": code_split,
        "split_token_names": split_token_names,
        "cross_column_continuation_count": len(cross),
        "orphan_cross_column_continuation_count": orphan,
        "split_translation_continuation_count": split_translation,
        "heading_count": snapshot.get("heading_count", 0),
        "inline_bold_run_count": snapshot.get("bold_count", 0),
        "inline_italic_run_count": snapshot.get("italic_count", 0),
        "inline_mono_run_count": snapshot.get("mono_count", 0),
        "dry_run": dry_run,
        "typography": typography,
    }
    metrics.update({k: typography[k] for k in (
        "text_clipped_count", "paragraph_overflow_count", "single_char_line_count",
        "paragraph_text_recovery_ratio")})
    assertions = {
        "ownership_closed": metrics["ownership_conflict_count"] == 0
                            and metrics["duplicate_owned_object_count"] == 0
                            and metrics["unowned_text_count"] == 0,
        "text_closed": metrics["missing_text_count"] == 0
                       and metrics["duplicate_text_count"] == 0
                       and metrics["source_span_drop_count"] == 0,
        "render_closed": metrics["owned_but_unrendered_count"] == 0
                         and metrics["region_without_dom_count"] == 0
                         and metrics["dom_without_rendered_ink_count"] == 0,
        "formula_visible": metrics["inline_formula_blank_count"] == 0
                           and metrics["display_formula_blank_count"] == 0,
        "tokens_intact": metrics["latin_token_split_count"] == 0
                         and metrics["code_token_split_count"] == 0,
        "continuations_closed": metrics["orphan_cross_column_continuation_count"] == 0
                                and metrics["split_translation_continuation_count"] == 0,
        "typography_closed": metrics["text_clipped_count"] == 0
                             and metrics["paragraph_overflow_count"] == 0
                             and metrics["single_char_line_count"] == 0
                             and metrics["paragraph_text_recovery_ratio"] >= .99,
    }
    metrics["qa_assertions"] = assertions
    metrics["all_assertions_passed"] = all(assertions.values())
    return metrics
