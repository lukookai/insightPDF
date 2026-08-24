# Fresh Full-Document End-to-End Regression Report

Decision: **BLOCKED**

## Fresh-run lineage

Both documents were rebuilt from the original PDFs under the current HEAD.
The source-chain PageModels, translations, HTML and PDFs were newly generated
inside `outputs/visual_v07_task4m/fresh_runs_v2`. The visual-v07 renderer then
created every `zh_visual.html` and `zh_visual.pdf` in a separate fresh root.
No old HTML, final PDF, or frozen page artifact was used as production input.
Translation caches were reused as explicitly allowed.

- PPAT source pages: 12; final visual pages: 13.
- 2504 source pages: 19; final visual pages: 19.
- Total final pages audited: 32.
- Unexpected extra pages: 1.
- Missing physical pages: 0.

An initial PPAT attempt used the system interpreter without `babeldoc`; it was
excluded and never supplied HTML/PDF to this run. The accepted fresh run used
the repository `.venv`, where DocLayout was available. This is recorded as an
environment-selection event, not a production fix.

## Final visual regression

Math atom loss/duplicate/order are
45/0/
0; superscript and subscript structure
loss are 9 and
10. Final-PDF atom loss is
1. Fixture verification for `𝓕(⋅)`,
`𝓕^{-1}(⋅)`, and `𝜆𝑐 > 0` has
`math_fixture_failure_count=0`.

Measurement has `dom_height_stale_count=0`,
`ink_outside_measurement_count=0` and
`final_block_collision_count=6`.

Exclusive ownership has Figure-internal duplicate render
39, Figure-internal soft owner
0, and multi-primary source
owner 0.

Typography has first-line indent missing/wrong counts
0/
0 and slot geometry mutation
0.

Tables have untranslated required cells
14, overflow
0, and p013 structure failure
0. The required p013 fixture remains
21x5, 105 cells, 3 horizontal rulings, and 0 vertical rulings only when that
last metric is zero.

## Source-chain gate evidence

The newly generated source chains are also part of the end-to-end evidence.
They contributed 57 recorded defect
rows. These failures were not repaired or suppressed. Consequently, Task 4M
can PASS only if both the source-chain and final visual metrics are all zero.

## Failure ledger

| Document | Page | Stage | Defect | Category | Evidence detail | Evidence file |
|---|---:|---|---|---|---|---|
| ppat | 5 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | physical_assertion_failure | PHYSICAL_PDF | blank=0 clipped=0 renderer_failures=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | physical_page_mismatch | PHYSICAL_PDF | physical=2 expected=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | overflow | MEASUREMENT_COLLISION | overflow=1 bottom_overflow=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | dom_render_failure | OTHER | owned_unrendered=2 region_no_dom=0 dom_no_ink=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | blank_formula | MATH_FORMULA | blank=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=0 text_table=0 text_figure=0 formula_formula=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 5 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=physical_pdf,render_collision,semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | physical_assertion_failure | PHYSICAL_PDF | blank=1 clipped=0 renderer_failures=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | physical_page_mismatch | PHYSICAL_PDF | physical=2 expected=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | overflow | MEASUREMENT_COLLISION | overflow=1 bottom_overflow=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | dom_render_failure | OTHER | owned_unrendered=1 region_no_dom=0 dom_no_ink=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | blank_formula | MATH_FORMULA | blank=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=0 text_table=0 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 6 | fresh_source_chain | grid_layout_failure | OTHER | gutter=0 overlap=None outside=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 1 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 1 | fresh_source_chain | dom_render_failure | OTHER | owned_unrendered=35 region_no_dom=5 dom_no_ink=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 1 | fresh_source_chain | formula_exclusivity_failure | MATH_FORMULA | duplicate=0 residue=3 foreign_text=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 1 | fresh_source_chain | residual_language | TRANSLATION_CLOSURE | body=0 heading=0 short_tokens=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 1 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=7 text_table=0 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 1 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=formula_exclusivity,render_collision,residual_language,semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 2 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 2 | fresh_source_chain | overflow | MEASUREMENT_COLLISION | overflow=1 bottom_overflow=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 2 | fresh_source_chain | formula_exclusivity_failure | MATH_FORMULA | duplicate=0 residue=0 foreign_text=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 2 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=7 text_table=0 text_figure=42 formula_formula=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 2 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=formula_exclusivity,render_collision,semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 10 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 10 | fresh_source_chain | paragraph_structural_pollution | OTHER | violations=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 10 | fresh_source_chain | residual_language | TRANSLATION_CLOSURE | body=1 heading=0 short_tokens=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 10 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=0 text_table=0 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 10 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=paragraph_structural,render_collision,residual_language,semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 9 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 9 | fresh_source_chain | overflow | MEASUREMENT_COLLISION | overflow=1 bottom_overflow=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 9 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=0 text_table=19 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 9 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=post_obstacle_grid,render_collision,semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 4 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 4 | fresh_source_chain | paragraph_structural_pollution | OTHER | violations=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 4 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=8 text_table=0 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 4 | fresh_source_chain | grid_layout_failure | OTHER | gutter=1 overlap=None outside=15 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 11 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 11 | fresh_source_chain | formula_exclusivity_failure | MATH_FORMULA | duplicate=0 residue=0 foreign_text=8 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 11 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=17 text_table=0 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 11 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=formula_exclusivity,render_collision,semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 3 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 3 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 7 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 7 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 8 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 8 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 12 | fresh_source_chain | page_assertion_false | PHYSICAL_PDF | page-level assertions failed | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | 12 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=semantic | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\defect_pages.json` |
| ppat | document | fresh_source_chain | source_chain_delivery_gate | SOURCE_CHAIN_GATE | blocked layers: semantic_qa, structural_paragraph_qa, translation_qa, dom_qa, physical_pdf_qa, formula_exclusivity_qa, residual_language_qa, rendered_collision_qa, qa_execution_incomplete | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\ppat_source_chain\delivery_gate.json` |
| 2504 | 16 | fresh_source_chain | render_collision | MEASUREMENT_COLLISION | text_formula=0 text_table=1 text_figure=0 formula_formula=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\2504_source_chain\defect_pages.json` |
| 2504 | 16 | fresh_source_chain | visual_hard_gate_failure | OTHER | blocked_layers=render_collision | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\2504_source_chain\defect_pages.json` |
| 2504 | 10 | fresh_source_chain | grid_layout_failure | OTHER | gutter=1 overlap=None outside=0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\2504_source_chain\defect_pages.json` |
| 2504 | document | fresh_source_chain | source_chain_delivery_gate | SOURCE_CHAIN_GATE | blocked layers: rendered_collision_qa | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_runs_v2\2504_source_chain\delivery_gate.json` |
| ppat | 1 | fresh_visual_render | visual_render_failure | SOURCE_TEXT_SLOT_MEASUREMENT_MAPPING | ValueError: measured paragraph missing: DLP00002-F0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p001\task4m_render_failure.json` |
| 2504 | 1 | fresh_visual_render | visual_render_failure | SOURCE_TEXT_SLOT_MEASUREMENT_MAPPING | ValueError: measured paragraph missing: DLP00001-F0 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p001\task4m_render_failure.json` |
| ppat | 1 | fresh_visual_artifact | missing_fresh_page_artifact | PHYSICAL_PDF | visual page QA/HTML/PDF/model missing | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p001` |
| ppat | 2 | visual_hard_gate | heading_target_missing_count | TRANSLATION_CLOSURE | heading_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p002\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | translatable_target_missing_count | TRANSLATION_CLOSURE | translatable_target_missing_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | severe_soft_soft_collision_count | MEASUREMENT_COLLISION | severe_soft_soft_collision_count=30 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | duplicate_baseline_cluster_count | OTHER | duplicate_baseline_cluster_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | missing_render_count | OTHER | missing_render_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | translatable_source_residual_fragment_count | TRANSLATION_CLOSURE | translatable_source_residual_fragment_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | translatable_source_residual_char_count | TRANSLATION_CLOSURE | translatable_source_residual_char_count=54 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | translatable_source_residual_sentence_count | TRANSLATION_CLOSURE | translatable_source_residual_sentence_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | heading_target_missing_count | TRANSLATION_CLOSURE | heading_target_missing_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | final_block_collision_count | MEASUREMENT_COLLISION | final_block_collision_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | heading_body_collision_count | MEASUREMENT_COLLISION | heading_body_collision_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | reading_order_overlap_count | OTHER | reading_order_overlap_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | browser_measured_repack_unresolved_count | OTHER | browser_measured_repack_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | source_prose_vector_still_rendered_count | OTHER | source_prose_vector_still_rendered_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | anchor_invasion_count | HARD_ANCHOR | anchor_invasion_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 3 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p003\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | translatable_target_missing_count | TRANSLATION_CLOSURE | translatable_target_missing_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | missing_render_count | OTHER | missing_render_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | final_block_collision_count | MEASUREMENT_COLLISION | final_block_collision_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | heading_body_collision_count | MEASUREMENT_COLLISION | heading_body_collision_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | reading_order_overlap_count | OTHER | reading_order_overlap_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | browser_measured_repack_unresolved_count | OTHER | browser_measured_repack_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\visual_page_qa.json` |
| ppat | 4 | math_atom_qa | final_pdf_atom_loss_count | OTHER | final_pdf_atom_loss_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\task4m_math_qa.json` |
| ppat | 4 | source_ownership_qa | figure_internal_duplicate_render_count | SOURCE_OWNERSHIP | figure_internal_duplicate_render_count=39 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\task4m_source_ownership_qa.json` |
| ppat | 4 | source_ownership_qa | erroneous_figure_internal_slot_count | SOURCE_OWNERSHIP | erroneous_figure_internal_slot_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p004\task4m_source_ownership_qa.json` |
| ppat | 5 | visual_hard_gate | translatable_target_missing_count | TRANSLATION_CLOSURE | translatable_target_missing_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | missing_render_count | OTHER | missing_render_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | heading_target_missing_count | TRANSLATION_CLOSURE | heading_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | final_block_collision_count | MEASUREMENT_COLLISION | final_block_collision_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | heading_body_collision_count | MEASUREMENT_COLLISION | heading_body_collision_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | reading_order_overlap_count | OTHER | reading_order_overlap_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | browser_measured_repack_unresolved_count | OTHER | browser_measured_repack_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 5 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p005\visual_page_qa.json` |
| ppat | 6 | visual_hard_gate | translatable_target_missing_count | TRANSLATION_CLOSURE | translatable_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p006\visual_page_qa.json` |
| ppat | 6 | visual_hard_gate | missing_render_count | OTHER | missing_render_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p006\visual_page_qa.json` |
| ppat | 6 | visual_hard_gate | heading_target_missing_count | TRANSLATION_CLOSURE | heading_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p006\visual_page_qa.json` |
| ppat | 7 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p007\visual_page_qa.json` |
| ppat | 7 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p007\visual_page_qa.json` |
| ppat | 7 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p007\visual_page_qa.json` |
| ppat | 7 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p007\visual_page_qa.json` |
| ppat | 7 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p007\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | heading_target_missing_count | TRANSLATION_CLOSURE | heading_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | short_translatable_source_residual_count | TRANSLATION_CLOSURE | short_translatable_source_residual_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | heading_short_residual_count | TRANSLATION_CLOSURE | heading_short_residual_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | untranslated_required_table_cell_count | TABLE | untranslated_required_table_cell_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | table_cell_target_missing_count | TRANSLATION_CLOSURE | table_cell_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | table_cell_source_residual_count | TRANSLATION_CLOSURE | table_cell_source_residual_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | anchor_invasion_count | HARD_ANCHOR | anchor_invasion_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 8 | visual_hard_gate | group_target_drop_count | OTHER | group_target_drop_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p008\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | translatable_target_missing_count | TRANSLATION_CLOSURE | translatable_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | translatable_source_residual_count | TRANSLATION_CLOSURE | translatable_source_residual_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | translatable_source_residual_fragment_count | TRANSLATION_CLOSURE | translatable_source_residual_fragment_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | translatable_source_residual_char_count | TRANSLATION_CLOSURE | translatable_source_residual_char_count=169 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | translatable_source_residual_sentence_count | TRANSLATION_CLOSURE | translatable_source_residual_sentence_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | formula_adjacent_target_missing_count | TRANSLATION_CLOSURE | formula_adjacent_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | short_translatable_source_residual_count | TRANSLATION_CLOSURE | short_translatable_source_residual_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | standalone_short_residual_count | TRANSLATION_CLOSURE | standalone_short_residual_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | formula_context_short_residual_count | TRANSLATION_CLOSURE | formula_context_short_residual_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | untranslated_required_table_cell_count | TABLE | untranslated_required_table_cell_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | table_cell_target_missing_count | TRANSLATION_CLOSURE | table_cell_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | table_cell_source_residual_count | TRANSLATION_CLOSURE | table_cell_source_residual_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | source_prose_vector_still_rendered_count | OTHER | source_prose_vector_still_rendered_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 9 | visual_hard_gate | group_target_drop_count | OTHER | group_target_drop_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p009\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | untranslated_required_table_cell_count | TABLE | untranslated_required_table_cell_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | table_cell_target_missing_count | TRANSLATION_CLOSURE | table_cell_target_missing_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | table_cell_source_residual_count | TRANSLATION_CLOSURE | table_cell_source_residual_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | visual_hard_gate | group_target_drop_count | OTHER | group_target_drop_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\visual_page_qa.json` |
| ppat | 10 | source_ownership_qa | erroneous_figure_internal_slot_count | SOURCE_OWNERSHIP | erroneous_figure_internal_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p010\task4m_source_ownership_qa.json` |
| ppat | 11 | visual_hard_gate | translatable_target_missing_count | TRANSLATION_CLOSURE | translatable_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | translatable_source_residual_count | TRANSLATION_CLOSURE | translatable_source_residual_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | severe_soft_soft_collision_count | MEASUREMENT_COLLISION | severe_soft_soft_collision_count=31 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | duplicate_baseline_cluster_count | OTHER | duplicate_baseline_cluster_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | wrong_region_render_count | OTHER | wrong_region_render_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | translatable_source_residual_fragment_count | TRANSLATION_CLOSURE | translatable_source_residual_fragment_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | translatable_source_residual_char_count | TRANSLATION_CLOSURE | translatable_source_residual_char_count=69 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | translatable_source_residual_sentence_count | TRANSLATION_CLOSURE | translatable_source_residual_sentence_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | formula_adjacent_target_missing_count | TRANSLATION_CLOSURE | formula_adjacent_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=18 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | heading_target_missing_count | TRANSLATION_CLOSURE | heading_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | short_translatable_source_residual_count | TRANSLATION_CLOSURE | short_translatable_source_residual_count=13 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | standalone_short_residual_count | TRANSLATION_CLOSURE | standalone_short_residual_count=13 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | formula_context_short_residual_count | TRANSLATION_CLOSURE | formula_context_short_residual_count=13 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 11 | visual_hard_gate | source_prose_vector_still_rendered_count | OTHER | source_prose_vector_still_rendered_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p011\visual_page_qa.json` |
| ppat | 12 | visual_hard_gate | severe_soft_soft_collision_count | MEASUREMENT_COLLISION | severe_soft_soft_collision_count=47 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p012\visual_page_qa.json` |
| ppat | 12 | visual_hard_gate | duplicate_baseline_cluster_count | OTHER | duplicate_baseline_cluster_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p012\visual_page_qa.json` |
| ppat | 12 | visual_hard_gate | unexpected_extra_page_count | PHYSICAL_PDF | unexpected_extra_page_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\ppat_p012\visual_page_qa.json` |
| 2504 | 1 | fresh_visual_artifact | missing_fresh_page_artifact | PHYSICAL_PDF | visual page QA/HTML/PDF/model missing | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p001` |
| 2504 | 2 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p002\visual_page_qa.json` |
| 2504 | 2 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p002\visual_page_qa.json` |
| 2504 | 2 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p002\visual_page_qa.json` |
| 2504 | 2 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p002\visual_page_qa.json` |
| 2504 | 2 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p002\visual_page_qa.json` |
| 2504 | 2 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p002\visual_page_qa.json` |
| 2504 | 3 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\visual_page_qa.json` |
| 2504 | 3 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\visual_page_qa.json` |
| 2504 | 3 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\visual_page_qa.json` |
| 2504 | 3 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\visual_page_qa.json` |
| 2504 | 3 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\visual_page_qa.json` |
| 2504 | 3 | math_atom_qa | math_atom_loss_count | MATH_FORMULA | math_atom_loss_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\task4m_math_qa.json` |
| 2504 | 3 | math_atom_qa | subscript_structure_loss_count | MATH_FORMULA | subscript_structure_loss_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\task4m_math_qa.json` |
| 2504 | 3 | math_atom_qa | inline_math_target_missing_count | TRANSLATION_CLOSURE | inline_math_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p003\task4m_math_qa.json` |
| 2504 | 4 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\visual_page_qa.json` |
| 2504 | 4 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\visual_page_qa.json` |
| 2504 | 4 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\visual_page_qa.json` |
| 2504 | 4 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\visual_page_qa.json` |
| 2504 | 4 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\visual_page_qa.json` |
| 2504 | 4 | math_atom_qa | math_atom_loss_count | MATH_FORMULA | math_atom_loss_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\task4m_math_qa.json` |
| 2504 | 4 | math_atom_qa | superscript_structure_loss_count | MATH_FORMULA | superscript_structure_loss_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\task4m_math_qa.json` |
| 2504 | 4 | math_atom_qa | subscript_structure_loss_count | MATH_FORMULA | subscript_structure_loss_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\task4m_math_qa.json` |
| 2504 | 4 | math_atom_qa | inline_math_target_missing_count | TRANSLATION_CLOSURE | inline_math_target_missing_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p004\task4m_math_qa.json` |
| 2504 | 5 | visual_hard_gate | role_target_missing_count | TRANSLATION_CLOSURE | role_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | math_token_order_inversion_count | MATH_FORMULA | math_token_order_inversion_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | anchor_invasion_count | HARD_ANCHOR | anchor_invasion_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\visual_page_qa.json` |
| 2504 | 5 | math_atom_qa | math_atom_loss_count | MATH_FORMULA | math_atom_loss_count=29 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\task4m_math_qa.json` |
| 2504 | 5 | math_atom_qa | superscript_structure_loss_count | MATH_FORMULA | superscript_structure_loss_count=8 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\task4m_math_qa.json` |
| 2504 | 5 | math_atom_qa | subscript_structure_loss_count | MATH_FORMULA | subscript_structure_loss_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\task4m_math_qa.json` |
| 2504 | 5 | math_atom_qa | inline_math_target_missing_count | TRANSLATION_CLOSURE | inline_math_target_missing_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p005\task4m_math_qa.json` |
| 2504 | 6 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p006\visual_page_qa.json` |
| 2504 | 6 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p006\visual_page_qa.json` |
| 2504 | 6 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p006\visual_page_qa.json` |
| 2504 | 6 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p006\visual_page_qa.json` |
| 2504 | 6 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p006\visual_page_qa.json` |
| 2504 | 7 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p007\visual_page_qa.json` |
| 2504 | 7 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p007\visual_page_qa.json` |
| 2504 | 7 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p007\visual_page_qa.json` |
| 2504 | 7 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p007\visual_page_qa.json` |
| 2504 | 7 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p007\visual_page_qa.json` |
| 2504 | 8 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p008\visual_page_qa.json` |
| 2504 | 8 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p008\visual_page_qa.json` |
| 2504 | 8 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p008\visual_page_qa.json` |
| 2504 | 8 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p008\visual_page_qa.json` |
| 2504 | 8 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p008\visual_page_qa.json` |
| 2504 | 9 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p009\visual_page_qa.json` |
| 2504 | 9 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p009\visual_page_qa.json` |
| 2504 | 9 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p009\visual_page_qa.json` |
| 2504 | 9 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p009\visual_page_qa.json` |
| 2504 | 9 | visual_hard_gate | gutter_intrusion_count | OTHER | gutter_intrusion_count=11 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p009\visual_page_qa.json` |
| 2504 | 9 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p009\visual_page_qa.json` |
| 2504 | 10 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p010\visual_page_qa.json` |
| 2504 | 10 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p010\visual_page_qa.json` |
| 2504 | 10 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p010\visual_page_qa.json` |
| 2504 | 10 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p010\visual_page_qa.json` |
| 2504 | 10 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p010\visual_page_qa.json` |
| 2504 | 11 | visual_hard_gate | math_base_symbol_loss_count | MATH_FORMULA | math_base_symbol_loss_count=6 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p011\visual_page_qa.json` |
| 2504 | 11 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=8 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p011\visual_page_qa.json` |
| 2504 | 11 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=8 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p011\visual_page_qa.json` |
| 2504 | 11 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=8 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p011\visual_page_qa.json` |
| 2504 | 11 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=9 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p011\visual_page_qa.json` |
| 2504 | 11 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=8 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p011\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | untranslated_required_table_cell_count | TABLE | untranslated_required_table_cell_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | table_cell_target_missing_count | TRANSLATION_CLOSURE | table_cell_target_missing_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | table_cell_source_residual_count | TRANSLATION_CLOSURE | table_cell_source_residual_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 12 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p012\visual_page_qa.json` |
| 2504 | 13 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p013\visual_page_qa.json` |
| 2504 | 13 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p013\visual_page_qa.json` |
| 2504 | 13 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p013\visual_page_qa.json` |
| 2504 | 13 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p013\visual_page_qa.json` |
| 2504 | 13 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p013\visual_page_qa.json` |
| 2504 | 14 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\visual_page_qa.json` |
| 2504 | 14 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\visual_page_qa.json` |
| 2504 | 14 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\visual_page_qa.json` |
| 2504 | 14 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\visual_page_qa.json` |
| 2504 | 14 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=7 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\visual_page_qa.json` |
| 2504 | 14 | math_atom_qa | math_atom_loss_count | MATH_FORMULA | math_atom_loss_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\task4m_math_qa.json` |
| 2504 | 14 | math_atom_qa | subscript_structure_loss_count | MATH_FORMULA | subscript_structure_loss_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\task4m_math_qa.json` |
| 2504 | 14 | math_atom_qa | inline_math_target_missing_count | TRANSLATION_CLOSURE | inline_math_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p014\task4m_math_qa.json` |
| 2504 | 15 | visual_hard_gate | translatable_target_duplicate_count | TABLE | translatable_target_duplicate_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | severe_soft_soft_collision_count | MEASUREMENT_COLLISION | severe_soft_soft_collision_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | duplicate_baseline_cluster_count | OTHER | duplicate_baseline_cluster_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | duplicate_render_count | SOURCE_OWNERSHIP | duplicate_render_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | untranslated_required_table_cell_count | TABLE | untranslated_required_table_cell_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | table_cell_wrong_owner_count | TABLE | table_cell_wrong_owner_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | final_block_collision_count | MEASUREMENT_COLLISION | final_block_collision_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | reading_order_overlap_count | OTHER | reading_order_overlap_count=3 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | slot_mapping_ambiguous_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_mapping_ambiguous_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | browser_measured_repack_unresolved_count | OTHER | browser_measured_repack_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=4 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\visual_page_qa.json` |
| 2504 | 15 | math_atom_qa | math_atom_loss_count | MATH_FORMULA | math_atom_loss_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\task4m_math_qa.json` |
| 2504 | 15 | math_atom_qa | subscript_structure_loss_count | MATH_FORMULA | subscript_structure_loss_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\task4m_math_qa.json` |
| 2504 | 15 | math_atom_qa | inline_math_target_missing_count | TRANSLATION_CLOSURE | inline_math_target_missing_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p015\task4m_math_qa.json` |
| 2504 | 16 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p016\visual_page_qa.json` |
| 2504 | 16 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p016\visual_page_qa.json` |
| 2504 | 16 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p016\visual_page_qa.json` |
| 2504 | 16 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p016\visual_page_qa.json` |
| 2504 | 16 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=5 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p016\visual_page_qa.json` |
| 2504 | 17 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p017\visual_page_qa.json` |
| 2504 | 17 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p017\visual_page_qa.json` |
| 2504 | 17 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p017\visual_page_qa.json` |
| 2504 | 17 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p017\visual_page_qa.json` |
| 2504 | 17 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p017\visual_page_qa.json` |
| 2504 | 18 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p018\visual_page_qa.json` |
| 2504 | 18 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p018\visual_page_qa.json` |
| 2504 | 18 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p018\visual_page_qa.json` |
| 2504 | 18 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p018\visual_page_qa.json` |
| 2504 | 18 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=1 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p018\visual_page_qa.json` |
| 2504 | 19 | visual_hard_gate | geometry_locked_outside_slot_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | geometry_locked_outside_slot_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p019\visual_page_qa.json` |
| 2504 | 19 | visual_hard_gate | geometry_locked_overflow_count | MEASUREMENT_COLLISION | geometry_locked_overflow_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p019\visual_page_qa.json` |
| 2504 | 19 | visual_hard_gate | slot_capacity_unresolved_count | SOURCE_TEXT_SLOT_TYPOGRAPHY | slot_capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p019\visual_page_qa.json` |
| 2504 | 19 | visual_hard_gate | unexplained_geometry_mutation_count | HARD_ANCHOR | unexplained_geometry_mutation_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p019\visual_page_qa.json` |
| 2504 | 19 | visual_hard_gate | capacity_unresolved_count | OTHER | capacity_unresolved_count=2 | `E:\Code\fanyi\outputs\visual_v07_task4m\fresh_visual_pages\2504_p019\visual_page_qa.json` |
| ppat | document | full_document_physical_pdf_qa | physical_pdf_qa_failure | PHYSICAL_PDF | full-document Physical PDF QA blocked | `E:\Code\fanyi\outputs\visual_v07_task4m\ppat_fresh_physical_pdf_qa.json` |
| 2504 | document | full_document_physical_pdf_qa | physical_pdf_qa_failure | PHYSICAL_PDF | full-document Physical PDF QA blocked | `E:\Code\fanyi\outputs\visual_v07_task4m\2504_fresh_physical_pdf_qa.json` |

## Hard metrics

```json
{
  "ppat_source_page_count": 12,
  "ppat_final_page_count": 13,
  "2504_source_page_count": 19,
  "2504_final_page_count": 19,
  "audited_physical_page_count": 32,
  "unexpected_extra_page_count": 1,
  "missing_physical_page_count": 0,
  "math_atom_loss_count": 45,
  "math_atom_duplicate_count": 0,
  "math_atom_order_error_count": 0,
  "superscript_structure_loss_count": 9,
  "subscript_structure_loss_count": 10,
  "final_pdf_atom_loss_count": 1,
  "final_pdf_atom_order_error_count": 0,
  "math_fixture_failure_count": 0,
  "dom_height_stale_count": 0,
  "ink_outside_measurement_count": 0,
  "final_block_collision_count": 6,
  "figure_internal_soft_text_owner_count": 0,
  "figure_internal_duplicate_render_count": 39,
  "source_span_multi_primary_owner_count": 0,
  "erroneous_figure_internal_slot_count": 6,
  "first_line_indent_missing_count": 0,
  "first_line_indent_wrong_count": 0,
  "slot_geometry_mutation_count": 0,
  "untranslated_required_table_cell_count": 14,
  "table_cell_overflow_count": 0,
  "p013_structure_failure_count": 0,
  "hard_anchor_moved_count": 0,
  "production_special_case_count": 0,
  "source_chain_hard_defect_count": 57,
  "visual_render_failure_count": 2,
  "all_hard_defect_count": 299
}
```

This task made no production-code change and created no `visual-v07` tag.
