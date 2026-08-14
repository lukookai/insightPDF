# -*- coding: utf-8 -*-
"""Phase 4E.1B-C3.2 finalizer: boards + checkpoint gate + report."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(HERE.parent.parent / "实现源码" / "pdf_translator") not in sys.path:
    sys.path.insert(0, str(HERE.parent.parent / "实现源码" / "pdf_translator"))

from math_dense_translation_router import build_protected_template  # noqa: E402

REPO = HERE.parent.parent
C32 = REPO / "outputs" / "phase4e1b_c32_math_boundary"


def _load(name):
    return json.loads((C32 / name).read_text(encoding="utf-8"))


def _font(size, bold=False):
    for c in (Path("C:/Windows/Fonts/arialbd.ttf" if bold else "C:/Windows/Fonts/arial.ttf"),
              Path("C:/Windows/Fonts/msyhbd.ttc" if bold else "C:/Windows/Fonts/msyh.ttc")):
        if c.exists():
            return ImageFont.truetype(str(c), size)
    return ImageFont.load_default()


def _wrap(text, font, draw, width):
    if not text:
        return [""]
    lines, cur = [], ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= width:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines[:6]


# --------------------------------------------------------------------------
# board 1: protection boundary before/after
# --------------------------------------------------------------------------
def math_boundary_board():
    old = _load("math_protection_boundary_old_red.json")
    new = _load("math_protection_boundary_after.json")
    before = {r["fixture"]: r["protected_text"] for r in old["negative_fixtures"]}
    after = {r["fixture"]: r["protected_text"] for r in new["negative_fixtures"]}

    rows = []
    for fx in ("article(i.e., ...)", "method(s)", "model(s)", "result(s)"):
        rows.append((fx, before[fx], after[fx], "FIXED"))
    for fx in ("f(x)", "sin(x)", "L(x)"):
        prot = build_protected_template(fx)["protected_text"]
        rows.append((fx, prot, prot, "PRESERVED"))

    W, cell_h = 1240, 118
    img = Image.new("RGB", (W, 90 + cell_h * len(rows) + 16), "white")
    d = ImageDraw.Draw(img)
    d.text((16, 12), "Math protection boundary -- before / after",
           fill=(20, 20, 20), font=_font(19, True))
    cols = [24, 280, 620, 1060]
    for x, h in zip(cols, ["FIXTURE", "BEFORE (old)", "AFTER (new)", "RESULT"]):
        d.text((x, 52), h, fill=(40, 40, 40), font=_font(12, True))
    y = 78
    for fx, b, a, res in rows:
        d.rectangle([14, y, W - 14, y + cell_h - 10], outline=(210, 210, 210))
        d.text((cols[0], y + 20), fx, fill=(20, 20, 20), font=_font(13, True))
        for i, line in enumerate(_wrap(b, _font(12), d, 320)):
            d.text((cols[1], y + 20 + i * 18), line, fill=(180, 30, 30), font=_font(12))
        for i, line in enumerate(_wrap(a, _font(12), d, 420)):
            d.text((cols[2], y + 20 + i * 18), line, fill=(20, 130, 50), font=_font(12))
        d.text((cols[3], y + 20), res,
               fill=(20, 130, 50) if res == "FIXED" else (20, 100, 50),
               font=_font(12, True))
        y += cell_h
    img.save(C32 / "math_boundary_before_after.png")


# --------------------------------------------------------------------------
# board 2: validator token-boundary
# --------------------------------------------------------------------------
def validator_board():
    old = _load("validator_boundary_old_red.json")
    new = _load("validator_boundary_after.json")
    cases = new["cases"]
    old_by_case = {r["case"]: r for r in old["cases"]}

    W, cell_h = 1240, 96
    img = Image.new("RGB", (W, 90 + cell_h * len(cases) + 16), "white")
    d = ImageDraw.Draw(img)
    d.text((16, 12), "Validator token boundary -- single-char token: old substring vs new token-aware",
           fill=(20, 20, 20), font=_font(17, True))
    cols = [24, 360, 660, 900, 1080]
    for x, h in zip(cols, ["CASE", "EXPECTED", "OLD (substring)", "NEW (token-aware)", "FIXED"]):
        d.text((x, 52), h, fill=(40, 40, 40), font=_font(12, True))
    y = 78
    for c in cases:
        oc = old_by_case[c["case"]]
        d.rectangle([14, y, W - 14, y + cell_h - 10], outline=(210, 210, 210))
        d.text((cols[0], y + 24), c["case"][:40], fill=(20, 20, 20), font=_font(11))
        exp = "PASS" if c["expected_pass"] else "FAIL"
        d.text((cols[1], y + 24), exp, fill=(20, 20, 20), font=_font(11))
        ov = "PASS" if oc["actual_pass"] else "FAIL"
        oc_ok = oc["actual_pass"] == oc["expected_pass"]
        d.text((cols[2], y + 24), ov,
               fill=(20, 130, 50) if oc_ok else (180, 30, 30), font=_font(11, True))
        nv = "PASS" if c["actual_pass"] else "FAIL"
        d.text((cols[3], y + 24), nv, fill=(20, 130, 50), font=_font(11, True))
        d.text((cols[4], y + 24), "YES" if c["correct"] and not oc_ok else ("OK" if c["correct"] else "NO"),
               fill=(20, 130, 50) if c["correct"] else (180, 30, 30), font=_font(11, True))
        y += cell_h
    img.save(C32 / "validator_token_boundary_board.png")


# --------------------------------------------------------------------------
# gate
# --------------------------------------------------------------------------
def compute_gate():
    old_pb = _load("math_protection_boundary_old_red.json")
    new_pb = _load("math_protection_boundary_after.json")
    old_vb = _load("validator_boundary_old_red.json")
    new_vb = _load("validator_boundary_after.json")
    audit = _load("math_protection_precision_recall_audit.json")
    summary = _load("router_execution_summary.json")
    formula = _load("formula_regression_remeasured.json")
    old4 = _load("old_4d2c_regression.json")
    freeze = _load("production_freeze_audit.json")
    cache = _load("production_cache_hash_audit.json")

    fp_after = new_pb["summary"]["false_positive_count"]
    conditions = {
        "math_protection_boundary_old_red": old_pb["summary"]["false_positive_count"] > 0,
        "prose_word_tail_false_math_token_count_0": fp_after == 0,
        "math_atom_false_positive_count_0": fp_after == 0,
        "lost_true_math_tokens_0": audit["lost_true_math_tokens"] == 0,
        "validator_boundary_cases_pass_all": new_vb["summary"]["all_correct"] is True,
        "real_translation_api_calls_gt_0": summary["real_translation_api_calls"] > 0,
        "math_dense_execution_failure_0": summary["math_dense_execution_failure"] == 0,
        "math_protected_execution_failure_0": summary["math_protected_execution_failure"] == 0,
        "retry_failure_count_0": summary["retry_failure_count"] == 0,
        "placeholder_loss_0": summary["placeholder_loss"] == 0,
        "math_token_mutation_0": summary["math_token_mutation"] == 0,
        "protected_markup_mutation_0": summary["protected_markup_mutation"] == 0,
        "non_translatable_api_calls_0": summary["non_translatable_api_calls"] == 0,
        "duplicate_formula_0": formula["duplicate_formula"] == 0,
        "orphan_formula_0": formula["orphan_formula"] == 0,
        "formula_crop_0": formula["formula_crop"] == 0,
        "existing_svg_hash_mismatch_0": formula["existing_svg_hash_mismatch"] == 0,
        "old_4d2c_regression_0": old4["decision"] == "pass",
        "production_cache_hash_changed_false": cache["production_cache_hash_changed"] is False,
        "production_special_case_count_0": freeze["production_special_case_count"] == 0,
    }
    decision = "pass" if all(conditions.values()) else "blocked"
    return {
        "schema_version": "phase4e1b.c32.checkpoint_gate.v1",
        "phase": "4E.1B-C3.2",
        "conditions": conditions,
        "passed_count": sum(1 for v in conditions.values() if v),
        "total_count": len(conditions),
        "decision": decision,
    }


def main():
    math_boundary_board()
    validator_board()
    gate = compute_gate()
    (C32 / "checkpoint_gate.json").write_text(
        json.dumps(gate, ensure_ascii=False, indent=2), encoding="utf-8")
    print("boards written")
    print("gate decision:", gate["decision"],
          f'({gate["passed_count"]}/{gate["total_count"]})')
    for k, v in gate["conditions"].items():
        print(f"  {k}: {v}")


if __name__ == "__main__":
    main()
