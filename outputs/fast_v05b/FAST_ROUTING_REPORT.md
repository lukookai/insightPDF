# FAST v05B — Production Routing Report

## Result

`FAST_V05B = PASS`

The E2E wrapper now stops source preparation after the existing PDF parser,
DocumentModel/PageModel construction, semantic detection, translation
preparation, and translation cache.  It then invokes the current
`render_visual_page(..., qa_mode="fast")` path directly, followed by the
FAST_GATE and final page-PDF merge.

## Route closure

- Observed stage order: `preflight → pdf_parse/page_model → translation/cache → page → ownership/math/slots → html/layout → typography → chromium_render → fast_gate → pdf_finalize`
- Expected stage order matched: `true`
- Current FAST renderer calls: `1`
- FAST_GATE calls: `1`
- Finalize calls: `1`

The old subprocess render/QA route is absent from the production adapter and
source-only preparation.  Static route tripwires and the injected one-page
smoke both passed:

- `phase4d1c_call_count = 0`
- `legacy_render_count = 0`
- `deep_qa_count = 0`
- `formula_render_trace_count = 0`
- `delivery_gate_call_count = 0`

## Shared translation cache

The run output directory is no longer a cache namespace.  Production uses a
fixed writable canonical cache and reads existing workspace translation
caches in place as seeds, using the established content/protected-token/
target-language/prompt-version key.  No cache file is copied into the run.
The smoke used a cache path outside its output directory and confirmed no
output-local `translation_cache.json` was created.

## Scope and verification

This was a dependency-injected single-page routing smoke.  It opened no PDF,
started no Chromium, performed no rasterization, called no translation API,
and ran no full paper or deep QA.  Typography, renderer visual policy,
SourceTextSlot geometry, formula policy, and document content were unchanged.
Machine-readable evidence is in `route_before_after.json`; the exact telemetry
stream is in `smoke_progress.jsonl`.
