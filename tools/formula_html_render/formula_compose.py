# -*- coding: utf-8 -*-
"""FormulaBlock composition for Phase 3E (rev 2).

Pipeline:  Math Candidate -> Context Filter -> Composition -> FormulaModel

Rev 2 fixes (per review):
1. inline/display is decided from the *whole logical line segment* context:
   a math candidate embedded in ordinary text (e.g. "Let X be ...") is inline,
   never display just because it uses a math font / sits alone in its bbox.
2. multi-line display equations are composed via an equation-group layer
   (bracket balance, continuation characters ", = (", row gaps, equation
   number on the last row).
3. large-operator limits (Sigma/Union superscript/subscript) are bound back
   into the operator's FormulaBlock instead of becoming their own blocks.
4. inline expressions continue across spans/lines when the expression is open
   (ends with "=" / "," / operators, unbalanced brackets).
5. title false positives ("LLM x MapReduce-V2" x) are removed using adjacent
   ordinary-text context, not just the math font.
6. contains_fraction is only true for a real fraction topology
   (horizontal bar + content above + content below).
7. FormulaModel gains detection_confidence / composition_confidence.
"""
from __future__ import annotations

import unicodedata
from collections import defaultdict

MATH_FONT_HINTS = ("cmmi", "cmsy", "cmr", "cmex", "msam", "msbm", "mtsy",
                   "eufm", "math", "stix", "xits", "latinmodern math",
                   "asana math", "tex gyre", "nimbus math", "lmsyn")
SMALL_FONT_HINTS = ("cmmi8", "cmsy8", "cmr8", "cmm8", "cmmi7", "cmsy7")
LARGE_OP_FONTS = ("cmex",)
GAP_MERGE_PT = 15.0
COLUMN_GAP_PT = 12.0       # x gap that separates logical line segments
WIDE_MATH_GAP_PT = 40.0    # gap between two candidate runs that splits them
CONTINUATION_END = set("=,+-|({[\u2212\u2265\u2264\u222a\u2229")  # open expression
LARGE_OP_CHARS = set("\u2211\u222b\u220f\u22c3\u22c2\u222a\u2229")


def is_math_font(font):
    low = (font or "").lower()
    return any(h in low for h in MATH_FONT_HINTS)


def is_small_math_font(font):
    low = (font or "").lower()
    return any(h in low for h in SMALL_FONT_HINTS)


def _math_char(ch):
    if not ch or ch.isspace():
        return False
    cat = unicodedata.category(ch[0])
    if cat in ("Sm", "Mn", "Sk", "Zl", "Zp", "Zs", "Co"):
        return True
    if 0x370 <= ord(ch[0]) < 0x400:
        return True
    return "(cid:" in ch


def _has_math_char(text):
    return any(_math_char(c) for c in (text or ""))


def span_is_candidate(span, include_digits=True):
    text = (span.get("text") or "")
    if not text.strip():
        return False
    if is_math_font(span.get("font")):
        return True
    if _has_math_char(text):
        return True
    if include_digits and all(c.isdigit() or c in ".,- \u00b7()[]" for c in text):
        if any(c.isdigit() for c in text):
            return True
    return False


def _is_citation(text):
    t = text.strip()
    return bool(t and t.startswith("[") and t.endswith("]")
                and all(c in "0123456789, -–—;" for c in t[1:-1]))


def re_full_digit_or_paren(text):
    t = (text or "").strip()
    return bool(t) and all(c.isdigit() or c in "(),.- " for c in t) \
        and any(c.isdigit() for c in t)


def _is_page_number(span, page_h):
    text = span.get("text") or ""
    b = span["bbox"]
    if not text.strip().isdigit():
        return False
    return b[1] < 40 or b[3] > page_h - 40


def _center(bbox):
    return (bbox[0] + bbox[2]) / 2.0, (bbox[1] + bbox[3]) / 2.0


def _cy(span):
    b = span["bbox"]
    return (b[1] + b[3]) / 2.0


def _union(boxes):
    return [min(b[0] for b in boxes), min(b[1] for b in boxes),
            max(b[2] for b in boxes), max(b[3] for b in boxes)]


def _run_text(spans):
    return "".join((s.get("text") or "") for s in spans)


def _bracket_balance(spans):
    bal = 0
    for s in spans:
        for c in (s.get("text") or ""):
            if c in "([{":
                bal += 1
            elif c in ")]}":
                bal -= 1
    return bal


def _ends_open(spans):
    t = _run_text(spans).strip()
    return bool(t) and t[-1] in CONTINUATION_END


def _is_eq_number(span, main_size=None):
    """(N) equation-number test.  Digits in a *small* font are subscripts
    (e.g. '12' under C1/C2) and digits set in a math font are formula content
    (e.g. '(1' inside (1-beta)H(D)) - neither is an equation number."""
    t = (span.get("text") or "").strip()
    if not re_full_digit_or_paren(t):
        return False
    if is_math_font(span.get("font")):
        return False
    if main_size:
        size = span.get("size") or 0
        if size and size < main_size * 0.88:
            return False
    return True


# --------------------------------------------------------------------------
# 1. logical line segments
# --------------------------------------------------------------------------
def _logical_segments(spans):
    """Group ALL spans into logical line segments.

    Row grouping by centre-y with a *running average* centre and a tolerance
    based on the running average glyph height (avoids the "first span anchors
    the row" bug where a small-height span tears a row apart).  Each row is
    then split into segments by x gap: > COLUMN_GAP_PT when at least one side
    is ordinary text (two-column layout), or > WIDE_MATH_GAP_PT between two
    math candidates (independent expressions sharing a row).
    """
    order = sorted(spans, key=lambda s: _cy(s))
    rows = []
    for s in order:
        h = s["bbox"][3] - s["bbox"][1]
        tol = max(2.5, h * 0.65)
        if rows and abs(_cy(s) - rows[-1]["cy"]) <= rows[-1]["tol"]:
            rows[-1]["spans"].append(s)
            n = len(rows[-1]["spans"])
            rows[-1]["cy"] = (rows[-1]["cy"] * (n - 1) + _cy(s)) / n
            rows[-1]["tol"] = max(
                rows[-1]["tol"],
                (rows[-1]["tol"] * (n - 1) + tol) / n)
        else:
            rows.append({"cy": _cy(s), "spans": [s], "tol": tol})
    for row in rows:
        row["spans"].sort(key=lambda s: s["bbox"][0])
    segs = []
    for row in rows:
        ss = row["spans"]
        cur = [ss[0]]
        for s in ss[1:]:
            gap = s["bbox"][0] - cur[-1]["bbox"][2]
            both_cand = span_is_candidate(s) and span_is_candidate(cur[-1])
            if gap <= COLUMN_GAP_PT or (both_cand and gap <= WIDE_MATH_GAP_PT):
                cur.append(s)
            else:
                segs.append(_mk_seg(cur))
                cur = [s]
        segs.append(_mk_seg(cur))
    segs.sort(key=lambda g: (g["cy"], g["x0"]))
    return segs


def _mk_seg(spans):
    b = _union([s["bbox"] for s in spans])
    return {"cy": (b[1] + b[3]) / 2.0, "x0": b[0], "y0": b[1],
            "x1": b[2], "y1": b[3], "spans": spans}


def _seg_has_math(seg):
    """A segment counts as a *math* segment only when it contains a real math
    span (math font or math unicode).  Pure-digit candidates do not make a
    segment "math" (so section numbers / years / page numbers get filtered)."""
    return any(is_math_font(s.get("font")) or _has_math_char(s.get("text") or "")
               for s in seg["spans"])


# --------------------------------------------------------------------------
# 2. false-positive filtering (segment context)
# --------------------------------------------------------------------------
def _filter_false_positives(spans, segs, page_h):
    """Return set of span ids kept as candidates.

    Two passes: pass 1 removes bullets / citations / page numbers / title
    symbols (e.g. "LLM x MapReduce-V2" x) / stray operators using the raw
    segment context; pass 2 re-computes the "segment has math" flag from the
    surviving candidates and removes lone digits (section numbers, years)
    from segments that are not real math lines.
    """
    seg_of = {}
    for seg in segs:
        for s in seg["spans"]:
            seg_of[id(s)] = seg

    kept1 = set()
    for i, s in enumerate(spans):
        text = (s.get("text") or "").strip()
        if not text or not span_is_candidate(s):
            continue
        seg = seg_of[id(s)]
        if text in {"\u2022", "\u00b7", "\u2219", "*"} and not is_math_font(s.get("font")):
            continue
        if _is_citation(text):
            continue
        if _is_page_number(s, page_h):
            continue
        # title/section symbol between ordinary text on the same segment
        if text in {"\u00d7", "\u2212", "-", "+", "="} or is_math_font(s.get("font")):
            if _between_plain_text(seg["spans"], s):
                continue
        kept1.add(id(s))

    seg_math = {id(seg) for seg in segs
                if any(id(s) in kept1
                       and (is_math_font(s.get("font"))
                            or _has_math_char(s.get("text") or ""))
                       for s in seg["spans"])}

    kept = set()
    for s in spans:
        if id(s) not in kept1:
            continue
        text = (s.get("text") or "").strip()
        seg = seg_of[id(s)]
        # lone digit(s) in a segment with no real math anywhere
        if (not is_math_font(s.get("font"))
                and all(c.isdigit() or c in ".,- " for c in text)
                and id(seg) not in seg_math):
            continue
        kept.add(id(s))
    return kept


def _between_plain_text(seg_spans, s):
    """s is a short symbol whose immediate left/right neighbours on the same
    segment are both ordinary (non-candidate, same baseline band) text."""
    if not is_math_font(s.get("font")) and (s.get("text") or "").strip() not in {
            "\u00d7", "\u2212"}:
        return False
    sb = s["bbox"]
    left = right = None
    for o in seg_spans:
        if o is s:
            continue
        ob = o["bbox"]
        # same baseline band
        if not (ob[1] < sb[3] - 1 and ob[3] > sb[1] + 1):
            continue
        if ob[2] <= sb[0] + 2.0:
            if left is None or ob[2] > left["bbox"][2]:
                left = o
        if ob[0] >= sb[2] - 2.0:
            if right is None or ob[0] < right["bbox"][0]:
                right = o
    if left is None or right is None:
        return False
    return (not span_is_candidate(left) and not span_is_candidate(right)
            and abs(_cy(left) - _cy(right)) <= 3.0)


# --------------------------------------------------------------------------
# 3. formula component: small ordinary-font span inside a formula
# --------------------------------------------------------------------------
def _main_size(spans):
    sizes = [s.get("size") or 0 for s in spans if s.get("size")]
    return max(sizes) if sizes else 10.0


def _is_component(s, cand_neighbour):
    """Small ordinary-font span (e.g. 'agg' in f_agg) glued to a math span."""
    if span_is_candidate(s):
        return False
    if not cand_neighbour:
        return False
    if not is_math_font(cand_neighbour.get("font")):
        return False
    size = s.get("size") or 0
    main = cand_neighbour.get("size") or 0
    if main and size >= main * 0.88:
        return False
    b, nb = s["bbox"], cand_neighbour["bbox"]
    gap = max(0.0, b[0] - nb[2], nb[0] - b[2])
    if gap > 3.0:
        return False
    return not (b[1] > nb[3] + 2 or b[3] < nb[1] - 2)


# --------------------------------------------------------------------------
# 4. inline merge inside a segment
# --------------------------------------------------------------------------
def _inline_merge(segs, kept, spans):
    """Merge candidate spans inside each segment into blocks.

    Merges on: small x gap + y overlap; expression open (ends with an operator
    / comma / bracket); unbalanced brackets (must continue).
    """
    by_id = {id(s): s for s in spans}
    blocks = []  # {"spans": [...], "bbox": ...}
    for seg in segs:
        ss = seg["spans"]
        # include small formula components glued to a kept candidate
        cands = []
        for idx, s in enumerate(ss):
            if id(s) in kept:
                cands.append(s)
                continue
            nb = None
            for off in (-1, 1):
                j = idx + off
                if 0 <= j < len(ss) and id(ss[j]) in kept:
                    nb = ss[j]
                    break
            if nb is not None and _is_component(s, nb):
                cands.append(s)
        cands.sort(key=lambda s: s["bbox"][0])
        if not cands:
            continue
        runs = [[cands[0]]]
        for s in cands[1:]:
            run = runs[-1]
            gap = s["bbox"][0] - run[-1]["bbox"][2]
            y_overlap = (s["bbox"][1] < run[-1]["bbox"][3]
                         and s["bbox"][3] > run[-1]["bbox"][1])
            # superscript/subscript spans of the same expression do not
            # overlap vertically with the base row - merge adjacent math
            # candidates with a small gap regardless of y overlap
            both_math = (is_math_font(s.get("font"))
                         or is_math_font(run[-1].get("font")))
            open_expr = _ends_open(run) or _bracket_balance(run) > 0
            if gap < GAP_MERGE_PT and (y_overlap or both_math) \
                    or open_expr:
                run.append(s)
            else:
                runs.append([s])
        for run in runs:
            if not run:
                continue
            b = _union([s["bbox"] for s in run])
            blocks.append({"spans": run, "bbox": b,
                           "cy": (b[1] + b[3]) / 2.0,
                           "main_size": _main_size(run)})
    return blocks


# --------------------------------------------------------------------------
# 5. large-operator limits binding
# --------------------------------------------------------------------------
def _operator_anchors(spans):
    """CMEX spans or explicit big-operator unicode chars."""
    anchors = []
    for s in spans:
        low = (s.get("font") or "").lower()
        text = s.get("text") or ""
        if "cmex" in low:
            anchors.append(s)
        elif any(c in LARGE_OP_CHARS for c in text) and len(text.strip()) <= 2:
            anchors.append(s)
    return anchors


def _bind_operator_limits(blocks, kept, spans):
    """Bind small-size superscript/subscript spans above/below a large
    operator back into the operator's block (e.g. Sigma limits), then run a
    general script-binding pass for every small span next to a math span
    (e.g. '12'/'J' subscripts of C1/C2/CJ, 'agg' in f_agg, S^(0) exponent)."""
    anchors = _operator_anchors(spans)
    if anchors:
        _bind_to_anchors(blocks, kept, spans, anchors)
    _bind_generic_scripts(blocks, kept, spans)
    # rebuild cy / main_size after binding
    for blk in blocks:
        blk["cy"] = (blk["bbox"][1] + blk["bbox"][3]) / 2.0
    return blocks


def _bind_to_anchors(blocks, kept, spans, anchors):
    used = set()
    for blk in blocks:
        blk_ids = {id(s) for s in blk["spans"]}
        for op in anchors:
            if id(op) not in blk_ids:
                continue
            ob = op["bbox"]
            ocx = (ob[0] + ob[2]) / 2.0
            ocy = (ob[1] + ob[3]) / 2.0
            op_h = max(ob[3] - ob[1], 1.0)
            op_w = max(ob[2] - ob[0], 1.0)
            for s in spans:
                if id(s) in blk_ids or id(s) in used or id(s) not in kept:
                    continue
                sb = s["bbox"]
                size = s.get("size") or 0
                main = blk["main_size"]
                if main and size >= main * 0.88:
                    continue
                scx = (sb[0] + sb[2]) / 2.0
                scy = (sb[1] + sb[3]) / 2.0
                dx = abs(scx - ocx)
                dy = abs(scy - ocy)
                above = scy < ob[1] - 0.3
                below = scy > ob[3] + 0.3
                if (above or below) and dx < max(6.0, op_w) \
                        and 0.2 * op_h < dy < 2.6 * op_h:
                    blk["spans"].append(s)
                    used.add(id(s))
                    blk["bbox"] = _union([blk["bbox"], sb])


def _bind_generic_scripts(blocks, kept, spans):
    """Bind any small span whose x overlaps (or sits adjacent to) a math span
    in a block and whose centre-y is clearly above/below that span's centre
    (superscript/subscript position).  Bound spans become components of the
    block instead of standalone FormulaBlocks."""
    used = set()
    changed = True
    while changed:
        changed = False
        for blk in blocks:
            blk_ids = {id(s) for s in blk["spans"]}
            main = blk["main_size"]
            for s in spans:
                if id(s) in blk_ids or id(s) in used or id(s) not in kept:
                    continue
                sb = s["bbox"]
                size = s.get("size") or 0
                if main and size >= main * 0.88:
                    continue
                scx = (sb[0] + sb[2]) / 2.0
                scy = (sb[1] + sb[3]) / 2.0
                best = None
                best_d = None
                for t in blk["spans"]:
                    tb = t["bbox"]
                    tcx = (tb[0] + tb[2]) / 2.0
                    # x adjacency / overlap
                    dx = max(tb[0] - sb[2], sb[0] - tb[2], 0.0)
                    if dx > 3.0:
                        continue
                    if not is_math_font(t.get("font")):
                        continue
                    d = abs(scx - tcx)
                    if best is None or d < best_d:
                        best, best_d = t, d
                if best is None:
                    continue
                tb = best["bbox"]
                tcy = (tb[1] + tb[3]) / 2.0
                th = max(tb[3] - tb[1], 1.0)
                dy = abs(scy - tcy)
                # generic scripts sit 0.2-1.5 line-heights from their base
                # glyph (wider windows belong to the operator-anchor pass)
                if dy < 0.2 * th or dy > 1.5 * th:
                    continue
                # target span itself may be large (operator) or normal; the
                # bound span is small and sits above/below it
                blk["spans"].append(s)
                used.add(id(s))
                blk["bbox"] = _union([blk["bbox"], sb])
                changed = True


# --------------------------------------------------------------------------
# 6. equation-group cross-line merge
# --------------------------------------------------------------------------
def _seg_blocks_between(segs, spans, a, b, kept):
    """Ordinary-text segment whose y range lies strictly between the two
    blocks AND whose x range overlaps either block.  A segment that merely
    shares the same row as a block (e.g. inline text after a formula) or sits
    in a different column is NOT a blocker."""
    kept_ids = kept
    y_lo = min(a["bbox"][3], b["bbox"][3])
    y_hi = max(a["bbox"][1], b["bbox"][1])
    if y_lo >= y_hi:
        return False
    for seg in segs:
        if seg["y1"] <= y_lo + 1 or seg["y0"] >= y_hi - 1:
            continue
        # the segments that carry a/b's own spans are not blockers
        if any(any(s is x for s in seg["spans"]) for x in a["spans"]):
            continue
        if any(any(s is x for s in seg["spans"]) for x in b["spans"]):
            continue
        x_ov_a = (seg["x1"] > a["bbox"][0] + 2 and seg["x0"] < a["bbox"][2] - 2)
        x_ov_b = (seg["x1"] > b["bbox"][0] + 2 and seg["x0"] < b["bbox"][2] - 2)
        if not (x_ov_a or x_ov_b):
            continue
        # must contain at least one plain (non-candidate) span
        plain = [s for s in seg["spans"]
                 if id(s) not in kept_ids and (s.get("text") or "").strip()]
        if plain:
            return True
    return False


def _cross_line_merge(blocks, kept, spans, segs):
    """Two passes:

    1. continuation pass - a block whose expression is open (ends with
       an operator / comma / bracket) merges with the left-most candidate
       block on the next row (the expression continues on the next line,
       usually left-aligned);
    2. overlap pass - blocks with large x-overlap and a normal line gap merge
       (multi-row display equations / operator stacks).
    """
    # --- pass 1: continuation ---
    changed = True
    while changed:
        changed = False
        for i in range(len(blocks)):
            a = blocks[i]
            if not (_ends_open(a["spans"]) or _bracket_balance(a["spans"]) > 0):
                continue
            below = [j for j in range(len(blocks)) if j != i
                     and min(_cy(x) for x in blocks[j]["spans"])
                     > _row_cy(a["spans"]) + 1.0]
            if not below:
                continue
            # restrict to next-row candidates within a normal line gap
            avg_h = (a["main_size"] + blocks[below[0]]["main_size"]) / 2.0
            row_ok = [j for j in below
                      if min(_cy(x) for x in blocks[j]["spans"])
                      - _row_cy(a["spans"]) < avg_h * 2.2]
            if not row_ok:
                continue
            # the continuation target is the left-most candidate on that row
            # (an expression continues on the next line, left-aligned)
            j = min(row_ok, key=lambda j: blocks[j]["bbox"][0])
            b = blocks[j]
            if _should_merge(a, b, segs, spans, kept, open_ok=True):
                a["spans"] = a["spans"] + [x for x in b["spans"]
                                           if not any(x is y for y in a["spans"])]
                a["bbox"] = _union([a["bbox"], b["bbox"]])
                a["cy"] = (a["bbox"][1] + a["bbox"][3]) / 2.0
                a["main_size"] = max(a["main_size"], b["main_size"])
                del blocks[j]
                changed = True
                break
        # (the while loop restarts from i=0 after each merge)

    # --- pass 2: overlap ---
    changed = True
    while changed:
        changed = False
        for i in range(len(blocks)):
            for j in range(i + 1, len(blocks)):
                a, b = blocks[i], blocks[j]
                if _should_merge(a, b, segs, spans, kept, open_ok=False):
                    a["spans"] = a["spans"] + [x for x in b["spans"]
                                               if not any(x is y for y in a["spans"])]
                    a["bbox"] = _union([a["bbox"], b["bbox"]])
                    a["cy"] = (a["bbox"][1] + a["bbox"][3]) / 2.0
                    a["main_size"] = max(a["main_size"], b["main_size"])
                    del blocks[j]
                    changed = True
                    break
            if changed:
                break
    return blocks


def _row_cy(spans):
    """Centre-y of the *lowest* logical row inside a block (row centre, not
    the max span centre - avoids superscript/subscript noise)."""
    rows = []
    for s in spans:
        cy = _cy(s)
        placed = False
        for r in rows:
            if abs(cy - r["cy"]) <= 3.0:
                r["spans"].append(s)
                r["cy"] = sum(_cy(x) for x in r["spans"]) / len(r["spans"])
                placed = True
                break
        if not placed:
            rows.append({"cy": cy, "spans": [s]})
    if not rows:
        return 0.0
    return max(r["cy"] for r in rows)


def _block_in_plain_text(segs, blk, kept):
    """True when the block's own segment(s) contain substantial ordinary
    text (the block is an inline formula embedded in a prose line)."""
    for seg in segs:
        if not any(any(s is x for s in seg["spans"]) for x in blk["spans"]):
            continue
        plain_w = sum(s["bbox"][2] - s["bbox"][0]
                      for s in seg["spans"]
                      if id(s) not in kept
                      and (s.get("text") or "").strip()
                      and not _is_component(s, None))
        if plain_w > 12.0:
            return True
    return False


def _should_merge(a, b, segs, spans, kept, open_ok=False):
    if _is_eq_number(a["spans"][-1], a["main_size"]) and len(a["spans"]) == 1:
        return False
    if _is_eq_number(b["spans"][-1], b["main_size"]) and len(b["spans"]) == 1:
        return False
    # line gap: lowest row of a -> highest row of b (block centres hide the
    # true inter-line distance once a block spans several rows)
    dy = min(_cy(s) for s in b["spans"]) - _row_cy(a["spans"])
    if dy <= 0:
        return False
    avg_h = (a["main_size"] + b["main_size"]) / 2.0
    a_open = _ends_open(a["spans"]) or _bracket_balance(a["spans"]) > 0
    ol = min(a["bbox"][2], b["bbox"][2]) - max(a["bbox"][0], b["bbox"][0])
    min_w = min(a["bbox"][2] - a["bbox"][0], b["bbox"][2] - b["bbox"][0])
    overlap_ratio = ol / min_w if min_w > 0 else 0.0
    blocked = _seg_blocks_between(segs, spans, a, b, kept)
    if blocked:
        return False
    if a_open and dy < avg_h * 2.2:
        return True
    # overlap merge only when b is NOT an inline formula inside a prose line
    # (e.g. a bare 'Cj' in "For each cluster, ...") - those belong to their
    # own line context, not to the display expression above
    if overlap_ratio > 0.3 and dy < avg_h * 1.7 \
            and not _block_in_plain_text(segs, b, kept):
        return True
    return False


# --------------------------------------------------------------------------
# 7. equation-number merge
# --------------------------------------------------------------------------
def _merge_eq_numbers(blocks, kept, spans, segs):
    for blk in blocks:
        num_cands = [s for s in spans if id(s) in kept
                     and _is_eq_number(s, blk["main_size"])]
    for blk in blocks:
        b = blk["bbox"]
        blk_ids = {id(s) for s in blk["spans"]}
        for s in num_cands:
            if id(s) in blk_ids:
                continue
            sb = s["bbox"]
            if not (sb[0] >= b[2] - 2 and sb[0] < b[2] + 60):
                continue
            # same line / just below the last row (display numbering)
            on_line = (sb[1] < b[3] + 2 and sb[3] > b[1] - 2)
            below = (sb[1] >= b[3] - 1 and sb[1] < b[3] + avg_h_of(blk) * 1.6)
            if on_line or below:
                blk["spans"].append(s)
                blk["bbox"] = _union([blk["bbox"], sb])
    return blocks


def avg_h_of(blk):
    return blk.get("main_size") or 10.0


# --------------------------------------------------------------------------
# 8. classify + FormulaModel
# --------------------------------------------------------------------------
def _fraction_analysis(blk, drawings):
    """True only for a real fraction topology: a thin horizontal bar with
    content spans above AND below it."""
    b = blk["bbox"]
    for d in drawings:
        db = d["bbox"]
        h = db[3] - db[1]
        w = db[2] - db[0]
        if not (0 < h <= 1.2 and w >= 8):
            continue
        if not (db[2] > b[0] + 1 and db[0] < b[2] - 1
                and db[3] > b[1] + 1 and db[1] < b[3] - 1):
            continue
        bar_y = (db[1] + db[3]) / 2.0
        above = [s for s in blk["spans"] if _cy(s) < bar_y - 1]
        below = [s for s in blk["spans"] if _cy(s) > bar_y + 1]
        if above and below:
            return True
    return False


def _segment_context(segs, blk):
    """Segments overlapping the block's y range; the block's own segments and
    plain-text widths (excluding formula components and eq numbers)."""
    own = []
    for seg in segs:
        if any(any(s is x for s in seg["spans"]) for x in blk["spans"]):
            own.append(seg)
    if not own:
        return [], 0.0, 0.0
    bx0 = min(g["x0"] for g in own)
    bx1 = max(g["x1"] for g in own)
    # also include neighbour segments whose y band overlaps the block AND whose
    # x range overlaps the block - a segment in a different column
    # (two-column layout) or far away is NOT context
    band = [seg for seg in segs
            if seg["y1"] >= min(g["y0"] for g in own) - 2
            and seg["y0"] <= max(g["y1"] for g in own) + 2
            and seg["x1"] > bx0 + 2 and seg["x0"] < bx1 - 2]
    plain_w = 0.0
    band_w = 0.0
    for seg in band:
        for s in seg["spans"]:
            if any(s is x for x in blk["spans"]):
                continue
            if _is_eq_number(s):
                continue
            w = s["bbox"][2] - s["bbox"][0]
            band_w += w
            if not span_is_candidate(s) and not _is_component(s, None):
                plain_w += w
    return own, plain_w, band_w


def _count_rows(spans):
    """Number of logical rows inside a block (row clustering with a 3pt
    tolerance; superscript/subscript rows count separately)."""
    rows = []
    for s in spans:
        cy = _cy(s)
        for r in rows:
            if abs(cy - r["cy"]) <= 3.0:
                r["n"] += 1
                r["cy"] = (r["cy"] * (r["n"] - 1) + cy) / r["n"]
                break
        else:
            rows.append({"cy": cy, "n": 1})
    return len(rows)


# --------------------------------------------------------------------------
# 9. FormulaGroup / RenderSegment separation
# --------------------------------------------------------------------------
def _cluster_rows(spans):
    """Cluster block spans into visual rows (3pt tolerance)."""
    rows = []
    for s in spans:
        cy = _cy(s)
        for r in rows:
            if abs(cy - r["cy"]) <= 3.0:
                r["spans"].append(s)
                n = len(r["spans"])
                r["cy"] = (r["cy"] * (n - 1) + cy) / n
                break
        else:
            rows.append({"cy": cy, "spans": [s]})
    return rows


def _bbox_intersect(a, b, margin=2.0):
    return not (a[2] < b[0] - margin or a[0] > b[2] + margin
                or a[3] < b[1] - margin or a[1] > b[3] + margin)


def _foreign_plain_in_bbox(bbox, own_spans, spans, kept):
    """Any ordinary-text span (not a kept candidate) inside the bbox?  These
    are not part of this formula group - their presence means the union
    rectangle covers foreign prose."""
    own_ids = {id(s) for s in own_spans}
    for s in spans:
        if id(s) in own_ids:
            continue
        if id(s) in kept or is_math_font(s.get("font")):
            continue
        if not (s.get("text") or "").strip():
            continue
        if _bbox_intersect(s["bbox"], bbox, margin=1.0):
            return True
    return False


def _foreign_block_overlap(bbox, own_block, all_blocks):
    """Another FormulaBlock whose bbox substantially overlaps this bbox."""
    for ob in all_blocks:
        if ob is own_block:
            continue
        obb = ob["bbox"]
        ix = min(bbox[2], obb[2]) - max(bbox[0], obb[0])
        iy = min(bbox[3], obb[3]) - max(bbox[1], obb[1])
        if ix > 2.0 and iy > 2.0:
            return True
    return False


def _split_render_segments(blk, spans, kept, all_blocks):
    """Split a FormulaGroup into RenderSegments.

    * display groups whose union box contains no foreign content keep a
      single segment (multi-line display equations like eq(3), S^(0));
    * everything else (cross-line inline formulas, or display boxes polluted
      by foreign text) is split by visual rows; adjacent rows with continuous
      x and overlapping y are re-merged into one segment.
    """
    b = blk["bbox"]
    is_display = blk.get("is_display", False)
    foreign_text = _foreign_plain_in_bbox(b, blk["spans"], spans, kept)
    foreign_blk = _foreign_block_overlap(b, blk, all_blocks)
    if is_display and not foreign_text and not foreign_blk:
        return [{"layout_bbox": b, "spans": list(blk["spans"])}]

    rows = _cluster_rows(blk["spans"])
    segments = []
    for r in rows:
        rb = _union([s["bbox"] for s in r["spans"]])
        segments.append({"layout_bbox": rb, "spans": r["spans"]})
    # merge consecutive segments that are x-continuous and y-overlapping
    merged = [segments[0]]
    for seg in segments[1:]:
        last = merged[-1]
        gap_x = seg["layout_bbox"][0] - last["layout_bbox"][2]
        y_ov = (seg["layout_bbox"][1] < last["layout_bbox"][3]
                and seg["layout_bbox"][3] > last["layout_bbox"][1])
        if y_ov and gap_x < GAP_MERGE_PT:
            last["layout_bbox"] = _union([last["layout_bbox"],
                                          seg["layout_bbox"]])
            last["spans"] = last["spans"] + seg["spans"]
        else:
            merged.append(seg)
    return merged


def _classify(blocks, kept, spans, segs, drawings):
    models = []
    blocks.sort(key=lambda b: (b["bbox"][1], b["bbox"][0]))
    for idx, blk in enumerate(blocks):
        b = blk["bbox"]
        spans_blk = blk["spans"]
        fonts = {s.get("font") for s in spans_blk}
        sizes = sorted({round(float(s.get("size") or 0), 2) for s in spans_blk})
        low = " ".join((x or "").lower() for x in fonts)

        contains_fraction = _fraction_analysis(blk, drawings)
        contains_large = "cmex" in low or any(
            c in "".join((s.get("text") or "") for s in spans_blk)
            for c in LARGE_OP_CHARS)
        contains_super = len(sizes) > 1 and any(
            is_small_math_font(s.get("font")) for s in spans_blk)
        multi_row = _count_rows(spans_blk) > 1

        # multi-row structures (stacked display equations, scripted operators)
        # count as complex even without large symbols / small fonts
        is_complex = (contains_fraction or contains_large
                      or contains_super or multi_row)

        # ---- inline/display from segment context ----
        own_segs, plain_w, _ = _segment_context(segs, blk)
        has_eq_num = any(_is_eq_number(s, blk["main_size"])
                         for s in spans_blk)
        seg_w = max(b[2] - b[0], 1.0)
        math_w = sum(s["bbox"][2] - s["bbox"][0] for s in spans_blk)
        is_display = (plain_w < 12.0) or (has_eq_num and math_w > 0.5 * seg_w)

        ftype = ("complex_display" if is_complex and is_display else
                 "complex_inline" if is_complex else
                 "display" if is_display else "inline")

        source_spans = sorted(spans_blk, key=lambda s: (round(_cy(s), 1), s["bbox"][0]))
        source_text = "".join((s.get("text") or "") for s in source_spans)
        baseline = max(s["bbox"][3] for s in source_spans)

        # ---- confidence ----
        mw = sum(s["bbox"][2] - s["bbox"][0] for s in spans_blk) or 1.0
        math_font_w = sum(s["bbox"][2] - s["bbox"][0] for s in spans_blk
                          if is_math_font(s.get("font")))
        math_char_w = sum(s["bbox"][2] - s["bbox"][0] for s in spans_blk
                          if _has_math_char(s.get("text") or ""))
        det = 0.45 + 0.35 * (math_font_w / mw) + 0.20 * (math_char_w / mw)
        det = max(0.3, min(1.0, det))
        bal = _bracket_balance(spans_blk)
        comp = 0.55
        if bal == 0:
            comp += 0.15
        else:
            comp -= 0.1
        if contains_large:
            comp += 0.15
        if len(spans_blk) > 1:
            comp += 0.10
        if has_eq_num:
            comp += 0.05
        if len(own_segs) > 1 and not is_display:
            comp += 0.05
        comp = max(0.3, min(1.0, comp))

        # ---- FormulaGroup -> RenderSegments ----
        blk["is_display"] = is_display
        rsegs = _split_render_segments(blk, spans, kept, blocks)
        render_segments = []
        for si, rs in enumerate(rsegs):
            rsb = rs["layout_bbox"]
            render_segments.append({
                "segment_id": "B%d-S%d" % (idx + 1, si + 1),
                "layout_bbox": [round(v, 3) for v in rsb],
                "render_viewbox": None,  # filled by the renderer
                "span_count": len(rs["spans"]),
            })

        models.append({
            "formula_id": "B%d" % (idx + 1),
            "type": ftype,
            "placement": "block" if is_display else "inline",
            "layout_bbox": [round(v, 3) for v in b],
            "ink_bbox": None,
            "baseline": round(baseline, 3),
            "source_text": source_text,
            "raw_component_text": source_text,
            "source_text_order_reliable": False,
            "components": [
                {"text": s.get("text"), "font": s.get("font"),
                 "size": s.get("size"),
                 "bbox": [round(v, 3) for v in s["bbox"]]} for s in spans_blk],
            "render_segments": render_segments,
            "contains_large_symbol": contains_large,
            "contains_fraction": contains_fraction,
            "contains_superscript": contains_super,
            "fonts": sorted(fonts),
            "font_sizes": sizes,
            "detection_confidence": round(det, 3),
            "composition_confidence": round(comp, 3),
            "translation_policy": "protect",
            "render_policy": "atomic_svg",
            "svg_asset": None,
            "svg_mode": None,
        })
    return models


def compose_blocks(spans, drawings, images, page_w, page_h) -> list[dict]:
    """Spans -> FormulaModel dicts (Phase 3E rev 2 schema)."""
    segs = _logical_segments(spans)
    kept = _filter_false_positives(spans, segs, page_h)
    if not kept:
        return []
    blocks = _inline_merge(segs, kept, spans)
    blocks = _bind_operator_limits(blocks, kept, spans)
    blocks = _cross_line_merge(blocks, kept, spans, segs)
    blocks = _merge_eq_numbers(blocks, kept, spans, segs)

    # dedupe: drop a block whose spans are all contained in another block
    final_blocks = []
    for i, a in enumerate(blocks):
        a_ids = {id(s) for s in a["spans"]}
        absorbed = False
        for j, b in enumerate(blocks):
            if i == j:
                continue
            if a_ids and a_ids <= {id(s) for s in b["spans"]}:
                absorbed = True
                break
        if not absorbed:
            final_blocks.append(a)

    return _classify(final_blocks, kept, spans, segs, drawings)
