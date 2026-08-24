# Full Visual Regression Report

Decision: **PASS**

## QA-FIRST scope

The current final artifact is the one-page Task 4K `zh_visual.pdf` for PPAT
page 5. The full visual-v07 regression universe additionally contains the
frozen p006 typography page and the three Task 1 table pages. No consolidated
multi-page v07 PDF exists, so the gate audits all five unique final/frozen page
artifacts rather than silently claiming that the p005 PDF contains other pages.

- `ppat_p005`: current_final, source document page 5, PDF pages=1, SHA-256 `140a1b9c415eb587273bc69fc0f07df0419475e1cc4213e82a5f7a343b15feb7`
- `ppat_p006`: typography_regression, source document page 6, PDF pages=1, SHA-256 `8a614d272293cdf4b9b76ace972ad5f9d2f6ba0e2489c0eb18d4c50083c99e5e`
- `2504_p007`: table_regression, source document page 7, PDF pages=1, SHA-256 `ffd4a34e678ed7ae0799df527fc4b2a199c22843f284a9bb6006fe1bc98b7329`
- `2504_p013`: table_regression, source document page 13, PDF pages=1, SHA-256 `e44fc9e9bf20f46bb897d815f7063fde10318dc01b2a72cba42efa57701358a7`
- `2504_p016`: table_regression, source document page 16, PDF pages=1, SHA-256 `bf2b334fb2ed7ba38e9c61a7b78fc6056c0c0b7c504074025983ce934a8f2cd5`

Every PDF was reopened and rendered with Poppler at 144 DPI. The audit covers
all `8` SourceTextSlots in p005/p006, all
`11` MathAtomGroups,
all Figure/formula anchors on p005/p006, and all
`158` LogicalCells on p007/p013/p016.

## A. Math regression

All atom loss, duplicate, order, superscript, subscript, and final-PDF loss
metrics are zero. The calligraphic forward transform `𝓕(⋅)`, semantic inverse
transform `𝓕^{-1}(⋅)`, and `𝜆𝑐 > 0` are visible in final PDF text/ink.
Each MathAtomGroup records its source sequence, opaque protected token, semantic
HTML atom sequence, and final-PDF character sequence. Atom order is preserved
through every layer. Task 4H PUA normalization and Task 4I `<sup>/<sub>`
structure both remain closed.

## B. Measurement regression

Both p005 and p006 were rerun through the current math-aware collision audit.
Every final raster painted-ink bbox is contained by its effective measurement
bbox. `dom_height_stale_count`, `ink_outside_measurement_count`, and
`final_block_collision_count` are all zero. No block was moved by this audit.

## C. Ownership regression

The Task 4F exclusive ownership ledger still has one PrimaryOwner per source
span. Figure-internal annotations have no soft paragraph or SourceTextSlot in
the current HTML, the Figure hard anchor remains present, and the external
caption remains separate. Figure-internal soft owner, duplicate render, and
multi-primary-owner counts are zero.

## D. Typography regression

Task 4D indentation was remeasured in current Chromium output. All three source
indent candidates remain restored; missing/wrong/false counts are zero. The
target paragraphs retain source-relative `1.5em` indentation. All p005/p006
SourceTextSlot rectangles match their frozen bboxes; slot mutation is zero.

## E. Table regression

The p007, p013, and p016 final PDFs were re-audited against the frozen Task 1
canonical cell targets. There are `158` LogicalCells,
including `52` translation-required cells;
overflow and translation regressions are zero. p013 remains 21x5 with 105
cells, 3 horizontal rulings, and 0 vertical rulings. Current translated-cell
owner counts match the frozen Task 1 structure ledger on all three pages.

## F. Formula / Figure hard anchors

Formula and Figure signatures on p005 are unchanged from Task 4F; p006 hard
anchors are unchanged from the pre-Task 3 checkpoint. Table anchors are
unchanged from Task 1. Formula mutation, Figure geometry mutation, table anchor
mutation, and total hard-anchor movement are all zero.

## Hard metrics

```json
{
  "math_atom_loss_count": 0,
  "math_atom_duplicate_count": 0,
  "math_atom_order_error_count": 0,
  "superscript_structure_loss_count": 0,
  "subscript_structure_loss_count": 0,
  "final_pdf_atom_loss_count": 0,
  "final_pdf_atom_order_error_count": 0,
  "math_fixture_missing_count": 0,
  "task4h_pua_regression_count": 0,
  "dom_height_stale_count": 0,
  "ink_outside_measurement_count": 0,
  "final_block_collision_count": 0,
  "painted_ink_containment_failure_count": 0,
  "figure_internal_soft_text_owner_count": 0,
  "figure_internal_duplicate_render_count": 0,
  "source_span_multi_primary_owner_count": 0,
  "source_span_render_owner_count_gt1": 0,
  "erroneous_figure_internal_slot_count": 0,
  "figure_hard_anchor_missing_count": 0,
  "first_line_indent_missing_count": 0,
  "first_line_indent_wrong_count": 0,
  "false_first_line_indent_count": 0,
  "slot_geometry_mutation_count": 0,
  "text_slot_count": 8,
  "table_cell_count": 158,
  "required_table_cell_count": 52,
  "table_overflow_count": 0,
  "table_anchor_mutation_count": 0,
  "table_translation_regression_count": 0,
  "table_structure_mutation_count": 0,
  "formula_anchor_mutation_count": 0,
  "figure_geometry_mutation_count": 0,
  "hard_anchor_moved_count": 0,
  "production_special_case_count": 0
}
```

This task is diagnostic only. It changed no renderer, slot, ownership,
typography, math reconstruction, measurement policy, or hard anchor. No
`visual-v07` tag was created.
