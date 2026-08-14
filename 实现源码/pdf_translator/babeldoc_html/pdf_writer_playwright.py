"""HTML -> PDF renderer driven by Playwright + system Chrome.

This is the **HTML-middleware** path: the translated HTML produced by
``build_babeldoc_html`` (absolutely-positioned ``<article>`` blocks over a
redacted source background) is rendered to PDF by a real browser engine, so the
PDF is genuinely generated *from* the HTML — not by a parallel PyMuPDF pass.

The PyMuPDF renderer (``pdf_writer_pymupdf``) is kept as a fallback only.
"""
from __future__ import annotations

import sys
from pathlib import Path

from playwright.sync_api import sync_playwright


def _launch_browser(p):
    # Prefer the system Chrome / Edge install: a real engine, no extra download,
    # and it can load system CJK fonts (Noto Serif SC) for correct glyphs.
    for chan in ("chrome", "msedge"):
        try:
            return p.chromium.launch(channel=chan, headless=True)
        except Exception as exc:  # noqa: BLE001
            print(f"[playwright] channel={chan} unavailable: {exc}", file=sys.stderr)
    # Fallback to Playwright's bundled Chromium if it happens to be installed.
    return p.chromium.launch(headless=True)


def render_pdf(html_path: str | Path, pdf_path: str | Path) -> bool:
    """Render an HTML file to PDF using a headless browser.

    Page size / margins come from the HTML's own ``@page`` CSS
    (``prefer_css_page_size=True``). ``print_background=True`` keeps the
    redacted source background image.
    """
    html_path = Path(html_path)
    pdf_path = Path(pdf_path)
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = _launch_browser(p)
        try:
            page = browser.new_page()
            # Load via file:// so relative asset paths (background PNG) resolve.
            page.goto(html_path.resolve().as_uri(), wait_until="networkidle")
            page.pdf(
                path=str(pdf_path),
                print_background=True,
                prefer_css_page_size=True,
                margin={"top": "0", "bottom": "0", "left": "0", "right": "0"},
            )
        finally:
            browser.close()

    ok = pdf_path.exists() and pdf_path.stat().st_size > 0
    if not ok:
        raise RuntimeError(f"Playwright did not produce a PDF at {pdf_path}")
    return ok
