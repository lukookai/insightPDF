# fast-v03 - Collapse Chromium PDF Prints

## Decision

**FAST_V03 = PASS**

Only PPAT p011, 2504 p011, and 2504 p005 frozen fixtures were opened. No translation API, fresh/full-document run, full QA, or contact sheet was executed.

## Print path before fast-v03

The pre-change production page path opened Chromium five times and printed two PDFs per page. The first print existed only to feed SourceTextSlot/collision prechecks; the second was the delivery PDF.

| fixture | Chromium launches | PDF prints | intermediate | final |
|---|---:|---:|---:|---:|
| ppat_p011 | 5 | 2 | 1 | 1 |
| 2504_p011 | 5 | 2 | 1 | 1 |
| 2504_p005 | 5 | 2 | 1 | 1 |

Print/launch reasons:

1. `intermediate_pdf`: initial layout PDF for SourceTextSlot/collision precheck.
2. `dom_measurement`: initial FinalBlockCollisionQA DOM measurement.
3. `typography_measurement`: Typography Fit/Fill shared candidate measurement.
4. `final_pdf`: final delivery PDF after Typography Fit/Fill.
5. `collision_validation`: final FinalBlockCollisionQA DOM measurement.

## Print path after fast-v03

One persistent Chromium page now performs the baseline RenderLedger capture, two bounded Typography DOM rounds, and the final collision RenderLedger capture. None of those operations print a PDF. `page.pdf()` is called only for final delivery.

| fixture | launches | DOM rounds | DOM captures | PDF prints | intermediate | final | elapsed | final print |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| ppat_p011 | 1 | 2 | 2 | 1 | 0 | 1 | 40.346s | 0.664s |
| 2504_p011 | 1 | 2 | 2 | 1 | 0 | 1 | 10.201s | 0.472s |
| 2504_p005 | 1 | 2 | 2 | 1 | 0 | 1 | 12.293s | 0.737s |

## Correctness gates

| fixture | pixel diff | slot mutation | collision | anchor moved | overflow | special case |
|---|---:|---:|---:|---:|---:|---:|
| ppat_p011 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2504_p011 | 0 | 0 | 0 | 0 | 0 | 0 |
| 2504_p005 | 0 | 0 | 0 | 0 | 0 | 0 |

Aggregate hard metrics:

- normal fixture final_pdf_print_count = 1: PASS
- total_pdf_print_count <= 2: PASS
- typography_trial_pdf_print_count = 0
- intermediate_pdf_print_count = 0
- chromium_launch_count = 1 per fixture
- visual_pixel_diff_count = 0
- slot_geometry_mutation_count = 0
- final_block_collision_count = 0
- hard_anchor_moved_count = 0
- overflow_count = 0
- production_special_case_count = 0

The bounded-correction guard permits at most one additional formal print, but none of the three normal fixtures used it. SourceTextSlot and hard-anchor signatures are unchanged, and the final rendered pixels are identical to the frozen visual-v07 pages.