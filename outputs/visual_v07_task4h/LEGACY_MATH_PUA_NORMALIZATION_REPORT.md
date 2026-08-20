# Legacy Math PUA Normalization Report

**Decision: PASS**

## Direct answers

1. **Why did `U+E232` previously become a zero-width glyph?** The HTML kept
   the private-use codepoint, but Chromium fell through to `Segoe UI Symbol`.
   Its cmap aliases `U+E232` to `uni200E` (left-to-right mark), glyph id `353`,
   advance width `0`. The frozen PDF therefore had two zero-width character
   records and no visible Fourier glyph. QA-first measured
   `legacy_pua_visible_failure_count=2`
   and `zero_width_math_glyph_count=2`.

2. **How was `𝓕` confirmed?** Not from `U+E232` alone. The source PDF records
   family `STIXMathCalligraphy`, PostScript name
   `STIXMathCalligraphy-Regular`, Type1 glyph name
   `uniE232`, glyph id
   `2`, and positive ink
   `true`. This reviewed legacy STIX
   calligraphic-capital-F identity selects standard `U+1D4D5` (MATHEMATICAL
   BOLD SCRIPT CAPITAL F) with confidence `1.0`.

3. **Does the mapping require source font/glyph provenance?** Yes. Source
   codepoint, embedded font family, PostScript name, embedded glyph name, and
   confidence must all match. Missing/conflicting evidence stays unchanged
   and becomes `BLOCK_unresolved_required_pua`; it is never silently replaced.

4. **Which font did Chromium finally use?** `Cambria Math`. Both
   standardized characters resolve to supported `u1D4D5` glyphs through the
   existing font chain. No legacy font was installed and CSS was not changed.

5. **Did width and ink recover?** Yes. Final PDF widths are `[7.468, 7.468]` pt and
   raster ink results are `[True, True]`; all are positive.

6. **Are `F(·)` and `F^{-1}(·)` complete?** Yes. DOM and PDF contain
   `𝓕(⋅)` and `𝓕−1(⋅)`. The existing minus, `1`, parentheses, and centered dot
   remain. Superscript reconstruction is intentionally outside this task.

7. **Was `λc > 0` repaired?** No. `deferred_to_Task_4I = true`. Task 4G
   remains `NOT_FONT_CAUSE`; `𝜆`, `𝑐`, and `>` are absent before Chromium.
   Its before/after block is byte-identical:
   `true`.

8. **Is production special-case count zero?** Yes:
   `production_special_case_count=0`.
   Production has no filename, page, paragraph-id, visible-text, or
   single-codepoint conditional. Fixture locators exist only in this runner.

## Verified render chain

```text
U+E232 + STIXMathCalligraphy + STIXMathCalligraphy-Regular + uniE232
  -> exact registry match -> U+1D4D5 𝓕
  -> existing math fallback -> Cambria Math u1D4D5
  -> positive DOM width -> positive final-PDF width and raster ink
```

Only the two verified occurrences were normalized. Unregistered PUA records
remain explicit, unresolved, non-required evidence; the wider private-use
range was not guessed.

## Hard metrics

```json
{
  "legacy_pua_html_count": 2,
  "legacy_pua_pdf_count": 2,
  "legacy_pua_visible_failure_count": 0,
  "zero_width_math_glyph_count": 0,
  "legacy_pua_normalized_count": 2,
  "unresolved_required_pua_count": 0,
  "normalized_math_glyph_missing_count": 0,
  "normalization_wrong_character_count": 0,
  "normalization_duplicate_character_count": 0,
  "final_block_collision_count": 0,
  "production_special_case_count": 0,
  "chromium_cambria_math_selection_count": 2,
  "final_positive_width_math_glyph_count": 2,
  "final_math_glyph_ink_count": 2,
  "fourier_sequence_complete_count": 2,
  "minus_one_regression_count": 0,
  "html_non_normalization_change_count": 0,
  "source_text_slot_geometry_mutation_count": 0,
  "font_chain_modified_count": 0,
  "inline_math_reconstruction_modified_count": 0,
  "formula_crop_modified_count": 0,
  "figure_modified_count": 0,
  "table_modified_count": 0,
  "typography_modified_count": 0,
  "flow_modified_count": 0
}
```

The normalized artifact differs from the current Task 4F artifact by exactly
two text codepoints. Figure ownership, formula crop, table, SourceTextSlot
geometry, typography, flow, translation prompt, and renderer geometry behavior
are unchanged. Final block collision remains zero.
