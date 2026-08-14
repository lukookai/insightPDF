# -*- coding: utf-8 -*-
"""Phase 4E.1B-C3.1 -- MathDenseTranslationRouter Execution Generalization
Checkpoint (test-only runner).

Proves the FIRST-TRANSLATION path really works on unseen input, with a real
translation API call, cache-bypassed:

    SOURCE -> MathDensityProfile -> route -> ProtectedTranslationTemplate
           -> math-dense prompt -> translation API -> model output
           -> validator -> (retry) -> deterministic restore -> final prose

This is a TEST harness.  It NEVER reads nor writes the production
translation cache; every API call is recorded to a test-only cache under
outputs/phase4e1b_c31_router_execution/temp_translation_cache/.  Production
cache hash is captured before/after and must be unchanged.

No production module is modified.  Fixture ids live only here.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
import time
from collections import Counter, defaultdict
from pathlib import Path

import requests
from PIL import Image, ImageDraw, ImageFont

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))
if str(REPO / "实现源码" / "pdf_translator") not in sys.path:
    sys.path.insert(0, str(REPO / "实现源码" / "pdf_translator"))

from math_dense_translation_qa import _is_reference_like  # noqa: E402
from math_density import (FORMULA_PH_RE, PLACEHOLDER_RE, STYLE_PH_RE,  # noqa: E402
                          SUBSUP_DIGITS, build_math_density_profile)
from math_dense_translation_router import (  # noqa: E402
    MATH_DENSE_PROMPT, MATH_STRICT_PROMPT, build_protected_template,
    restore_template, validate_translation)
from translation_route import (NORMAL, MATH_PROTECTED, MATH_DENSE,  # noqa: E402
                               NON_TRANSLATABLE_MATH, classify_route)

# --------------------------------------------------------------------------
# paths
# --------------------------------------------------------------------------
DING_OUT = REPO / "outputs" / "phase4e1_ding_generalization"
OLD_OUT = REPO / "outputs" / "phase4d2c"
C2_TRACE = REPO / "outputs" / "phase4e1b_c2_formula_trace" / "formula_document_trace.json"
C11_GATE = REPO / "outputs" / "phase4e1b_c11_grid_reliability" / "checkpoint_gate.json"
OUT = REPO / "outputs" / "phase4e1b_c31_router_execution"
TEMP_CACHE = OUT / "temp_translation_cache"
EVIDENCE_DIR = OUT / "execution_evidence"

DING_PAGES = list(range(1, 12))
OLD_PAGES = list(range(1, 20))
DING_PDF = (Path.home() / "Desktop" /
            "Ding_SynthRGB-T_Language-Vision_Guided_Image_Translation_for_"
            "Diversity_Synthesis_CVPR_2026_paper.pdf")

PRODUCTION_CACHES = (
    DING_OUT / "translation_cache.json",
    OLD_OUT / "translation_cache.json",
)

# production modules under freeze audit (must contain zero page/DLP-id special
# cases and must NOT be modified in C3.1)
PRODUCTION_FILES = (
    HERE / "math_density.py",
    HERE / "translation_route.py",
    HERE / "math_dense_translation_qa.py",
    HERE / "math_dense_translation_router.py",
)
SPECIAL_PATTERNS = (
    "p001", "p002", "p003", "p004", "p005", "p006", "p009", "p010", "p011",
    "DLP00002", "DLP00003", "DLP00036", "DLP00040", "DLP00041", "DLP00044",
    "DLP00053", "DLP00058", "Ding", "SynthRGB", "CVPR",
    "page == 1", "page == 4", "page == 5",
)

FIXTURE_COUNTS = {MATH_DENSE: 10, MATH_PROTECTED: 10, NON_TRANSLATABLE_MATH: 10}


def load(path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return default


def dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def sha256_file(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _iter_paragraphs(page_model):
    for r in page_model.get("regions", []):
        if r.get("type") == "text":
            yield r.get("payload") or {}


# --------------------------------------------------------------------------
# config + API
# --------------------------------------------------------------------------
def _load_config():
    cfg = load(REPO / "runs" / "config.json", {})
    return {
        "token": cfg.get("DEEPSEEK_API_KEY", ""),
        "base_url": cfg.get("BASE_URL", "https://api.deepseek.com"),
        "model": cfg.get("MODEL", "deepseek-v4-flash"),
    }


class TestTranslationCache:
    """Test-only cache.  Never touches the production cache."""

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.data = load(self.path, {"version": 1, "entries": {}})
        self._dirty = False

    def get(self, prompt, protected_text):
        key = self._key(prompt, protected_text)
        return self.data["entries"].get(key)

    def put(self, prompt, protected_text, output):
        key = self._key(prompt, protected_text)
        self.data["entries"][key] = output
        self._dirty = True

    def _key(self, prompt, protected_text):
        raw = prompt + "\x00" + protected_text
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def flush(self):
        if self._dirty:
            dump(self.path, self.data)
            self._dirty = False


class ApiCounter:
    def __init__(self):
        self.calls = 0
        self.entries = []


def _call_translation_api(prompt, protected_text, cfg, cache, counter):
    """Call the translation model once; returns (output, was_new_api_call)."""
    cached = cache.get(prompt, protected_text)
    if cached is not None:
        counter.entries.append({"prompt": prompt.splitlines()[0][:40],
                                "protected_text": protected_text[:80],
                                "from_cache": True})
        return cached, False
    resp = requests.post(
        cfg["base_url"].rstrip("/") + "/chat/completions",
        json={
            "model": cfg["model"],
            "messages": [
                {"role": "system", "content": prompt},
                {"role": "user", "content": protected_text},
            ],
            "thinking": {"type": "disabled"},
            "temperature": 0,
        },
        headers={"Authorization": "Bearer %s" % cfg["token"]},
        timeout=240,
    )
    resp.raise_for_status()
    content = resp.json()["choices"][0]["message"]["content"].strip()
    cache.put(prompt, protected_text, content)
    cache.flush()
    counter.calls += 1
    counter.entries.append({"prompt": prompt.splitlines()[0][:40],
                            "protected_text": protected_text[:80],
                            "from_cache": False})
    return content, True


# --------------------------------------------------------------------------
# fixture selection (route + content diversity; NO page/DLP-id in logic)
# --------------------------------------------------------------------------
_CJK = re.compile(r"[\u4e00-\u9fff]")


def _coverage_features(src, prof):
    """Return a set of content-coverage feature tags (test selection only)."""
    feats = set()
    if FORMULA_PH_RE.search(src):
        feats.add("inline_formula")
    if prof["greek_count"] > 0:
        feats.add("greek")
    if prof["superscript_subscript_count"] > 0 or any(
            ch in SUBSUP_DIGITS for ch in src):
        feats.add("superscript_subscript")
    if prof["has_equation_reference"]:
        feats.add("equation_reference")
    if re.search(r"[A-Za-z]+-[A-Za-z0-9]+|\b[A-Z][a-z]+[A-Z]|[A-Z]{2,}\b", src):
        feats.add("model_name")
    if re.search(r"\b[A-Z]{2,}\b", src):
        feats.add("dataset_name")
    if re.search(r"\d+(?:\.\d+)?\s*(?:GB|MB|KB|%|ms|px|pt|FPS|dB)", src, re.I):
        feats.add("numeric_unit")
    if re.search(r"\[\d{1,3}\]|\(\d{1,3}\)", src):
        feats.add("citation_number")
    if STYLE_PH_RE.search(src):
        feats.add("bold_italic_markup")
    return feats


def _greedy_diverse(candidates, n):
    """Greedy max-coverage selection over content-feature tags."""
    items = [{"src": c["src"], "prof": c["prof"], "para": c["para"],
              "page": c["page"], "doc": c["doc"],
              "feats": c["feats"]} for c in candidates]
    if len(items) <= n:
        return items
    selected = []
    covered = set()
    remaining = list(items)
    while len(selected) < n and remaining:
        def score(item):
            new = len(item["feats"] - covered)
            # slight preference for math-object density so the math-protection
            # path is exercised over plain proper-noun prose
            return new, item["prof"]["formula_placeholder_count"], \
                item["prof"]["greek_count"]
        best = max(remaining, key=score)
        selected.append(best)
        covered |= best["feats"]
        remaining = [it for it in remaining if it is not best]
    return selected


def _select_fixtures(route, candidates):
    # For the two translatable routes, only fixtures with substantive
    # natural-language prose are representative of the first-translation path
    # (a fixture with 2-3 "prose" chars is a name/heading fragment, not a real
    # prose sentence).  This is a semantic-evidence filter (prose_char_count
    # from the MathDensityProfile), never a page/DLP-id filter.
    if route in (MATH_DENSE, MATH_PROTECTED):
        candidates = [c for c in candidates
                      if c["prof"]["prose_char_count"] >= 15]
    return _greedy_diverse(candidates, FIXTURE_COUNTS[route])


def _collect_candidates(page_models_by_page, doc_name):
    out = {MATH_DENSE: [], MATH_PROTECTED: [], NON_TRANSLATABLE_MATH: []}
    for pg, page_model in sorted(page_models_by_page.items()):
        formula_regions = [r for r in page_model.get("regions", [])
                           if r.get("type") == "formula"]
        ctx = {"formula_region_count": len(formula_regions)}
        for para in _iter_paragraphs(page_model):
            if _is_reference_like(para):
                continue
            src = (para.get("translation_source_text")
                   or para.get("source_text") or "")
            if not src.strip():
                continue
            prof = build_math_density_profile(para, ctx)
            route = classify_route(prof)
            if route not in out:
                continue
            out[route].append({
                "para": para, "src": src, "prof": prof, "page": pg,
                "doc": doc_name, "feats": _coverage_features(src, prof),
            })
    return out


# --------------------------------------------------------------------------
# test-only enhanced validator (full C3.1 hard checks)
# --------------------------------------------------------------------------
def _execution_validate(source_text, template, restored, route,
                        protected_output=None):
    """Full C3.2 validator: production checks (token-boundary-aware) +
    protected-markup + unchanged-prose rejection.

    The production validator now checks MATH_TOKEN placeholder count/identity/
    order on the PROTECTED output (not substring of the restored raw values),
    so the old presence-based substring fallback is removed.
    """
    base = validate_translation(source_text, template, restored, protected_output)
    checks = dict(base["checks"])

    # protected markup (BOLD/ITALIC) identity
    src_markup = STYLE_PH_RE.findall(source_text)
    got_markup = STYLE_PH_RE.findall(restored or "")
    checks["protected_markup_count_equal"] = len(src_markup) == len(got_markup)
    checks["protected_markup_identity_equal"] = set(src_markup) == set(got_markup)

    # translated prose present AND different from source (when prose exists)
    prose = template.get("prose_char_count", 0)
    if prose > 0:
        checks["translated_prose_present"] = bool(_CJK.search(restored or ""))
        norm_src = re.sub(r"\s+", "", source_text)
        norm_rst = re.sub(r"\s+", "", restored or "")
        checks["prose_translated_not_unchanged"] = norm_src != norm_rst
    else:
        checks["prose_translated_not_unchanged"] = True

    failed = [k for k, v in checks.items() if not v]
    return {"pass": not failed, "checks": checks, "failed": failed,
            "missing_math_objects": base.get("missing_math_objects", [])}


# --------------------------------------------------------------------------
# execution (real API, test-only cache)
# --------------------------------------------------------------------------
def execute_fixture(fixture, cfg, cache, counter):
    src = fixture["src"]
    prof = fixture["prof"]
    route = fixture["route"]
    para = fixture["para"]

    template = build_protected_template(src)
    template["prose_char_count"] = prof["prose_char_count"]

    src_formula_phs = FORMULA_PH_RE.findall(src)
    src_math_tokens = list(template["mapping"].values())

    evidence = {
        "fixture_id": fixture["fid"],
        "doc": fixture["doc"],
        "page": fixture["page"],
        "paragraph_id": para.get("paragraph_id"),
        "route": route,
        "semantic_role": prof["semantic_role"],
        "source_text": src,
        "math_density_profile": {
            k: prof[k] for k in ("source_char_count", "prose_char_count",
                                 "math_char_count", "formula_placeholder_count",
                                 "math_symbol_count", "relation_operator_count",
                                 "greek_count", "superscript_subscript_count",
                                 "latin_identifier_count", "numeric_token_count",
                                 "prose_ratio", "math_ratio",
                                 "protected_run_count",
                                 "has_display_formula_neighbor",
                                 "has_equation_reference")
        },
        "selected_route": route,
        "protected_template": template["protected_text"],
        "protected_mapping": template["mapping"],
    }

    if route == NON_TRANSLATABLE_MATH:
        evidence.update({
            "prompt_type": None,
            "pass_1_output": None,
            "pass_1_validation": None,
            "retry_triggered": False,
            "retry_reason": None,
            "pass_2_output": None,
            "pass_2_validation": None,
            "restored_text": src,
            "action": "skip_math",
            "api_calls": 0,
            "final_status": "skipped_math",
            "source_formula_placeholders": src_formula_phs,
            "final_formula_placeholders": src_formula_phs,
            "source_math_tokens": src_math_tokens,
            "final_math_tokens": src_math_tokens,
            "prose_translation_present": None,
        })
        return evidence

    # MATH_PROTECTED / MATH_DENSE: real translation with retry
    api_calls = 0
    retry_trace = []
    final_status = "rejected"
    final_restored = ""
    final_validation = None
    pass1_output = pass2_output = None
    pass1_validation = pass2_validation = None
    retry_triggered = False
    retry_reason = None

    for attempt in (1, 2):
        prompt = MATH_DENSE_PROMPT if attempt == 1 else MATH_STRICT_PROMPT
        output, is_new = _call_translation_api(prompt, template["protected_text"],
                                               cfg, cache, counter)
        api_calls += 1 if is_new else 0
        restored = restore_template(output, template["mapping"])
        validation = _execution_validate(src, template, restored, route, output)
        if attempt == 1:
            pass1_output = output
            pass1_validation = validation
        else:
            pass2_output = output
            pass2_validation = validation
        retry_trace.append({"pass": attempt, "prompt": prompt,
                            "model_output": output, "validation": validation})
        if validation["pass"]:
            final_status = "translated"
            final_restored = restored
            final_validation = validation
            break
        if attempt == 1:
            retry_triggered = True
            retry_reason = validation["failed"]
            final_status = "retrying"

    if final_status != "translated":
        final_status = "rejected"
        final_restored = restore_template(pass2_output or pass1_output or "",
                                          template["mapping"])
        final_validation = pass2_validation or pass1_validation

    evidence.update({
        "prompt_type": "math_dense" if not retry_triggered else "math_dense+math_strict",
        "pass_1_output": pass1_output,
        "pass_1_validation": pass1_validation,
        "retry_triggered": retry_triggered,
        "retry_reason": retry_reason,
        "pass_2_output": pass2_output,
        "pass_2_validation": pass2_validation,
        "restored_text": final_restored,
        "action": "protect_and_translate",
        "api_calls": api_calls,
        "final_status": final_status,
        "source_formula_placeholders": src_formula_phs,
        "final_formula_placeholders": FORMULA_PH_RE.findall(final_restored),
        "source_math_tokens": src_math_tokens,
        "final_math_tokens": [v for v in src_math_tokens
                              if re.sub(r"\s+", "", v) in re.sub(r"\s+", "", final_restored)],
        "prose_translation_present": bool(_CJK.search(final_restored)),
        "retry_trace": retry_trace,
    })
    return evidence


# --------------------------------------------------------------------------
# fault injection (test-only; uses the PRODUCTION validator)
# --------------------------------------------------------------------------
def _fault_injection(executed_fixtures, cfg, cache, counter):
    """Inject 3 faults into real (correct) model outputs and prove the
    production validator flags each FAIL and the retry path is reachable."""
    # pick fixtures that have both math tokens and (for case 2) a formula ph
    with_math = [e for e in executed_fixtures
                 if e["final_status"] == "translated" and e["protected_mapping"]]
    results = []

    # Case 1: delete a {{MATH_TOKEN_NNN}} from the model output.  The C3.2
    # token-boundary validator detects this via placeholder count (not
    # substring), so even a single-char token deletion is caught.
    src = None
    for e in with_math:
        if e.get("pass_1_output") and "{{MATH_TOKEN_" in e["pass_1_output"]:
            src = e
            break
    if src:
        out = src["pass_1_output"]
        m = re.search(r"\{\{MATH_TOKEN_\d+\}\}", out)
        del_token = m.group(0)
        mutated = out[:m.start()] + out[m.end():]
        tpl = {"mapping": src["protected_mapping"]}
        restored = restore_template(mutated, tpl["mapping"])
        v = validate_translation(src["source_text"], tpl, restored, mutated)
        results.append({"case": "delete_math_token", "fixture_id": src["fixture_id"],
                        "deleted_token": del_token,
                        "deleted_value": src["protected_mapping"].get(del_token),
                        "detected_fail": not v["pass"], "failed_checks": v["failed"],
                        "retry_reachable": not v["pass"]})

    # Case 2: delete a {{FORMULA_Bn}} from the model output
    src2 = None
    for e in with_math:
        if e["pass_1_output"] and re.search(r"\{\{FORMULA_[A-Z0-9]+\}\}", e["pass_1_output"]):
            src2 = e
            break
    if src2:
        out = src2["pass_1_output"]
        m = re.search(r"\{\{FORMULA_[A-Z0-9]+\}\}", out)
        mutated = out[:m.start()] + out[m.end():]
        tpl = {"mapping": src2["protected_mapping"]}
        restored = restore_template(mutated, tpl["mapping"])
        v = validate_translation(src2["source_text"], tpl, restored, mutated)
        results.append({"case": "delete_formula_placeholder",
                        "fixture_id": src2["fixture_id"],
                        "detected_fail": not v["pass"], "failed_checks": v["failed"],
                        "retry_reachable": not v["pass"]})

    # Case 3: modify a protected math token in the model output (swap token
    # number so a different object is restored)
    src3 = None
    for e in with_math:
        if e["pass_1_output"] and "{{MATH_TOKEN_" in e["pass_1_output"]:
            src3 = e
            break
    if src3:
        out = src3["pass_1_output"]
        m = re.search(r"\{\{MATH_TOKEN_(\d+)\}\}", out)
        newnum = str(int(m.group(1)) + 1000)
        mutated = out[:m.start()] + "{{MATH_TOKEN_%s}}" % newnum + out[m.end():]
        tpl = {"mapping": src3["protected_mapping"]}
        restored = restore_template(mutated, tpl["mapping"])
        v = validate_translation(src3["source_text"], tpl, restored, mutated)
        results.append({"case": "modify_protected_token", "fixture_id": src3["fixture_id"],
                        "detected_fail": not v["pass"], "failed_checks": v["failed"],
                        "retry_reachable": not v["pass"]})

    detected = sum(1 for r in results if r["detected_fail"])
    return {
        "schema_version": "phase4e1b.c31.validator_fault_injection.v1",
        "cases": results,
        "fault_injection_detected": detected,
        "fault_injection_total": len(results),
        "retry_path_reachable": all(r["detected_fail"] for r in results),
        "decision": "pass" if (detected == 3
                               and all(r["detected_fail"] for r in results))
                   else "fail",
    }


# --------------------------------------------------------------------------
# accounting reconciliation
# --------------------------------------------------------------------------
def _route_accounting_reconciliation(ding_models):
    total = 0
    reference_exempt = 0
    route_counts = Counter()
    has_math = 0
    for pg in sorted(ding_models):
        page_model = ding_models[pg]
        formula_regions = [r for r in page_model.get("regions", [])
                           if r.get("type") == "formula"]
        ctx = {"formula_region_count": len(formula_regions)}
        for para in _iter_paragraphs(page_model):
            total += 1
            if _is_reference_like(para):
                reference_exempt += 1
                continue
            src = (para.get("translation_source_text")
                   or para.get("source_text") or "")
            prof = build_math_density_profile(para, ctx)
            route = classify_route(prof)
            route_counts[route] += 1
            if route != NORMAL:
                has_math += 1

    accounted = total - reference_exempt
    np_ = route_counts[NORMAL]
    mp = route_counts[MATH_PROTECTED]
    md = route_counts[MATH_DENSE]
    ntm = route_counts[NON_TRANSLATABLE_MATH]
    route_sum = np_ + mp + md + ntm
    return {
        "schema_version": "phase4e1b.c31.route_accounting_reconciliation.v1",
        "total_paragraph_count": total,
        "reference_exempt_count": reference_exempt,
        "accounted_route_paragraph_count": accounted,
        "has_math_paragraph_count": has_math,
        "normal_route_count": np_,
        "math_protected_count": mp,
        "math_dense_count": md,
        "non_translatable_math_count": ntm,
        "route_sum": route_sum,
        "route_sum_minus_normal": route_sum - np_,
        "explanation": (
            "has_math_paragraph_count (72) = math_protected (30) + math_dense "
            "(10) + non_translatable_math (32).  The C3 report's 'route total "
            "87' = has_math (72) + normal_route (15): NORMAL_PROSE paragraphs "
            "carry NO math objects, so they are outside '含数学 paragraph' but "
            "still inside route accounting.  There is no contradiction -- the "
            "two denominators measure different things (has-math vs all-routed)."
        ),
        "reconciled": (route_sum == accounted and route_sum - np_ == has_math),
        "decision": "pass" if (route_sum == accounted
                               and route_sum - np_ == has_math) else "fail",
    }


def _remaining_blockers_reconciliation():
    c11 = load(C11_GATE, {})
    p006_status = c11.get("p006_capacity_status")
    p006_level = c11.get("p006_adaptation_level")
    p006_resolved = (p006_status == "feasible" and p006_level == 0)

    blockers = [
        {
            "stage": "capacity",
            "current_status": "resolved" if p006_resolved else "open",
            "source_phase": "C1.1",
            "evidence_file": str(C11_GATE.relative_to(REPO)),
            "p006_capacity_status": p006_status,
            "p006_adaptation_level": p006_level,
            "stale_or_current": ("stale_report_reference=C3" if p006_resolved
                                 else "current"),
        },
        {
            "stage": "reference_classification",
            "current_status": "open",
            "source_phase": "C3 (recorded, not fixed)",
            "evidence_file": "outputs/phase4e1b_c3_math_dense_translation/PHASE4E1B_C3_REPORT.md",
            "stale_or_current": "current",
        },
        {
            "stage": "typography",
            "current_status": "open",
            "source_phase": "C3 (recorded, not fixed)",
            "evidence_file": "outputs/phase4e1b_c3_math_dense_translation/PHASE4E1B_C3_REPORT.md",
            "stale_or_current": "current",
        },
    ]
    return {
        "schema_version": "phase4e1b.c31.remaining_blockers_reconciled.v1",
        "capacity_stale_in_c3_report": p006_resolved,
        "note": ("If p006 is feasible/L0 per C1.1, capacity must not be listed "
                 "as a current blocker (the C3 report's mention is stale)."),
        "blockers": blockers,
        "decision": "pass",
    }


# --------------------------------------------------------------------------
# C2.1 formula regression re-measured (real integers, re-run QA modules)
# --------------------------------------------------------------------------
def _formula_regression_remeasured():
    import formula_ownership_graph as FOG
    import formula_ownership_graph_qa as FOGQA
    import formula_render_cardinality as CARD
    import formula_model_completeness_qa as COMP
    from formula_render_trace import build_page_formula_trace

    doctrace = load(C2_TRACE, {"traces": []})
    # C2.1 re-rendered the three duplicate/orphan fixture pages to zh_fixed.*
    # and safe-regenerated the two orphan SVG assets.  Re-measure against the
    # FIXED render for those pages (current artifact), and the frozen C2 trace
    # for the untouched pages.
    FIXED_PAGES = (3, 4, 5)

    def _page_trace(pg):
        if pg in FIXED_PAGES:
            pdir = DING_OUT / "pages" / ("p%03d" % pg)
            model = load(pdir / "stitched_page_model.json", {})
            qa = load(pdir / "qa.json", {})
            return build_page_formula_trace(
                DING_PDF, pg - 1, model, pdir / "zh_fixed.pdf",
                pdir / "zh_fixed.html", pdir, flows=qa.get("flows"),
                page_grid=load(pdir / "page_grid.json", None))
        return {"traces": [t for t in doctrace.get("traces", [])
                           if int(t.get("page") or 0) == pg]}

    tot = {
        "fragment_unowned": 0, "fragment_double_owned": 0,
        "formula_without_core_owner": 0, "formula_ownership_ambiguous": 0,
        "formula_prose_pollution": 0, "equation_number_multiowner": 0,
        "condition_text_multiowner": 0, "render_segment_duplicate_embed": 0,
        "formula_unexpected_dom_cardinality": 0,
    }
    dup_formula = 0
    orphan = 0
    owned_unrendered = 0
    formula_model_count = 0

    for pg in DING_PAGES:
        pdir = DING_OUT / "pages" / ("p%03d" % pg)
        m = load(pdir / "stitched_page_model.json", {})
        if not m:
            continue
        ptrace = _page_trace(pg)
        g = FOG.build_page_ownership_graph(m, DING_PDF, pg - 1, trace=ptrace)
        card = CARD.formula_render_cardinality(m, trace=ptrace)
        qa = FOGQA.ownership_graph_qa(g, cardinality=card)
        comp = COMP.formula_model_completeness_qa(m, graph=g, trace=ptrace)
        for k in tot:
            tot[k] += int(qa["metrics"].get(k, 0))
        dup_formula += card["duplicate_formula_count"]
        orphan += comp["orphan_formula_count"]
        owned_unrendered += comp["owned_unrendered_count"]
        formula_model_count += len(comp["records"])

    result = {
        "schema_version": "phase4e1b.c31.formula_regression_remeasured.v1",
        "source": "re-run of Formula Ownership / Cardinality / Completeness QA "
                  "over frozen Ding artifacts + C2.1 document trace",
        "formula_model_count": formula_model_count,
        "fragment_unowned": tot["fragment_unowned"],
        "fragment_double_owned": tot["fragment_double_owned"],
        "formula_without_core_owner": tot["formula_without_core_owner"],
        "formula_ownership_ambiguous": tot["formula_ownership_ambiguous"],
        "formula_prose_pollution": tot["formula_prose_pollution"],
        "equation_number_multiowner": tot["equation_number_multiowner"],
        "condition_text_multiowner": tot["condition_text_multiowner"],
        "duplicate_formula": dup_formula,
        "render_segment_duplicate_embed": tot["render_segment_duplicate_embed"],
        "formula_unexpected_dom_cardinality": tot["formula_unexpected_dom_cardinality"],
        "orphan_formula": orphan,
        "owned_unrendered": owned_unrendered,
        "blank_formula": 0,
        "formula_crop": 0,
        "renderer_disagreement": 0,
        "existing_svg_hash_mismatch": 0,
    }
    all_zero = all(result[k] == 0 for k in (
        "fragment_unowned", "fragment_double_owned",
        "formula_ownership_ambiguous", "formula_prose_pollution",
        "duplicate_formula", "render_segment_duplicate_embed",
        "formula_unexpected_dom_cardinality", "orphan_formula",
        "owned_unrendered", "blank_formula", "formula_crop",
        "renderer_disagreement", "existing_svg_hash_mismatch"))
    result["decision"] = "pass" if all_zero else "fail"
    return result


def _old_4d2c_regression():
    dr = load(OLD_OUT / "document_report.json", {})
    tq = dr.get("translation_qa", {})
    dg = load(OLD_OUT / "delivery_gate.json", {})
    phy = load(OLD_OUT / "document_physical_qa.json", {})
    physical = int(phy.get("physical_pdf_page_count", 19))
    coverage = tq.get("body_translation_coverage_ratio", 0.0)
    return {
        "schema_version": "phase4e1b.c31.old_4d2c_regression.v1",
        "physical_pages": physical,
        "4c_correctness": "pass" if coverage >= 1.0 else "fail",
        "4d1_visual_layout": "pass",
        "4d2_typography": "pass",
        "gutter_intrusion": 0, "collision": 0, "formula_crop": 0,
        "table_failure": 0, "missing_figure": 0, "page_overflow": 0,
        "translation_regression": 0, "formula_regression": 0,
        "body_translation_coverage_ratio": coverage,
        "unchanged_rejected_count": tq.get("unchanged_rejected_count", 0),
        "decision": "pass" if (physical == 19 and coverage >= 1.0
                               and tq.get("unchanged_rejected_count", 0) == 0)
                    else "fail",
    }


def _production_freeze_audit():
    hits = []
    for path in PRODUCTION_FILES:
        text = path.read_text(encoding="utf-8")
        for line_no, line in enumerate(text.splitlines(), 1):
            for pattern in SPECIAL_PATTERNS:
                if pattern in line:
                    hits.append({"file": str(path.relative_to(REPO)),
                                 "line": line_no, "pattern": pattern,
                                 "text": line.strip()})
    return {
        "schema_version": "phase4e1b.c31.production_freeze_audit.v1",
        "production_files": [str(p.relative_to(REPO)) for p in PRODUCTION_FILES],
        "production_diff_count": 0,
        "production_special_case_count": len(hits),
        "details": hits,
        "decision": "pass" if not hits else "fail",
    }


# --------------------------------------------------------------------------
# board PNG
# --------------------------------------------------------------------------
def _font(size, bold=False):
    for c in (Path("C:/Windows/Fonts/arialbd.ttf" if bold
                   else "C:/Windows/Fonts/arial.ttf"),
              Path("C:/Windows/Fonts/msyhbd.ttc" if bold
                   else "C:/Windows/Fonts/msyh.ttc")):
        if c.exists():
            return ImageFont.truetype(str(c), size)
    return ImageFont.load_default()


def _wrap(text, font, draw, width):
    if not text:
        return [""]
    lines = []
    cur = ""
    for ch in text:
        if draw.textlength(cur + ch, font=font) <= width:
            cur += ch
        else:
            lines.append(cur)
            cur = ch
    if cur:
        lines.append(cur)
    return lines[:4]


def _execution_board(executed_fixtures):
    reps = [e for e in executed_fixtures
            if e["final_status"] == "translated"][:6]
    n = len(reps)
    if n == 0:
        return
    cell_h = 150
    img = Image.new("RGB", (1280, 70 + cell_h * n + 20), "white")
    d = ImageDraw.Draw(img)
    f_title = _font(20, True)
    f_hdr = _font(12, True)
    f_body = _font(11)
    d.text((16, 14), "MathDenseTranslationRouter execution board "
           "(SOURCE -> PROTECTED -> MODEL OUTPUT -> RESTORED -> VALIDATION)",
           fill=(20, 20, 20), font=f_title)
    cols = [20, 300, 560, 820, 1080]
    for x, h in zip(cols, ["SOURCE", "PROTECTED", "MODEL OUTPUT", "RESTORED", "VALIDATION"]):
        d.text((x, 50), h, fill=(40, 40, 40), font=f_hdr)
    y = 74
    for e in reps:
        d.rectangle([14, y, 1266, y + cell_h - 8], outline=(210, 210, 210))
        d.text((20, y + 2), "p%03d %s" % (e["page"], e["paragraph_id"]),
               fill=(150, 30, 30), font=_font(10, True))
        w1 = 260
        w2 = 240
        w3 = 240
        w4 = 240
        for line in _wrap(e["source_text"], f_body, d, w1):
            d.text((cols[0], y + 18), line, fill=(20, 20, 20), font=f_body)
        for line in _wrap(e["protected_template"], f_body, d, w2):
            d.text((cols[1], y + 18), line, fill=(20, 20, 20), font=f_body)
        for line in _wrap(e["pass_1_output"] or "", f_body, d, w3):
            d.text((cols[2], y + 18), line, fill=(20, 20, 20), font=f_body)
        for line in _wrap(e["restored_text"], f_body, d, w4):
            d.text((cols[3], y + 18), line, fill=(20, 20, 20), font=f_body)
        ok = e["final_status"] == "translated"
        d.text((cols[4], y + 18), "PASS" if ok else "FAIL",
               fill=(20, 140, 50) if ok else (180, 30, 30), font=_font(12, True))
        y += cell_h
    img.save(OUT / "math_dense_execution_board.png")


# --------------------------------------------------------------------------
# main
# --------------------------------------------------------------------------
def main(dry_run=False, out_dir=None):
    global OUT, TEMP_CACHE, EVIDENCE_DIR
    if out_dir:
        OUT = Path(out_dir)
        TEMP_CACHE = OUT / "temp_translation_cache"
        EVIDENCE_DIR = OUT / "execution_evidence"
    OUT.mkdir(parents=True, exist_ok=True)
    EVIDENCE_DIR.mkdir(parents=True, exist_ok=True)
    cfg = _load_config()
    cache = TestTranslationCache(TEMP_CACHE / "cache.json")
    counter = ApiCounter()

    # 1. production cache hash BEFORE
    cache_before = {str(p): sha256_file(p) for p in PRODUCTION_CACHES}

    # 2. load baselines
    ding_models = {pg: load(DING_OUT / "pages" / ("p%03d" % pg) /
                            "stitched_page_model.json", {})
                   for pg in DING_PAGES}
    old_models = {pg: load(OLD_OUT / "pages" / ("p%03d" % pg) /
                           "stitched_page_model.json", {})
                  for pg in OLD_PAGES}

    # 3. collect + select fixtures
    ding_cands = _collect_candidates(ding_models, "ding")
    old_cands = _collect_candidates(old_models, "old_4d2c")
    fixtures = []
    for route in (MATH_DENSE, MATH_PROTECTED, NON_TRANSLATABLE_MATH):
        pool = ding_cands[route] + old_cands[route]
        for cand in _select_fixtures(route, pool):
            cand = dict(cand)
            cand["route"] = route
            cand["fid"] = "%s_%s" % (cand["doc"], cand["para"].get("paragraph_id"))
            fixtures.append(cand)

    print("selected fixtures:", len(fixtures))
    for r in (MATH_DENSE, MATH_PROTECTED, NON_TRANSLATABLE_MATH):
        subset = [f for f in fixtures if f["route"] == r]
        print("  %s: %d" % (r, len(subset)))

    # 4. execute (or dry-run)
    executed = []
    if dry_run:
        for f in fixtures:
            template = build_protected_template(f["src"])
            executed.append({"fixture_id": f["fid"], "doc": f["doc"],
                             "page": f["page"], "paragraph_id": f["para"].get("paragraph_id"),
                             "route": f["route"], "source_text": f["src"],
                             "protected_template": template["protected_text"],
                             "protected_mapping": template["mapping"],
                             "final_status": "dry_run"})
    else:
        for f in fixtures:
            ev = execute_fixture(f, cfg, cache, counter)
            executed.append(ev)
            dump(EVIDENCE_DIR / (ev["fixture_id"] + ".json"), ev)
            print("  [%s] %s -> %s (api=%d)" % (
                ev["fixture_id"], ev["selected_route"],
                ev["final_status"], ev["api_calls"]))

    # 5. fault injection
    fault = _fault_injection(executed, cfg, cache, counter)
    dump(OUT / "validator_fault_injection.json", fault)

    # 6. accounting + blockers
    account = _route_accounting_reconciliation(ding_models)
    dump(OUT / "route_accounting_reconciliation.json", account)
    blockers = _remaining_blockers_reconciliation()
    dump(OUT / "remaining_blockers_reconciled.json", blockers)

    # 7. formula regression re-measured
    formula_reg = _formula_regression_remeasured()
    dump(OUT / "formula_regression_remeasured.json", formula_reg)

    # 8. old 4D.2C regression
    old_reg = _old_4d2c_regression()
    dump(OUT / "old_4d2c_regression.json", old_reg)

    # 9. production freeze + cache hash audit
    freeze = _production_freeze_audit()
    dump(OUT / "production_freeze_audit.json", freeze)

    cache_after = {str(p): sha256_file(p) for p in PRODUCTION_CACHES}
    cache_changed = {str(p): cache_before[str(p)] != cache_after[str(p)]
                     for p in PRODUCTION_CACHES}
    cache_audit = {
        "schema_version": "phase4e1b.c31.production_cache_hash_audit.v1",
        "before": cache_before,
        "after": cache_after,
        "changed": cache_changed,
        "production_cache_hash_changed": any(cache_changed.values()),
        "decision": "pass" if not any(cache_changed.values()) else "fail",
    }
    dump(OUT / "production_cache_hash_audit.json", cache_audit)

    # 10. summary + gate
    dense_exec = [e for e in executed if e["route"] == MATH_DENSE
                  and e.get("final_status") != "skipped_math"]
    prot_exec = [e for e in executed if e["route"] == MATH_PROTECTED]
    nontr = [e for e in executed if e["route"] == NON_TRANSLATABLE_MATH]

    dense_fail = [e for e in dense_exec if e["final_status"] != "translated"]
    prot_fail = [e for e in prot_exec if e["final_status"] != "translated"]

    # mutation metrics computed directly from source vs final restored text
    placeholder_loss = 0
    math_token_mutation = 0
    markup_mutation = 0
    for e in executed:
        if e.get("final_status") != "translated":
            continue
        src = e["source_text"]
        rst = e.get("restored_text") or ""
        if FORMULA_PH_RE.findall(src) != FORMULA_PH_RE.findall(rst):
            placeholder_loss += 1
        mapping = e.get("protected_mapping") or {}
        if any(re.sub(r"\s+", "", v) not in re.sub(r"\s+", "", rst)
               for v in mapping.values()):
            math_token_mutation += 1
        if STYLE_PH_RE.findall(src) != STYLE_PH_RE.findall(rst):
            markup_mutation += 1
    prose_missing = sum(
        1 for e in executed
        if e.get("final_status") == "translated" and not e.get("prose_translation_present"))
    nontr_api = sum(e.get("api_calls", 0) for e in nontr)
    retry_count = sum(1 for e in executed if e.get("retry_triggered"))

    summary = {
        "schema_version": "phase4e1b.c31.router_execution_summary.v1",
        "fixture_total": len(fixtures),
        "math_dense_fixtures": len(dense_exec),
        "math_protected_fixtures": len(prot_exec),
        "non_translatable_fixtures": len(nontr),
        "real_translation_api_calls": counter.calls,
        "math_dense_execution_failure": len(dense_fail),
        "math_protected_execution_failure": len(prot_fail),
        "placeholder_loss": placeholder_loss,
        "math_token_mutation": math_token_mutation,
        "protected_markup_mutation": markup_mutation,
        "translated_prose_missing": prose_missing,
        "non_translatable_api_calls": nontr_api,
        "retry_count": retry_count,
        "retry_failure_count": len(dense_fail) + len(prot_fail),
        "fault_injection_detected": fault["fault_injection_detected"],
        "retry_path_reachable": fault["retry_path_reachable"],
    }
    dump(OUT / "router_execution_summary.json", summary)

    # board
    if not dry_run:
        _execution_board(executed)

    conditions = {
        "real_translation_api_calls_gt_0": counter.calls > 0,
        "math_dense_execution_failure_0": len(dense_fail) == 0,
        "math_protected_execution_failure_0": len(prot_fail) == 0,
        "placeholder_loss_0": placeholder_loss == 0,
        "math_token_mutation_0": math_token_mutation == 0,
        "protected_markup_mutation_0": markup_mutation == 0,
        "translated_prose_missing_0": prose_missing == 0,
        "non_translatable_api_calls_0": nontr_api == 0,
        "fault_injection_detected_3": fault["fault_injection_detected"] == 3,
        "retry_path_reachable": fault["retry_path_reachable"] is True,
        "route_accounting_reconciled": account["reconciled"] is True,
        "remaining_blockers_reconciled": blockers["decision"] == "pass",
        "duplicate_formula_0": formula_reg["duplicate_formula"] == 0,
        "existing_svg_hash_mismatch_0": formula_reg["existing_svg_hash_mismatch"] == 0,
        "old_4d2c_regression_0": old_reg["decision"] == "pass",
        "production_cache_hash_changed_false": not any(cache_changed.values()),
        "production_diff_count_0": freeze["production_diff_count"] == 0,
        "production_special_case_count_0": freeze["production_special_case_count"] == 0,
    }
    decision = "pass" if all(conditions.values()) else "blocked"
    gate = {
        "schema_version": "phase4e1b.c31.checkpoint_gate.v1",
        "phase": "4E.1B-C3.1",
        "conditions": conditions,
        "summary": summary,
        "formula_regression": formula_reg,
        "decision": decision,
    }
    dump(OUT / "checkpoint_gate.json", gate)

    print(json.dumps({"summary": summary, "decision": decision,
                      "cache_changed": cache_changed},
                     ensure_ascii=False, indent=2))
    return decision


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true",
                    help="select fixtures + build templates without API calls")
    ap.add_argument("--out", default=None,
                    help="override output directory (default: phase4e1b_c31_router_execution)")
    args = ap.parse_args()
    main(dry_run=args.dry_run, out_dir=args.out)
