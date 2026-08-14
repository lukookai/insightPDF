# -*- coding: utf-8 -*-
"""Model-level QA for ordinary prose accidentally adopted by formulas."""
from __future__ import annotations

import json
from pathlib import Path

try:
    from formula_exclusivity_qa import (
        _adopted_component_is_formula_semantic,
    )
except ImportError:  # package import during focused tests
    from .formula_exclusivity_qa import (
        _adopted_component_is_formula_semantic,
    )


def formula_adopted_prose_qa(page, out_dir=None):
    """Audit adopted FormulaModel components without opening a PDF.

    Equation numbers and the narrow cases-condition allowlist are formula
    semantics.  Any other adopted natural-language fragment is a hard defect.
    """
    formula_records = []
    all_allowlisted = []
    all_plain = []
    for region in page.get("regions", []) or []:
        if region.get("type") != "formula":
            continue
        formula = region.get("payload") or {}
        formula_id = formula.get("formula_id") or region.get("region_id")
        adopted = [c for c in formula.get("components", []) or []
                   if c.get("adopted")]
        allowlisted = []
        plain = []
        for index, component in enumerate(adopted):
            allowed, reason = _adopted_component_is_formula_semantic(component)
            detail = {
                "formula_id": formula_id,
                "component_index": index,
                "text": component.get("text") or "",
                "bbox": component.get("bbox"),
                "reason": reason,
            }
            (allowlisted if allowed else plain).append(detail)
        all_allowlisted.extend(allowlisted)
        all_plain.extend(plain)
        formula_records.append({
            "formula_id": formula_id,
            "adopted_component_count": len(adopted),
            "allowlisted_adopted_component_count": len(allowlisted),
            "allowlisted_adopted_component_details": allowlisted,
            "adopted_plain_prose_count": len(plain),
            "adopted_plain_prose_details": plain,
            "formula_adopted_prose_passed": len(plain) == 0,
        })
    result = {
        "formula_count": len(formula_records),
        "formula_records": formula_records,
        "formula_adopted_component_count": (
            len(all_allowlisted) + len(all_plain)),
        "formula_adopted_allowlisted_component_count": len(all_allowlisted),
        "formula_adopted_allowlisted_component_details": all_allowlisted[:48],
        "formula_adopted_plain_prose_count": len(all_plain),
        "formula_adopted_plain_prose_details": all_plain[:48],
        "formula_adopted_prose_passed": len(all_plain) == 0,
    }
    if out_dir:
        path = Path(out_dir) / "formula_adopted_prose_qa.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(result, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    return result


__all__ = ["formula_adopted_prose_qa"]
