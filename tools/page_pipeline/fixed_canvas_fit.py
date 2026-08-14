"""LocalFitStrategy -- region-local fit gradient for visual-v01.

For every soft-text region the fixed-canvas visual route applies a strict,
ordered local adaptation gradient.  All changes are confined to the region
itself; hard anchors are never displaced and the region never spills into
the next object's space.

Gradient (each level is strictly stronger, all region-local):

  L0  source-faithful      render with the balanced typography as-is
  L1  line-wrap only       conservative wrapping, no boundary change
  L2  micro spacing        script-gap / token spacing tweaks (no global
                           word-spacing, region only)
  L3  line-height tighten  small line-height reduction (readability guard)
  L4  font-size shrink     small font shrink with a hard floor
  L5  paragraph gap        tighten inter-paragraph gaps (prose/caption only)

If L0..L5 still do not fit: capacity_unresolved = True and the delivery
gate must BLOCK.  Never silently add pages; never push the object below.

Only the *height* axis is the binding constraint on the fixed canvas
(width is already fixed by the column track), so the strategy reduces to
finding the smallest adaptation that makes est_height <= region_height.
"""

from __future__ import annotations

from typing import Any, Dict, List

from flow_layout import estimate_paragraph_height  # noqa: E402

# hard floors (readability / typography guards)
MIN_FONT_SCALE = 0.85
MIN_LINE_HEIGHT = 1.05          # pt multiplier on base size
BASE_LINE_HEIGHT = 1.3          # flow_layout default
LINE_TIGHTEN_STEP = 0.92        # per L3 step
FONT_SHRINK_STEP = 0.95         # per L4 step


class LocalFitStrategy:
    """Region-local fit resolution for one soft-text region."""

    def __init__(self, width_map: Dict[str, float] | None = None):
        self.width_map = width_map or {}
        self.records: List[Dict[str, Any]] = []

    # ------------------------------------------------------------- public
    def fit_paragraph(self, zh: str, font_size: float, col_width: float,
                      region_height: float) -> Dict[str, Any]:
        """Fit a single paragraph into ``region_height`` (pt).

        Returns {level, font_scale, line_height_scale, est_height,
        unresolved, detail}.  ``font_size`` is the source/typography size;
        the returned scales are the smallest adaptation that fits.
        """
        if region_height <= 0 or not zh.strip():
            return {"level": 0, "font_scale": 1.0, "line_height_scale": 1.0,
                    "est_height": 0.0, "unresolved": False,
                    "detail": "empty or no region"}
        fs = max(float(font_size or 10.0), 1.0)
        lh = BASE_LINE_HEIGHT
        est = estimate_paragraph_height(zh, fs, max(col_width, 1.0),
                                        self.width_map, line_height=lh)
        level = 0
        fscale, lscale = 1.0, 1.0
        if est <= region_height + 0.5:
            rec = self._record(zh, fs, col_width, region_height, level,
                               fscale, lscale, est, False)
            return rec
        # L3: line-height tightening first (keeps glyph size)
        lh_l3 = BASE_LINE_HEIGHT * LINE_TIGHTEN_STEP
        est_l3 = estimate_paragraph_height(zh, fs, max(col_width, 1.0),
                                           self.width_map, line_height=lh_l3)
        if est_l3 <= region_height + 0.5:
            lscale = LINE_TIGHTEN_STEP
            level = 3
            rec = self._record(zh, fs, col_width, region_height, level,
                               fscale, lscale, est_l3, False)
            return rec
        # L4: font shrink steps (with hard floor) -- each step re-estimates
        # at the shrunk size with tightened line-height
        fs_cur = fs
        fsteps = 0
        while fsteps < 4:  # 1.0 -> 0.95 -> 0.90 -> 0.85 floor
            fs_cur = fs * (FONT_SHRINK_STEP ** (fsteps + 1))
            if fs_cur / fs < MIN_FONT_SCALE - 1e-6:
                break
            lh_cur = max(BASE_LINE_HEIGHT * LINE_TIGHTEN_STEP,
                         MIN_LINE_HEIGHT)
            est_cur = estimate_paragraph_height(
                zh, fs_cur, max(col_width, 1.0), self.width_map,
                line_height=lh_cur)
            if est_cur <= region_height + 0.5:
                fscale = fs_cur / fs
                lscale = LINE_TIGHTEN_STEP
                level = 4
                rec = self._record(zh, fs, col_width, region_height, level,
                                   fscale, lscale, est_cur, False)
                return rec
            fsteps += 1
        # L5: paragraph-gap tightening is a *cross-paragraph* step applied
        # by the layout (see fit_sequence); for a single paragraph it is a
        # no-op and we report unresolved.
        rec = self._record(zh, fs, col_width, region_height, level,
                           fscale, lscale, est_cur, True,
                           detail="L0..L4 insufficient")
        return rec

    def fit_sequence(self, paras: List[Dict[str, Any]],
                     region_top: float, region_height: float,
                     col_width: float) -> Dict[str, Any]:
        """Fit a consecutive soft-text sequence into one region.

        ``paras``: [{"text", "font_size", "anchor_y", ...}] ordered by y.
        Sequence-level steps (L5 gap tightening) act on the whole block.
        Returns {level, font_scale, line_height_scale, gap_scale,
        est_height, region_height, unresolved, per_para}.
        """
        if not paras or region_height <= 0:
            return {"level": 0, "font_scale": 1.0, "line_height_scale": 1.0,
                    "gap_scale": 1.0, "est_height": 0.0,
                    "region_height": region_height, "unresolved": False,
                    "per_para": []}
        # source gap between consecutive paras (clamped) for L5
        def _gap(a, b):
            g = b["anchor_y"] - (a["anchor_y"] + a["est_h0"])
            return max(2.0, min(14.0, g if 2.0 < g < 40.0 else 8.0))

        # ---- L0: full-size estimate -----------------------------------
        seq = []
        for p in paras:
            fs = max(float(p.get("font_size") or 10.0), 1.0)
            est0 = estimate_paragraph_height(p["text"], fs,
                                             max(col_width, 1.0),
                                             self.width_map,
                                             line_height=BASE_LINE_HEIGHT)
            seq.append({**p, "fs": fs, "est_h0": est0})
        gap0 = [_gap(seq[i], seq[i + 1]) for i in range(len(seq) - 1)]
        est_total = sum(s["est_h0"] for s in seq) + sum(gap0)
        if est_total <= region_height + 0.5:
            return self._seq_record(seq, 0, 1.0, 1.0, 1.0, est_total,
                                    region_height, False)

        # ---- L3: line-height tighten -----------------------------------
        lh3 = BASE_LINE_HEIGHT * LINE_TIGHTEN_STEP
        est_l3 = 0.0
        for s in seq:
            est_l3 += estimate_paragraph_height(s["text"], s["fs"],
                                                max(col_width, 1.0),
                                                self.width_map,
                                                line_height=lh3)
        est_l3 += sum(gap0)
        if est_l3 <= region_height + 0.5:
            return self._seq_record(seq, 3, 1.0, LINE_TIGHTEN_STEP, 1.0,
                                    est_l3, region_height, False)

        # ---- L4: font shrink (hard floor) ------------------------------
        fscale = 1.0
        while fscale * FONT_SHRINK_STEP >= MIN_FONT_SCALE - 1e-6:
            fscale *= FONT_SHRINK_STEP
            est_f = 0.0
            for s in seq:
                est_f += estimate_paragraph_height(
                    s["text"], s["fs"] * fscale, max(col_width, 1.0),
                    self.width_map, line_height=lh3)
            est_f += sum(gap0)
            if est_f <= region_height + 0.5:
                return self._seq_record(seq, 4, fscale, LINE_TIGHTEN_STEP,
                                        1.0, est_f, region_height, False)

        # ---- L5: paragraph-gap tightening (prose/caption only) ---------
        lh5 = lh3
        est_l5 = sum(
            estimate_paragraph_height(s["text"], s["fs"] * fscale,
                                      max(col_width, 1.0), self.width_map,
                                      line_height=lh5)
            for s in seq)
        gap_l5 = sum(max(g * 0.5, 2.0) for g in gap0)
        est_l5 += gap_l5
        if est_l5 <= region_height + 0.5:
            return self._seq_record(seq, 5, fscale, lh5 / BASE_LINE_HEIGHT,
                                    0.5, est_l5, region_height, False)

        return self._seq_record(seq, 5, fscale, lh5 / BASE_LINE_HEIGHT,
                                0.5, est_l5, region_height, True)

    # ------------------------------------------------------------ helpers
    def _record(self, zh, fs, col_w, region_h, level, fscale, lscale,
                est, unresolved, detail="ok"):
        rec = {"text_head": zh[:48], "font_size": fs, "col_width": col_w,
               "region_height": region_h, "level": level,
               "font_scale": round(fscale, 4),
               "line_height_scale": round(lscale, 4),
               "est_height": round(est, 3), "unresolved": unresolved,
               "detail": detail}
        self.records.append(rec)
        return rec

    def _seq_record(self, seq, level, fscale, lscale, gap_scale,
                    est_total, region_height, unresolved):
        rec = {"level": level, "font_scale": round(fscale, 4),
               "line_height_scale": round(lscale, 4),
               "gap_scale": round(gap_scale, 4),
               "est_height": round(est_total, 3),
               "region_height": round(region_height, 3),
               "unresolved": unresolved,
               "per_para": [{"paragraph_id": s.get("paragraph_id"),
                             "anchor_y": round(s["anchor_y"], 3),
                             "est_h": round(s["est_h0"], 3)} for s in seq]}
        self.records.append(rec)
        return rec

    def reset(self):
        self.records = []


def fit_paragraph(zh, font_size, col_width, region_height, width_map=None):
    return LocalFitStrategy(width_map).fit_paragraph(
        zh, font_size, col_width, region_height)


def fit_sequence(paras, region_top, region_height, col_width, width_map=None):
    return LocalFitStrategy(width_map).fit_sequence(
        paras, region_top, region_height, col_width)
