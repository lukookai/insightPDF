"""Independent Table Reconstruction module.

Recovers a structured TableModel (columns / rows / cells) from a detected
table ROI plus the underlying PDF geometry (PyMuPDF text + vector primitives).
It does NOT require vertical rules: sparse-rule / booktabs tables are recovered
purely from text x/y clustering.  No translation, HTML, WeasyPrint, OCR or
vision models are involved.
"""
