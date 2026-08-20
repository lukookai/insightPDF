# Inline Math Reconstruction Report

Decision: **PASS**

## Why the inverse-transform exponent was flattened

The legacy recovery path concatenated every inline formula span into one plain
string. It retained the minus and digit but discarded their structural
relationship to the base. The source PDF proves a superscript: the script font
is 7.472pt versus the 9.963pt base, its
baseline is raised by 3.486pt, and its left
edge is only 1.026pt after the base. The fixed
HTML therefore uses a real `<sup>` element rather than concatenating text.

## Where the lambda expression was lost

The source PDF exposes the prose word, italic base, smaller subscript,
comparison operator, and following prose as separate extraction fragments.
The old per-fragment prose filter retained only the fragment beginning with
`0`; the base/subscript/operator were lost before protected translation and
before Chromium. Task 4I merges co-baseline fragments while retaining the
legacy prose line as the geometry anchor, then protects one MathAtomGroup.
The subscript is 7.472pt versus the 9.963pt
base and its baseline is lowered by 2.491pt.

## Source → protected → HTML → final

The production model records base, superscript, subscript, operator, delimiter,
argument, and atom order. Translation sees an opaque group token. Rendering
expands that token to semantic spans plus `<sup>` / `<sub>`. QA confirms the
source and final atom sequences are identical and ordered. Task 4H remains in
force: the legacy calligraphic PUA base is normalized from verified source
font/glyph provenance to standard mathematical Unicode before group rendering;
the font chain itself is unchanged. The Task 4H regression gate is
`True`.

Reconstruction records: {'candidate_group_count': 11, 'reconstructed_group_count': 11, 'unresolved_group_count': 0}. Final DOM groups:
11. Final PDF group traces with ink: 11.

## Hard metrics

```json
{
  "math_atom_loss_count": 0,
  "math_atom_duplicate_count": 0,
  "math_atom_order_error_count": 0,
  "superscript_structure_loss_count": 0,
  "subscript_structure_loss_count": 0,
  "inline_math_target_missing_count": 0,
  "final_block_collision_count": 0,
  "production_special_case_count": 0,
  "source_text_slot_geometry_mutation_count": 0,
  "font_chain_modified_count": 0,
  "figure_anchor_mutation_count": 0,
  "table_anchor_mutation_count": 0,
  "formula_anchor_mutation_count": 0,
  "task4h_pua_regression_count": 0,
  "semantic_math_group_count": 11,
  "dom_superscript_raised_count": 3,
  "dom_subscript_lowered_count": 9,
  "final_pdf_math_group_ink_count": 11,
  "final_pdf_atom_loss_count": 0,
  "final_pdf_atom_order_error_count": 0
}
```

The QA-FIRST frozen artifact was red with
`math_atom_loss_count=3` and
`superscript_structure_loss_count=3`.
All final loss, duplicate, order, superscript, subscript, missing-target,
collision, and production-special-case metrics are zero. SourceTextSlot
geometry and paragraph font-family declarations are byte-identical. No figure,
table, formula crop, translation prompt, OCR, LaTeX reconstruction, or font
chain was modified. Production special-case count is
0.
