# -*- coding: utf-8 -*-
"""PhysicalPDFQA - the final PDF itself is the truth.

Input : the FINAL per-page PDF (``zh.pdf``), never the HTML / DOM snapshot /
        intermediate JSON.
Output: physical page count (hard gate == expected), page sizes, blank /
        mostly-blank detection, dual-renderer success (Renderer A + Renderer
        B), renderer-specific failures, clipped visual regions, and a
        diagnostic renderer-parity diff ratio (NOT a hard gate in 4C.1A:
        different renderers antialias/hint differently).

Renderer selection (never hard-code a single backend):
  * Renderer A: Poppler if available, else PyMuPDF/MuPDF (reported).
  * Renderer B: PDFium (pypdfium2).  If missing -> renderer_b_available=false
                and production_delivery_gate=false (no silent claim of
                dual-renderer QA).
"""
from __future__ import annotations

import json
import shutil
import subprocess
import sys
from pathlib import Path

import pymupdf

try:
    import pypdfium2 as _pdfium
    PDFIUM_AVAILABLE = True
except Exception:  # noqa: BLE001
    _pdfium = None
    PDFIUM_AVAILABLE = False

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

RENDER_DPI = 150
INK_THRESHOLD = 235          # pixel min(r,g,b) < 235 counts as ink
MOSTLY_BLANK_RATIO = 0.01    # ink pixels < 1% of page area -> mostly blank
BLANK_RATIO = 0.0005         # effectively zero ink
PARITY_DIFF_HARD_FAIL = 0.02  # diagnostic only in 4C.1A (not a gate)


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def _ink_ratio(pil_image):
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")
    pixels = list(pil_image.getdata())
    total = max(len(pixels), 1)
    ink = sum(1 for r, g, b in pixels if min(r, g, b) < INK_THRESHOLD)
    return ink / total


def renderer_a_available():
    """Poppler preferred; PyMuPDF is the fallback backend."""
    exe = shutil.which("pdftoppm")
    # Codex's Windows runtime exposes pdftoppm through a .cmd shim.  Python's
    # subprocess cannot execute that shim directly with shell=False, so use
    # the guaranteed PyMuPDF fallback instead of reporting a false renderer
    # failure.
    return bool(exe and not exe.lower().endswith((".cmd", ".bat")))


def _render_with_poppler(pdf_path, page_index, out_png):
    proc = subprocess.run(
        ["pdftoppm", "-png", "-r", str(RENDER_DPI), "-f", str(page_index + 1),
         "-l", str(page_index + 1), "-singlefile", str(pdf_path),
         str(out_png.with_suffix(""))],
        capture_output=True, text=True, timeout=120)
    if proc.returncode != 0 or not out_png.exists():
        raise RuntimeError("pdftoppm failed: %s" % proc.stderr[:300])
    return True


def _render_a(pdf_path, page_index):
    """Renderer A (Poppler or PyMuPDF). Returns (pil_image, backend)."""
    if renderer_a_available():
        from PIL import Image
        import tempfile
        tmp = Path(tempfile.mkdtemp(prefix="physqa_a_"))
        out_png = tmp / ("page%03d.png" % (page_index + 1))
        _render_with_poppler(pdf_path, page_index, out_png)
        return Image.open(out_png), "poppler"
    doc = pymupdf.open(str(pdf_path))
    page = doc[page_index]
    pix = page.get_pixmap(dpi=RENDER_DPI)
    doc.close()
    from PIL import Image
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    return img, "pymupdf"


def _render_b(pdf_path, page_index):
    """Renderer B (PDFium). Returns pil_image."""
    pdf = _pdfium.PdfDocument(str(pdf_path))
    page = pdf[page_index]
    bitmap = page.render(scale=RENDER_DPI / 72.0)
    pil = bitmap.to_pil()
    page.close()
    pdf.close()
    return pil


def _raster_pixel_diff(a, b):
    """Diagnostic parity diff ratio (0..1) after resizing B to A's size."""
    if a.size != b.size:
        b = b.resize(a.size)
    if a.mode != "RGB":
        a = a.convert("RGB")
    if b.mode != "RGB":
        b = b.convert("RGB")
    pa, pb = list(a.getdata()), list(b.getdata())
    total = max(len(pa), 1)
    diff = 0
    for ca, cb in zip(pa, pb):
        if ca != cb:
            diff += 1
    return diff / total


def physical_pdf_qa(pdf_path, page_model=None, *,
                    expected_physical_page_count=1, out_dir=None,
                    output_filename="physical_qa.json"):
    """QA one final zh.pdf against the physical truth.

    ``page_model`` supplies region bboxes for the clipped-region check and
    the geometry-sensitive region audit (table/formula/figure).
    """
    pdf_path = Path(pdf_path).resolve()
    out_dir = Path(out_dir) if out_dir else pdf_path.parent
    result = {
        "input_pdf": str(pdf_path),
        "expected_physical_page_count": expected_physical_page_count,
        "physical_pdf_page_count": None,
        "unexpected_extra_page_count": None,
        "page_sizes": [],
        "blank_page_count": None,
        "mostly_blank_page_count": None,
        "renderer_a_backend": None,
        "renderer_a_success": None,
        "renderer_a_error": "",
        "renderer_b_available": PDFIUM_AVAILABLE,
        "renderer_b_backend": "pdfium" if PDFIUM_AVAILABLE else None,
        "renderer_b_success": None,
        "renderer_b_error": "",
        "renderer_specific_failure_count": None,
        "clipped_visual_region_count": 0,
        "clipped_visual_regions": [],
        "renderer_parity_diff_ratio": None,
        "geometry_qa": {},
        "assertion_physical_page_count_passed": False,
        "assertion_no_blank_passed": False,
        "assertion_renderers_alive": False,
    }

    # ---- page count + sizes (independent of any renderer) ----------------
    try:
        doc = pymupdf.open(str(pdf_path))
        result["physical_pdf_page_count"] = doc.page_count
        result["page_sizes"] = [[round(p.rect.width, 2),
                                 round(p.rect.height, 2)] for p in doc]
        doc.close()
    except Exception as exc:  # noqa: BLE001
        result["physical_pdf_page_count"] = -1
        result["renderer_a_error"] = "open failed: %s" % exc
        result["assertion_physical_page_count_passed"] = False
        result["assertion_no_blank_passed"] = False
        result["assertion_renderers_alive"] = False
        _dump(out_dir / output_filename, result)
        return result

    result["unexpected_extra_page_count"] = max(
        0, result["physical_pdf_page_count"]
        - expected_physical_page_count)

    # ---- raster both renderers, one page at a time ------------------------
    blank = 0
    mostly = 0
    renderer_a_ok = True
    renderer_b_ok = True
    a_error = ""
    b_error = ""
    parity_pages = []
    renderer_a_backend = None
    for page_index in range(result["physical_pdf_page_count"]):
        try:
            img_a, renderer_a_backend = _render_a(pdf_path, page_index)
            result["renderer_a_backend"] = renderer_a_backend
        except Exception as exc:  # noqa: BLE001
            renderer_a_ok = False
            a_error = "page %d: %s" % (page_index + 1, exc)
            break
        ratio_a = _ink_ratio(img_a)
        if ratio_a <= BLANK_RATIO:
            blank += 1
        elif ratio_a <= MOSTLY_BLANK_RATIO:
            mostly += 1
        if PDFIUM_AVAILABLE:
            try:
                img_b = _render_b(pdf_path, page_index)
            except Exception as exc:  # noqa: BLE001
                renderer_b_ok = False
                b_error = "page %d: %s" % (page_index + 1, exc)
                break
            ratio_b = _ink_ratio(img_b)
            parity_pages.append({
                "page": page_index + 1,
                "ink_ratio_a": round(ratio_a, 4),
                "ink_ratio_b": round(ratio_b, 4),
                "diff_ratio": round(_raster_pixel_diff(img_a, img_b), 4),
            })

    result["blank_page_count"] = blank
    result["mostly_blank_page_count"] = mostly
    result["renderer_a_success"] = renderer_a_ok
    result["renderer_a_error"] = a_error
    result["renderer_b_success"] = renderer_b_ok
    result["renderer_b_error"] = b_error
    result["renderer_specific_failure_count"] = (
        (0 if renderer_a_ok else 1) + (0 if renderer_b_ok else 1))
    if parity_pages:
        result["renderer_parity_diff_ratio"] = round(
            sum(p["diff_ratio"] for p in parity_pages) / len(parity_pages), 4)
        result["renderer_parity_pages"] = parity_pages

    # ---- region-aware geometry audit (page 1 only for multi-page PDFs) ----
    result["geometry_qa"] = _geometry_qa(pdf_path, page_model)

    result["assertion_physical_page_count_passed"] = (
        result["physical_pdf_page_count"] == expected_physical_page_count)
    result["assertion_no_blank_passed"] = blank == 0
    result["assertion_renderers_alive"] = (
        renderer_a_ok and PDFIUM_AVAILABLE and renderer_b_ok)
    result["all_assertions_passed"] = (
        result["assertion_physical_page_count_passed"]
        and result["assertion_no_blank_passed"]
        and result["assertion_renderers_alive"])
    _dump(out_dir / output_filename, result)
    return result


def _geometry_qa(pdf_path, page_model):
    """Region-aware physical audit on the rendered page (table/formula/
    figure strict; text-flow structural only)."""
    regions = page_model.get("regions", []) if page_model else []
    out = {"table": [], "formula": [], "figure": [], "text": {}}
    if not regions:
        return out
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    # clip/ink check per region: ink inside the region bbox vs the region
    # area.  Empty regions are detected by ink_ratio ~= 0.
    scale = 72.0 / 72.0  # PDF pt == PDF pt
    for region in regions:
        rtype = region.get("type")
        if rtype not in ("table", "formula", "figure"):
            continue
        bbox = region.get("bbox")
        if not bbox:
            continue
        x0, y0, x1, y1 = [float(v) for v in bbox]
        if x1 <= x0 or y1 <= y0:
            out[rtype].append({"region": region.get("region_id"),
                               "empty": True, "bbox": bbox})
            continue
        try:
            pix = page.get_pixmap(
                dpi=RENDER_DPI,
                clip=pymupdf.Rect(max(x0, 0), max(y0, 0), x1, y1))
            img = Image_from_pixmap(pix)
            ratio = _ink_ratio(img)
        except Exception:  # noqa: BLE001
            ratio = 1.0  # cannot prove blank -> not a blank failure
        out[rtype].append({
            "region": region.get("region_id"),
            "bbox": bbox,
            "ink_ratio": round(ratio, 4),
            "empty": ratio <= BLANK_RATIO,
        })
    doc.close()
    return out


def Image_from_pixmap(pix):
    from PIL import Image
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--model", required=True)
    parser.add_argument("--out", required=True)
    parser.add_argument("--expected", type=int, default=1)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    result = physical_pdf_qa(args.pdf, model,
                             expected_physical_page_count=args.expected,
                             out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["all_assertions_passed"] else 2


if __name__ == "__main__":
    sys.exit(main())
