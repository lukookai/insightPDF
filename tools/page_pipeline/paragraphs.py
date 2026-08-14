# -*- coding: utf-8 -*-
"""Paragraph reconstruction and semantic-flow closure for Phase 4B.1.

Source lines remain geometry evidence.  They are first reconstructed into
physical column fragments, then joined into LogicalParagraph objects.  A
logical paragraph may own more than one source fragment (the common case is a
paragraph/list item continuing from the bottom of the left column at the top
of the right column), but is translated exactly once.
"""
from __future__ import annotations

import re


FORMULA_RE = re.compile(r"\{\{FORMULA_[A-Z0-9]+\}\}")
SECTION_RE = re.compile(r"^(?:\d+(?:\.\d+)*|[A-Z]\.\d+)\s*\S")
CODE_ASSIGN_RE = re.compile(
    r"\b([A-Za-z_][A-Za-z0-9_]*(?:\s+_[A-Za-z0-9_]+)*)"
    r"\s*=\s*([0-9]+(?:\.[0-9]+)?)\b"
)
LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9]*(?:[._+\-×][A-Za-z0-9]+)+")


def _cy(span):
    box = span["bbox"]
    return (box[1] + box[3]) / 2.0


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _font_style(font):
    low = (font or "").lower()
    if "ital" in low or "obli" in low:
        return "italic"
    if any(h in low for h in ("medi", "bold", "-dem", "-hea")):
        return "bold"
    if "mono" in low or "monl" in low or "courier" in low:
        return "mono"
    return "normal"


def detect_columns(spans, page_w=None, min_gap=14.0):
    """Return stable column bounds.

    The earlier pairwise-gap heuristic could place the p13 cut at x=280 and
    steal the punctuation span ``, in`` from the left column.  Once two body
    columns are detected, their separator is the page centre; this PDF family
    uses a symmetric two-column grid and full-width lines are handled before
    column assignment.
    """
    if not spans:
        return []
    lo = min(s["bbox"][0] for s in spans)
    hi = max(s["bbox"][2] for s in spans)
    if page_w:
        mid = page_w / 2.0
        left = [s for s in spans if (s["bbox"][0] + s["bbox"][2]) / 2 < mid]
        right = [s for s in spans if (s["bbox"][0] + s["bbox"][2]) / 2 > mid]
        if left and right:
            return [(lo, mid), (mid, hi)]
    return [(lo, hi)]


def cluster_lines(spans):
    """Group source spans into visual lines and order them left-to-right."""
    order = sorted(spans, key=lambda s: (_cy(s), s["bbox"][0]))
    lines = []
    for span in order:
        height = span["bbox"][3] - span["bbox"][1]
        tol = max(2.5, height * 0.65)
        match = None
        for line in reversed(lines[-3:]):
            if abs(_cy(span) - line["cy"]) <= max(line["tol"], tol):
                match = line
                break
        if match is None:
            lines.append({"cy": _cy(span), "spans": [span], "tol": tol})
        else:
            match["spans"].append(span)
            n = len(match["spans"])
            match["cy"] = (match["cy"] * (n - 1) + _cy(span)) / n
            match["tol"] = max(match["tol"], tol)
    for line in lines:
        line["spans"].sort(key=lambda s: s["bbox"][0])
    return sorted(lines, key=lambda line: line["cy"])


class SourceTextBuilder:
    """Reconstruct translatable logical source text from source lines.

    It preserves punctuation spans, repairs PDF line-end hyphenation and
    inserts a word boundary between ordinary Latin/number tokens on adjacent
    lines.  Formula placeholders are treated as atomic tokens.
    """

    def __init__(self, formula_by_span_id=None):
        self.formula_by_span_id = formula_by_span_id or {}

    @staticmethod
    def _needs_space(prev, cur, gap):
        if not prev or not cur or prev[-1].isspace() or cur[0].isspace():
            return False
        if prev.endswith(("(", "[", "{", "/")):
            return False
        if cur.startswith((")", "]", "}", ",", ".", ":", ";", "?", "!", "%")):
            return False
        if FORMULA_RE.fullmatch(prev) or FORMULA_RE.fullmatch(cur):
            return gap > 1.0
        if prev[-1].isalnum() and cur[0].isalnum():
            return gap > 0.35
        if prev[-1] in ",.;:!?" and cur[0].isalnum():
            return gap > 0.35
        return False

    def line_text(self, line):
        parts = []
        last_token = None
        last_box = None
        for span in line.get("spans", []):
            token = self.formula_by_span_id.get(span["id"])
            if token and token == last_token:
                last_box = span["bbox"]
                continue
            text = token or span.get("text", "")
            if not text:
                continue
            if parts and last_box is not None:
                gap = span["bbox"][0] - last_box[2]
                if self._needs_space(parts[-1], text, gap):
                    parts.append(" ")
            parts.append(text)
            last_token = token
            last_box = span["bbox"]
        return "".join(parts).strip()

    @staticmethod
    def join_text(left, right):
        left = (left or "").rstrip()
        right = (right or "").lstrip()
        if not left:
            return right
        if not right:
            return left
        # Real discretionary line-end hyphenation: em- + bedding -> embedding.
        if (re.search(r"[A-Za-z]{2,}-$", left)
                and re.match(r"^[a-z]", right)
                and not re.search(r"[A-Za-z0-9_]+-$", left.split()[-1]) is None):
            token = left.split()[-1]
            next_token = right.split()[0]
            if ("_" not in token and "-" not in next_token
                    and not token.lower().startswith("nomic-")
                    and not token[:-1].isupper()):
                return left[:-1] + right
        if (left[-1].isalnum() or left[-1] in ")]}"
                or FORMULA_RE.search(left)):
            return left + " " + right
        return left + right

    def build(self, lines):
        text = ""
        for line in lines:
            text = self.join_text(text, self.line_text(line))
        text = re.sub(r"(\{\{FORMULA_[A-Z0-9]+\}\})(?:\s*\1)+", r"\1", text)
        text = re.sub(r"(?<=[A-Za-z0-9])\s+_(?=[A-Za-z0-9])", "_", text)
        return text.strip()


def _line_bbox(line):
    return _union([s["bbox"] for s in line["spans"]])


def _line_heading(line, builder, col_median_size):
    text = builder.line_text(line).strip()
    spans = line.get("spans", [])
    fonts = [s.get("font") or "" for s in spans if (s.get("text") or "").strip()]
    sizes = [s.get("size") or 0 for s in spans if (s.get("size") or 0) > 0]
    styled = [f for f in fonts if _font_style(f) == "bold"]
    section = bool(SECTION_RE.match(text))
    larger = bool(sizes and col_median_size and max(sizes) >= col_median_size * 1.12)
    # A math symbol in LLM×MapReduce-V2 need not itself use the bold font.
    mostly_bold = bool(fonts and len(styled) >= max(1, len(fonts) - 1))
    return len(text) <= 90 and (section and (mostly_bold or larger) or mostly_bold and larger)


def _line_mostly_bold(line):
    fonts = [s.get("font") or "" for s in line.get("spans", [])
             if (s.get("text") or "").strip() and "CMSY" not in (s.get("font") or "")]
    return bool(fonts and all(_font_style(font) == "bold" for font in fonts))


def merge_paragraphs(lines, builder, col_median_size=0.0):
    """Merge normal source lines, preserving independent heading boundaries."""
    paragraphs = []
    for line in lines:
        bbox = _line_bbox(line)
        is_heading = _line_heading(line, builder, col_median_size)
        if not paragraphs:
            paragraphs.append({"lines": [line], "bbox": bbox,
                               "cy0": line["cy"], "cy1": line["cy"],
                               "line_heading": is_heading})
            continue
        last = paragraphs[-1]
        height = max(bbox[3] - bbox[1], 1.0)
        gap = line["cy"] - last["cy1"]
        # Multi-line numbered headings (notably C.4 + LLM×MapReduce-V2)
        # inherit the heading role on their following all-bold line.
        if last["line_heading"] and _line_mostly_bold(line) and gap < height * 1.8:
            is_heading = True
        boundary = is_heading != last["line_heading"]
        if gap < height * 1.6 and not boundary:
            last["lines"].append(line)
            last["bbox"] = _union([last["bbox"], bbox])
            last["cy1"] = line["cy"]
        else:
            paragraphs.append({"lines": [line], "bbox": bbox,
                               "cy0": line["cy"], "cy1": line["cy"],
                               "line_heading": is_heading})
    return paragraphs


def _span_fonts(para):
    return [s.get("font") or "" for line in para.get("lines", [])
            for s in line.get("spans", []) if s.get("font")]


def _detect_style_role(para, col_median_size):
    src = (para.get("source_text") or "").lstrip()
    fonts = _span_fonts(para)
    sizes = [s.get("size") or 0 for line in para.get("lines", [])
             for s in line.get("spans", []) if (s.get("size") or 0) > 0]
    max_size = max(sizes) if sizes else 0
    if src.startswith(("•", "·", "∙", "-", "*", "–", "▪", "●")):
        return "list_item"
    if re.match(r"^\s*\d{1,2}[.)]\s", src):
        return "list_item"
    if re.match(r"^(Table|Figure|Fig\.?|Tab\.?)\s*\d", src, re.I):
        return "caption"
    if SECTION_RE.match(src) and len(src) <= 90:
        return "heading"
    if col_median_size and max_size >= col_median_size * 1.15 and len(src) <= 90:
        return "heading"
    if len(src) <= 90 and fonts:
        non_symbol = [f for f in fonts if "CMSY" not in f]
        if non_symbol and all(_font_style(f) == "bold" for f in non_symbol):
            return "heading"
    return "body"


def _style_ranges(source_text, lines, builder):
    """Locate styled source-span groups in the reconstructed paragraph."""
    ranges = []
    cursor = 0
    for line in lines:
        groups = []
        current = None
        for span in line.get("spans", []):
            if span.get("is_formula"):
                continue
            style = _font_style(span.get("font"))
            if style == "normal":
                current = None
                continue
            if current and current[0] == style:
                current[1].append(span)
            else:
                current = [style, [span]]
                groups.append(current)
        for style, spans in groups:
            phrase = builder.line_text({"spans": spans, "cy": line["cy"]}).strip()
            if not phrase:
                continue
            # PDF span spacing can differ slightly from reconstructed spacing.
            pattern = re.escape(phrase).replace(r"\ ", r"\s+")
            match = re.search(pattern, source_text[cursor:])
            if match is None:
                match = re.search(pattern, source_text)
                base = 0
            else:
                base = cursor
            if match:
                start, end = base + match.start(), base + match.end()
                ranges.append({"start": start, "end": end, "style": style,
                               "text": source_text[start:end]})
                cursor = end
    return ranges


def _code_ranges(source_text, lines):
    ranges = []
    occupied = set()
    for match in CODE_ASSIGN_RE.finditer(source_text):
        ident = re.sub(r"\s+_", "_", match.group(1))
        exact = "%s = %s" % (ident, match.group(2))
        ranges.append({"start": match.start(), "end": match.end(),
                       "style": "mono", "text": exact,
                       "source_text": match.group(0)})
        occupied.update(range(match.start(), match.end()))
    # Monospace source spans such as nomic-embed-text-v1 are protected even
    # without an equals sign.
    for line in lines:
        for span in line.get("spans", []):
            if _font_style(span.get("font")) != "mono":
                continue
            raw = (span.get("text") or "").strip()
            for token in LATIN_TOKEN_RE.findall(raw):
                start = source_text.find(token)
                if start >= 0 and not any(i in occupied for i in range(start, start + len(token))):
                    ranges.append({"start": start, "end": start + len(token),
                                   "style": "mono", "text": token,
                                   "source_text": token})
                    occupied.update(range(start, start + len(token)))
    return sorted(ranges, key=lambda r: r["start"])


def _translation_markup(source_text, style_ranges, code_ranges):
    edits = []
    protected_runs = {}
    inline_runs = []
    for i, run in enumerate(code_ranges):
        token = "{{CODE_%d}}" % i
        protected_runs[token] = run["text"]
        edits.append((run["start"], run["end"], token))
        inline_runs.append({"text": run["text"], "style": "mono",
                            "translation_policy": "protect", "token": token})
    for i, run in enumerate(style_ranges):
        if any(not (run["end"] <= c["start"] or run["start"] >= c["end"])
               for c in code_ranges):
            continue
        style = run["style"]
        start_token = "{{%s_%d}}" % (style.upper(), i)
        end_token = "{{END_%s_%d}}" % (style.upper(), i)
        edits.append((run["start"], run["end"],
                      start_token + source_text[run["start"]:run["end"]] + end_token))
        inline_runs.append({"text": run["text"], "style": style,
                            "translation_policy": "translate"})
    marked = source_text
    for start, end, replacement in sorted(edits, key=lambda e: e[0], reverse=True):
        marked = marked[:start] + replacement + marked[end:]
    return marked, protected_runs, inline_runs


def _physical_paragraph(para, column, index, cx0, cx1, builder, col_med,
                        full_width=False):
    para["column"] = column
    para["reading_order"] = (column, para["cy0"])
    para["fragment_id"] = ("W%d" % index if column == -1 else "P%d-%d" % (column, index))
    para["col_x0"] = round(cx0, 3)
    para["col_x1"] = round(cx1, 3)
    para["col_width"] = round(max(cx1 - cx0, 1.0), 3)
    para["full_width"] = full_width
    para["anchor_y"] = round(min(s["bbox"][1] for s in para["lines"][0]["spans"]), 3)
    sizes = [s.get("size") or 0 for line in para["lines"]
             for s in line.get("spans", []) if (s.get("size") or 0) > 0]
    para["base_font_size"] = round(sorted(sizes)[len(sizes) // 2], 3) if sizes else 0.0
    para["source_text"] = builder.build(para["lines"])
    para["span_ids"] = [s["id"] for line in para["lines"] for s in line["spans"]]
    para["style_role"] = _detect_style_role(para, col_med)
    return para


def _can_cross_column(left, right, page_h):
    text_left = (left.get("source_text") or "").rstrip()
    text_right = (right.get("source_text") or "").lstrip()
    if not text_left or not text_right:
        return False
    if left.get("column") != 0 or right.get("column") != 1:
        return False
    if left["bbox"][3] < page_h - 120 or right["anchor_y"] > 180:
        return False
    if right.get("style_role") in ("heading", "caption", "list_item"):
        return False
    if re.search(r"[.!?。！？:;]\s*$", text_left):
        return False
    if not re.match(r"^[a-z(]", text_right):
        return False
    return abs((left.get("base_font_size") or 0) - (right.get("base_font_size") or 0)) <= 1.0


def _logicalize(physical, page_h, builder):
    merge_pair = None
    lefts = [p for p in physical if p["column"] == 0]
    rights = [p for p in physical if p["column"] == 1]
    if lefts and rights and _can_cross_column(lefts[-1], rights[0], page_h):
        merge_pair = (lefts[-1]["fragment_id"], rights[0]["fragment_id"])

    logical = []
    consumed = set()
    for para in physical:
        if para["fragment_id"] in consumed:
            continue
        fragments = [para]
        if merge_pair and para["fragment_id"] == merge_pair[0]:
            right = next(p for p in physical if p["fragment_id"] == merge_pair[1])
            fragments.append(right)
            consumed.add(right["fragment_id"])
        source = ""
        for frag in fragments:
            source = builder.join_text(source, frag["source_text"])
        all_lines = [line for frag in fragments for line in frag["lines"]]
        styles = _style_ranges(source, all_lines, builder)
        codes = _code_ranges(source, all_lines)
        marked, protected, inline_runs = _translation_markup(source, styles, codes)
        lp_id = "LP%d" % len(logical)
        source_fragments = []
        for fi, frag in enumerate(fragments):
            source_fragments.append({
                "flow_fragment_id": "%s-F%d" % (lp_id, fi),
                "source_fragment_id": frag["fragment_id"],
                "column": frag["column"], "anchor_y": frag["anchor_y"],
                "bbox": [round(v, 3) for v in frag["bbox"]],
                "col_x0": frag["col_x0"], "col_x1": frag["col_x1"],
                "col_width": frag["col_width"],
                "base_font_size": frag["base_font_size"],
                "source_text": frag["source_text"],
                "span_ids": frag["span_ids"],
                "continuation": fi > 0,
            })
        logical.append({
            "paragraph_id": lp_id,
            "logical_paragraph_id": lp_id,
            "source_fragments": source_fragments,
            "source_text": source,
            "translation_source_text": marked,
            "style_role": para["style_role"],
            "inline_runs": inline_runs,
            "protected_runs": protected,
            "span_ids": [sid for frag in fragments for sid in frag["span_ids"]],
            "lines": all_lines,
            "bbox": _union([frag["bbox"] for frag in fragments]),
            "column": para["column"],
            "reading_order": para["reading_order"],
            "anchor_y": para["anchor_y"],
            "col_x0": para["col_x0"], "col_x1": para["col_x1"],
            "col_width": para["col_width"],
            "base_font_size": para["base_font_size"],
            "full_width": para["full_width"],
            "cross_column_continuation": len(fragments) > 1,
        })
    return logical


def build_paragraphs(spans, page_w, page_h, formula_by_span_id=None):
    """Build LogicalParagraphs while retaining physical FlowFragment input."""
    formula_by_span_id = formula_by_span_id or {}
    builder = SourceTextBuilder(formula_by_span_id)
    # vertical (rotated) spans are separated BEFORE column detection so a
    # tall sidebar span (p001 arXiv header, bbox h=348pt) can never blow up
    # cluster_lines tolerance and swallow body paragraphs.
    vertical_spans = [s for s in spans if s.get("vertical")]
    body_spans = [s for s in spans if not s.get("vertical")]
    spans = body_spans
    cols = detect_columns(spans, page_w=page_w)
    cut = cols[0][1] if len(cols) > 1 else None
    all_lines = cluster_lines(spans)

    def line_xspan(line):
        return (min(s["bbox"][0] for s in line["spans"]),
                max(s["bbox"][2] for s in line["spans"]))

    def full_width(line):
        if cut is None:
            return False
        x0, x1 = line_xspan(line)
        if not (x1 - x0 > page_w * 0.55 and x0 < cut < x1):
            return False
        spans_x = sorted(line["spans"], key=lambda s: s["bbox"][0])
        return all(spans_x[i + 1]["bbox"][0] - spans_x[i]["bbox"][2] <= 8.0
                   for i in range(len(spans_x) - 1))

    full_lines = [line for line in all_lines if full_width(line)]
    full_ids = {id(s) for line in full_lines for s in line["spans"]}
    physical = []
    for i, para in enumerate(merge_paragraphs(full_lines, builder)):
        x0 = min(s["bbox"][0] for line in para["lines"] for s in line["spans"])
        x1 = max(s["bbox"][2] for line in para["lines"] for s in line["spans"])
        physical.append(_physical_paragraph(para, -1, i, x0, x1, builder, 0, True))

    for ci, (cx0, cx1) in enumerate(cols):
        if len(cols) == 1:
            lo, hi = -1e9, 1e9
        else:
            lo = -1e9 if ci == 0 else cut
            hi = cut if ci == 0 else 1e9
        col_spans = [s for s in spans if id(s) not in full_ids
                     and lo <= (s["bbox"][0] + s["bbox"][2]) / 2.0 < hi]
        if not col_spans:
            continue
        sizes = sorted(s.get("size") or 0 for s in col_spans if (s.get("size") or 0) > 0)
        col_med = sizes[len(sizes) // 2] if sizes else 0.0
        lines = cluster_lines(col_spans)
        for i, para in enumerate(merge_paragraphs(lines, builder, col_med)):
            physical.append(_physical_paragraph(para, ci, i, cx0, cx1,
                                                builder, col_med, False))

    # vertical sidebars: one isolated paragraph each (own column == -2 so
    # they never merge with body columns or cross-column continuations)
    for i, vs in enumerate(vertical_spans):
        line = {"cy": _cy(vs), "spans": [vs], "tol": 0.0}
        x0, x1 = vs["bbox"][0], vs["bbox"][2]
        y0, y1 = vs["bbox"][1], vs["bbox"][3]
        para = {"lines": [line], "bbox": [x0, y0, x1, y1],
                "cy0": _cy(vs), "cy1": _cy(vs), "line_heading": False}
        physical.append(_physical_paragraph(para, -2, i, x0, x1, builder,
                                            0, False))

    physical.sort(key=lambda p: (p["reading_order"][0], p["reading_order"][1]))
    return _logicalize(physical, page_h, builder), len(cols)
