"""QA visualization for a reconstructed TableModel.

Renders the original page and overlays:
  * detected table ROI (yellow),
  * inferred column boundaries (cyan vertical lines),
  * inferred row boundaries (magenta horizontal lines),
  * each cell bbox (green) with an "r{c}r{c}" label,
  * column index labels (C0..) above the ROI and row index labels (R0..) left.
"""

from __future__ import annotations


def render_reconstruction(pdf, page, model, out_path, zoom=2.0):
    import pymupdf
    from PIL import Image, ImageDraw

    doc = pymupdf.open(pdf)
    pg = doc[page - 1]
    mat = pymupdf.Matrix(zoom, zoom)
    pix = pg.get_pixmap(matrix=mat)
    mode = "RGBA" if pix.alpha else "RGB"
    img = Image.frombytes(mode, (pix.width, pix.height), pix.samples)
    if mode == "RGBA":
        img = img.convert("RGB")
    draw = ImageDraw.Draw(img)

    def X(x):
        return x * zoom

    def Y(y):
        return y * zoom

    roi = model["bbox"]

    # column boundary x set
    col_x = set()
    for c in model["columns"]:
        col_x.add(round(c["x0"], 3))
        col_x.add(round(c["x1"], 3))
    # row boundary y set
    row_y = set()
    for r in model["rows"]:
        row_y.add(round(r["y0"], 3))
        row_y.add(round(r["y1"], 3))

    # column boundaries (cyan)
    for x in col_x:
        draw.line([(X(x), Y(roi[1])), (X(x), Y(roi[3]))],
                  fill=(0, 180, 180), width=2)
    # row boundaries (magenta)
    for y in row_y:
        draw.line([(X(roi[0]), Y(y)), (X(roi[2]), Y(y))],
                  fill=(200, 0, 200), width=2)
    # cells (green) + label
    for c in model["cells"]:
        b = c["bbox"]
        draw.rectangle([X(b[0]), Y(b[1]), X(b[2]), Y(b[3])],
                       outline=(0, 160, 0), width=1)
        tag = "r%dc%d" % (c["row"], c["col"])
        if c.get("is_header"):
            tag += "*"
        draw.text((X(b[0]) + 2, Y(b[1]) + 1), tag, fill=(0, 110, 0))
    # ROI (yellow)
    draw.rectangle([X(roi[0]), Y(roi[1]), X(roi[2]), Y(roi[3])],
                   outline=(230, 200, 0), width=2)
    # column index labels above ROI
    for c in model["columns"]:
        cx = (c["x0"] + c["x1"]) / 2.0
        draw.text((X(cx) - 6, Y(roi[1]) - 14), "C%d" % c["index"],
                  fill=(0, 120, 120))
    # row index labels left of ROI
    for r in model["rows"]:
        cy = (r["y0"] + r["y1"]) / 2.0
        draw.text((X(roi[0]) - 20, Y(cy) - 5), "R%d" % r["index"],
                  fill=(150, 0, 150))

    doc.close()
    img.save(out_path)
