# fast-v02 - Typography Fast Path

## Decision

**FAST_V02 = PASS**

Only the frozen PPAT p011, 2504 p011, and 2504 p005 fixtures were used. No translation API, fresh document run, full-document QA, contact sheet, or candidate PDF was executed.

## Architecture

The old per-level L0 -> trial -> L1 -> trial loop was replaced by one persistent Chromium page. Round 1 batch-measures all role-policy Fit candidates. Round 2 validates the selected Fit state and batch-measures all eligible Fill candidates. The selected state is then printed once as the formal fixture PDF.

SourceTextSlot coordinates and all formula, figure, and table anchor styles are treated as immutable signatures. No repack, block move, slot expansion, or page/text special case exists in production.

## Performance and hard gates

| fixture | old trials | old trial PDFs | new DOM rounds | browser launches | new trial PDFs | max correction | collision | overflow | slot mutation | anchor moved | pixel diff |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| ppat_p011 | 15 | 10 | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| 2504_p011 | 59 | 0 | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |
| 2504_p005 | 45 | 0 | 2 | 1 | 0 | 1 | 0 | 0 | 0 | 0 | 0 |

## Targeted timing

| fixture | frozen full-page wall clock | fast Typography | one formal PDF print |
|---|---:|---:|---:|
| ppat_p011 | 496.089s | 14.706s | 6.605s |
| 2504_p011 | 247.296s | 3.580s | 2.477s |
| 2504_p005 | 231.076s | 4.218s | 2.883s |

The new numbers are targeted fixture timings, not a claim about a fresh full production page. They isolate the optimized Typography path and its one formal fixture print.

## Result

- typography_correction_count <= 1 for every block: PASS
- typography_trial_pdf_print_count = 0
- typography_measurement_round_count <= 2: PASS
- slot_geometry_mutation_count = 0
- final_block_collision_count = 0
- hard_anchor_moved_count = 0
- overflow_count = 0
- visual_pixel_diff_count = 0
- production_special_case_count = 0

The three review images contain SOURCE | OLD | FAST-V02 at identical page scale. No full-paper run was started.
