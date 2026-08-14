"""HTML -> PDF using headless Chromium (Playwright).

Used as a Windows-friendly fallback for WeasyPrint, which requires native GTK
libraries that are not always present. Keeps the same (html_path, pdf_path)
signature as the WeasyPrint callers in this package.
"""

from __future__ import annotations

from pathlib import Path


def render_html_to_pdf(html_path: Path, pdf_path: Path) -> None:
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    pdf_path = Path(pdf_path).resolve()
    pdf_path.parent.mkdir(parents=True, exist_ok=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--no-sandbox", "--disable-gpu"])
        page = browser.new_page()
        page.goto(html_path.as_uri(), wait_until="networkidle")
        # Phase 4D.2C: a custom @font-face (Noto Serif SC) can race with
        # page.pdf() and intermittently produce a mostly-blank page (p002 /
        # p009 / p012 rendered with NO extractable Chinese).  Block on the
        # font + image load before printing so the text layer is deterministic.
        page.evaluate("document.fonts && document.fonts.ready")
        page.wait_for_function(
            "Array.from(document.images).every(i => i.complete)")
        data = page.pdf(print_background=True, prefer_css_page_size=True)
        pdf_path.write_bytes(data)
        browser.close()
