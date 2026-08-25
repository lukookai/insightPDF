# FAST Existing Artifact Performance Baseline

## Decision

**PASS** — baseline reconstructed exclusively from frozen visual-v07 Task 4M artifacts. No PDF translation, Chromium, Poppler, QA, or translation API was run.

## Frozen production baseline

| Metric | Seconds | Human |
|---|---:|---:|
| Effective visual render | 4145.535 | 1h09m06s |
| Effective source chain | 1222.021 | 20m22s |
| Accumulated QA | 1862.239 | 31m02s |

QA is a measured overlapping component of the source/visual windows; it must not be added again to obtain end-to-end production time.

The source + visual baseline is **1h29m28s**, which exceeds the five-minute target by **1h24m28s**.

## Explicitly excluded non-production time

| Item | Seconds | Human | Reason |
|---|---:|---:|---|
| Interruption idle | 5923.908 | 1h38m44s | No active production process; excluded from bottleneck ranking |
| Invalid interpreter run | 869.093 | 14m29s | Discarded environment run; excluded |
| Duplicate full audit | 281.736 | 4m42s | Diagnostic false-positive rerun; excluded from normal production target |

## Slow-page hotspots

| Page | Time | Typography trials | Trial PDF prints | Inferred Chromium PDF prints |
|---|---:|---:|---:|---:|
| `ppat_p011` | 8m16s | 15 | 10 | 14 |
| `2504_p011` | 4m07s | 59 | 0 | 3 |
| `2504_p005` | 3m51s | 45 | 0 | 3 |
| `2504_p014` | 3m06s | 44 | 0 | 3 |
| `2504_p003` | 2m37s | 39 | 0 | 3 |
| `ppat_p005` | 2m33s | 12 | 0 | 3 |

Print counts are reconstructed from frozen renderer control flow, lock/fill/fit/repack ledgers, and explicit trial PDF files; no browser was launched. PPAT p004 is excluded because its timestamps cross the Task 4M interruption boundary.

## Optimization priority

### P0 — Typography Fit/Fill multi-round trial render

Slow pages carry dozens of explicit _local_typography_*trial HTML/Chromium measurement artifacts; PPAT p011 also carries ten trial PDFs and alone spans about 8m16s.

Why it blocks <=5 min: A single page can exceed the entire 300-second document budget because every trial launches measurement/render work before the final page is accepted.

First measure: Collapse/strictly bound trial count while preserving the current visual policy; implementation deferred.

### P1 — Per-page Chromium/PDF print lifecycle

The frozen renderer prints each page independently and reprints the same zh_visual.pdf after lock/fill/fit.

Why it blocks <=5 min: Twelve or nineteen independent browser/print lifecycles consume most of the effective 69m06s visual window; the five-minute target permits only seconds per page.

First measure: Reduce print lifecycle cardinality/batch document work; implementation deferred.

### P2 — Repeated production-path QA, PDF reopen, rasterize

Accumulated QA closure is 31m02s, including repeated source/final PDF inspection; the full audit was also duplicated once.

Why it blocks <=5 min: The verifier reopens/rasterizes artifacts already measured during production, so validation approaches or exceeds the complete target budget.

First measure: Reuse a production render ledger and separate fast gate from asynchronous full regression; implementation deferred.

## Translation-cache evidence

Task 4M recorded 960 cache hits, 0 misses, and 0 new translation calls. Model latency is therefore not part of this baseline.
