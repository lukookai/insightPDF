# -*- coding: utf-8 -*-
"""4D.1A exploration: p001 source span structure + x-projection."""
import pymupdf
from pathlib import Path

doc = pymupdf.open("runs/diag_src_2504.pdf")
page = doc[0]
W, H = page.rect.width, page.rect.height
print("page size: %.1f x %.1f pt" % (W, H))

# all text spans with bbox + font + size, sorted by y
spans = []
for block in page.get_text("dict").get("blocks", []):
    if block.get("type") == 1:
        continue
    for line in block.get("lines", []):
        for span in line.get("spans", []):
            t = (span.get("text") or "").strip()
            if not t:
                continue
            spans.append({
                "text": t[:60], "bbox": [round(v, 2) for v in span["bbox"]],
                "font": span.get("font"), "size": round(span.get("size", 0), 1)})
spans.sort(key=lambda s: (s["bbox"][1], s["bbox"][0]))
print("n spans:", len(spans))
for s in spans:
    b = s["bbox"]
    print("y=%6.1f x0=%6.1f x1=%6.1f sz=%4.1f %-28s | %s" % (
        (b[1] + b[3]) / 2, b[0], b[2], s["size"], s["font"][:26], s["text"]))
doc.close()
