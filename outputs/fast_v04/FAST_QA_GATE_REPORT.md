# fast-v04 - Production QA Fast Gate

## Decision

**FAST_V04 = PASS**

Only the existing PPAT p011, 2504 p011, and 2504 p005 artifacts were read. No translation, renderer, Chromium, Poppler, source-PDF parse, full regression, page rasterization, overlay, PNG, contact sheet, or review bundle was executed.

## Production routing

The default production mode on `fast` is now `qa_mode=fast`. It returns immediately after the nine deterministic FAST_GATE checks. The existing deep QA implementation was not removed and remains available only through explicit `release`, `debug`, or `regression` selection.

## Fixture results

| fixture | FAST_GATE | time | PDF reopen | unnecessary reopen | rasterize | heavy QA skipped |
|---|---|---:|---:|---:|---:|---:|
| ppat_p011 | PASS | 0.001344s | 1 | 0 | 0 | 8 |
| 2504_p011 | PASS | 0.001111s | 1 | 0 | 0 | 8 |
| 2504_p005 | PASS | 0.000944s | 1 | 0 | 0 | 8 |

All nine checks passed on every fixture:

- `render_success`: PASS
- `physical_page_count_valid`: PASS
- `required_translation_missing_count`: PASS
- `required_table_cell_missing_count`: PASS
- `source_span_multi_primary_owner_count`: PASS
- `obvious_block_collision_count`: PASS
- `obvious_overflow_count`: PASS
- `hard_anchor_missing_count`: PASS
- `invalid_bbox_count`: PASS

## Content invariants

The visual-v03 zero-pixel-diff evidence was reused rather than rasterizing these pages again. Because fast-v04 changes only QA routing and metadata inspection, it does not touch the renderer or the final HTML/PDF payload.

| fixture | final PDF pixel diff | slot mutation | anchor moved | production special case |
|---|---:|---:|---:|---:|
| ppat_p011 | 0 | 0 | 0 | 0 |
| 2504_p011 | 0 | 0 | 0 | 0 |
| 2504_p005 | 0 | 0 | 0 | 0 |

## Why this is outside the hot path

The frozen fast-v01 baseline measured 1862.239 seconds (31m02s) of accumulated QA across the 31-page Task 4M run. fast-v04 does not claim that entire interval as immediately saved, because some QA overlapped other work; it establishes the enforceable production boundary: one low-cost gate per page, with all eight deep QA categories skipped unless explicitly requested.

FAST_GATE performs one necessary PDF structural open for physical page count. It performs zero unnecessary reopen operations and zero rasterizations. Full math provenance, complete source-to-PDF chain, Figure ownership, painted-ink, visual raster, contact sheet, review bundle, and full-document regression remain release/debug tools.