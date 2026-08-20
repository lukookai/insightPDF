# Task 4J Review Bundle

This is a read-only audit of the frozen Task 4I artifact. `problem_before.png`
is the final PDF raster. The paint overlay distinguishes the declared slot
from actual final-PDF ink; the DOM overlay distinguishes the fixed-height box
from its overflowing text range and the following soft block.

The text range and ink extend below the declared slot, but the block and its
ancestors do not clip at that boundary. The following soft block starts well
below the overflowing ink. Therefore the strict clip/occlusion QA signal is
not reproduced; the measurable condition is a stale declared DOM height.
