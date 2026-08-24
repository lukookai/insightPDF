# Math-aware Block Measurement Report

Decision: **PASS**

## Why DOM height was smaller than painted ink

`PAF_B1_02` retained an explicit frozen CSS/SourceTextSlot height of
38.801pt. The content still painted with
`overflow:visible` through the last wrapped line, so the independent final-PDF
raster ink reached y=511.750pt while the DOM-only
measurement stopped at y=504.035pt. The old measurement
was therefore short by 7.715pt.
This was stale measurement, not clipping or later-block occlusion.

## Math ascent/descent evidence

The target MathAtomGroup contains 1 script atom(s). Its
subscript `𝑐` extends 0.950pt below the
base atom box. Across all measured groups, the recorded non-zero script
extensions are:

```json
[
  {
    "render_id": "PAF_B1_00",
    "atom_id": "MAG-52F45C34EAFB-A01",
    "role": "superscript",
    "text": "−1",
    "ascent_extension_pt": 1.195,
    "descent_extension_pt": 0.0
  },
  {
    "render_id": "PAF_B1_00",
    "atom_id": "MAG-FD587A88A6FD-A01",
    "role": "subscript",
    "text": "𝑐",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_00",
    "atom_id": "MAG-179831757CCB-A01",
    "role": "subscript",
    "text": "𝑢,𝑣",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_00",
    "atom_id": "MAG-179831757CCB-A04",
    "role": "superscript",
    "text": "2",
    "ascent_extension_pt": 1.195,
    "descent_extension_pt": 0.0
  },
  {
    "render_id": "PAF_B1_00",
    "atom_id": "MAG-179831757CCB-A07",
    "role": "superscript",
    "text": "2",
    "ascent_extension_pt": 1.195,
    "descent_extension_pt": 0.0
  },
  {
    "render_id": "PAF_B1_00",
    "atom_id": "MAG-612662CB23BA-A03",
    "role": "superscript",
    "text": "𝐶",
    "ascent_extension_pt": 1.196,
    "descent_extension_pt": 0.0
  },
  {
    "render_id": "PAF_B1_02",
    "atom_id": "MAG-98F1AC430FC0-A01",
    "role": "subscript",
    "text": "𝑐",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 0.95
  },
  {
    "render_id": "PAF_B1_04",
    "atom_id": "MAG-954A3FCBE690-A01",
    "role": "subscript",
    "text": "𝑖",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_04",
    "atom_id": "MAG-818DD43AAC31-A01",
    "role": "subscript",
    "text": "𝑖",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_01",
    "atom_id": "MAG-21200F752707-A01",
    "role": "subscript",
    "text": "𝑘",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_01",
    "atom_id": "MAG-21200F752707-A04",
    "role": "subscript",
    "text": "𝑖",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_01",
    "atom_id": "MAG-3F6A4368F6C1-A01",
    "role": "subscript",
    "text": "𝐹𝐹𝑁",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_01",
    "atom_id": "MAG-3F6A4368F6C1-A04",
    "role": "subscript",
    "text": "𝑖",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_01",
    "atom_id": "MAG-412216EF6F74-A01",
    "role": "subscript",
    "text": "𝑘",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  },
  {
    "render_id": "PAF_B1_03",
    "atom_id": "MAG-E9D29DCF0174-A01",
    "role": "subscript",
    "text": "𝑘",
    "ascent_extension_pt": 0.0,
    "descent_extension_pt": 1.043
  }
]
```

The subscript is relevant evidence that normal-text baseline assumptions are
unsafe. It is not the sole source of the 8pt-class stale height: the frozen
block also paints a final wrapped line below its declared DOM bottom. Task 4J's
sup/sub neutralization remains unchanged; this task measures the actual result
instead of changing script CSS.

## Fixed measurement

The production measurement now retains `dom_bbox`, final painted-glyph bbox,
per-atom base/sup/sub boxes, `ink_top`, `ink_bottom`, ascent, and descent. From
the block origin it computes:

`measured_height = max(dom_height, painted_ink_height)`

For the target this is
`max(38.801, 46.516)`
= **46.516pt**. The effective bottom
is y=511.750pt, which encloses both the
DOM block and the independent raster-ink bottom
y=511.750pt. The text-layer glyph bbox is retained only as
the ownership constraint used to isolate the target's raster ink.

## Frozen geometry and regressions

- SourceTextSlot geometry is byte-for-byte unchanged:
  `slot_geometry_mutation_count=0`.
- Figure and table anchors are unchanged:
  `figure_anchor_mutation_count=0` and
  `table_anchor_mutation_count=0`.
- Task 4H calligraphic `𝓕(⋅)` / inverse-transform glyph content is unchanged.
- Task 4I semantic `<sup>` / `<sub>` markup and every MathAtomGroup atom order
  are unchanged: `True`.
- No block is moved and the SourceTextSlot CSS `height` is not enlarged. The
  copied final PDF is pixel/content-identical; only measurement truth changes.
- Production special-case count is
  `0`. Production code contains no
  target text, render id, page, or filename branch.

## Hard metrics

```json
{
  "dom_height_stale_count": 0,
  "ink_outside_measurement_count": 0,
  "final_block_collision_count": 0,
  "slot_geometry_mutation_count": 0,
  "figure_anchor_mutation_count": 0,
  "table_anchor_mutation_count": 0,
  "production_special_case_count": 0,
  "math_atom_order_error_count": 0,
  "task4h_regression_count": 0,
  "superscript_structure_loss_count": 0,
  "subscript_structure_loss_count": 0,
  "pdf_content_mutation_count": 0
}
```
