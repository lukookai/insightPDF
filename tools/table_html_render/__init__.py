"""Phase 2A: English-locked HTML round-trip for a reconstructed TableModel.

Independent of the translation pipeline -- no DeepSeek, no translation, no
Chinese, no font auto-shrink, no overflow repair.  Consumes a TableModel JSON
(see tools/table_reconstruction) and proves that the model carries enough
geometry to recreate the original English table via HTML -> PDF.
"""
