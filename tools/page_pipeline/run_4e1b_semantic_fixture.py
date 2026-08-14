# -*- coding: utf-8 -*-
"""Phase 4E.1B-B -- semantic integration fixture.

Builds the document model from RAW page models, runs apply_document_semantics
(writes semantic_role), then verifies the semantic QA is now consistent:
reference_role_gap_count = 0, reference_state_orphan_count = 0,
reference_false_positive_body_count = 0.  Also checks the old 4D.2C document
for body->reference false positives.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
sys.path.insert(0, str(HERE))

DING_PDF = (r"C:\Users\74496\Desktop\Ding_SynthRGB-T_Language-Vision_Guided_"
            r"Image_Translation_for_Diversity_Synthesis_CVPR_2026_paper.pdf")
DING_OLD = REPO / "outputs" / "phase4e1_ding_generalization"
OLD4D2C = REPO / "outputs" / "phase4d2c"
DIAG_PDF = REPO / "runs" / "diag_src_2504.pdf"
OUT = REPO / "outputs" / "phase4e1b_generalization_extension"


def _load(p, default=None):
    try:
        return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def build_document_from_raw(pdf, base_out, n_pages):
    from page_model import build_page_model
    from document_model import build_document_model
    raw_pages = []
    for p in range(1, n_pages + 1):
        tag = "p%03d" % p
        raw = _load(base_out / "pages" / tag / "raw_page_model.json")
        if raw is None:
            # rebuild the raw model (production main path)
            raw = build_page_model(str(pdf), p - 1,
                                   base_out / "pages" / tag,
                                   run_doclayout=True)
            _dump(base_out / "pages" / tag / "raw_page_model.json", raw)
        raw_pages.append(raw)
    return build_document_model(str(pdf), raw_pages)


def run_semantic_fixture(doc, name):
    from document_semantic_state import apply_document_semantics
    from document_semantic_qa import document_semantic_qa
    apply_document_semantics(doc)
    pages = {p["page"]: p for p in doc["pages"]}
    qa = document_semantic_qa(sorted(pages), pages)
    # body->reference false positives: body paragraphs written as reference
    fp = [pp for pp in qa.get("role_gap_details", []) if False]
    return qa


def main():
    OUT.mkdir(parents=True, exist_ok=True)
    report = {}

    # ---- Ding semantic fixture ----
    print("=== Ding semantic fixture ===")
    ding_doc = build_document_from_raw(DING_PDF, DING_OLD, 11)
    from document_semantic_state import apply_document_semantics
    from document_semantic_qa import document_semantic_qa
    apply_document_semantics(ding_doc)
    ding_pages = {p["page"]: p for p in ding_doc["pages"]}
    ding_qa = document_semantic_qa(sorted(ding_pages), ding_pages)
    # a MAIN-BODY false positive: a paragraph on a page BEFORE the reference
    # section start got a reference semantic_role
    def _main_body_fp(doc, ref_start_page):
        fpid = []
        for page in doc.get("pages", []):
            if page.get("page", 999) >= (ref_start_page or 999):
                continue
            for r in page.get("regions", []):
                if r.get("type") != "text":
                    continue
                p = r.get("payload") or {}
                if (p.get("semantic_role") or "").startswith("reference"):
                    fpid.append(p.get("paragraph_id"))
        return fpid

    ding_fp = _main_body_fp(ding_doc, ding_qa["reference_start_page"])
    report["ding"] = {
        "reference_start_detected": ding_qa["reference_start_detected"],
        "reference_start_page": ding_qa["reference_start_page"],
        "reference_pages": ding_qa["reference_pages"],
        "reference_continuation_preserved": ding_qa["reference_continuation_preserved"],
        "reference_role_gap_count": ding_qa["reference_role_gap_count"],
        "reference_state_orphan_count": ding_qa["reference_state_orphan_count"],
        "reference_false_positive_body_count": len(ding_fp),
        "body_fp_ids": ding_fp[:8],
        "semantic_roles_distribution": {},
    }
    from collections import Counter
    report["ding"]["semantic_roles_distribution"] = dict(Counter(
        (lp.get("semantic_role") or "?") for lp in ding_doc["logical_paragraphs"]))
    print("  Ding gap=%d orphan=%d fp=%d pages=%s"
          % (report["ding"]["reference_role_gap_count"],
             report["ding"]["reference_state_orphan_count"],
             report["ding"]["reference_false_positive_body_count"],
             report["ding"]["reference_pages"]))

    # ---- old 4D.2C semantic regression ----
    print("=== old 4D.2C semantic regression ===")
    d2c_doc = build_document_from_raw(DIAG_PDF, OLD4D2C, 19)
    apply_document_semantics(d2c_doc)
    d2c_pages = {p["page"]: p for p in d2c_doc["pages"]}
    d2c_qa = document_semantic_qa(sorted(d2c_pages), d2c_pages)
    d2c_body_fp = _main_body_fp(d2c_doc, d2c_qa["reference_start_page"])
    report["old_4d2c"] = {
        "reference_start_page": d2c_qa["reference_start_page"],
        "reference_pages": d2c_qa["reference_pages"],
        "reference_role_gap_count": d2c_qa["reference_role_gap_count"],
        "reference_state_orphan_count": d2c_qa["reference_state_orphan_count"],
        "body_misflagged_reference_count": len(d2c_body_fp),
        "body_misflagged_ids": d2c_body_fp[:8],
    }
    print("  old 4D.2C ref_start=%s ref_pages=%s gap=%d orphan=%d body_fp=%d"
          % (report["old_4d2c"]["reference_start_page"],
             report["old_4d2c"]["reference_pages"],
             report["old_4d2c"]["reference_role_gap_count"],
             report["old_4d2c"]["reference_state_orphan_count"],
             report["old_4d2c"]["body_misflagged_reference_count"]))

    report["decision"] = ("pass" if (
        report["ding"]["reference_role_gap_count"] == 0
        and report["ding"]["reference_state_orphan_count"] == 0
        and report["ding"]["reference_false_positive_body_count"] == 0
        and report["old_4d2c"]["body_misflagged_reference_count"] == 0)
        else "fail")
    _dump(OUT / "semantic_integration_report.json", report)
    print("=== semantic fixture: %s ===" % report["decision"].upper())
    return 0 if report["decision"] == "pass" else 2


if __name__ == "__main__":
    sys.exit(main())
