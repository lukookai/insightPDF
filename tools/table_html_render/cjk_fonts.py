"""Detect the first actually-available CJK serif font on this machine.

Windows / macOS / Linux candidates are checked in priority order by scanning
well-known font file paths (plus the per-user Windows font directory).  The
first font whose file exists wins; the full candidate stack is still passed to
the browser, so any machine with e.g. Noto Serif CJK installed picks it up even
if this probe misses the exact path.

The probe result is recorded in the report as requested / rendered so the
browser substitution is never silent.
"""

from __future__ import annotations

import os
from pathlib import Path

# (css_family, [candidate file paths]) in priority order.
CJK_SERIF_CANDIDATES = [
    ("SimSun", [r"C:\Windows\Fonts\simsun.ttc", r"C:\Windows\Fonts\SIM SUN.TTC"]),
    ("SimSun-ExtB", [r"C:\Windows\Fonts\simsunb.ttf"]),
    ("NSimSun", [r"C:\Windows\Fonts\simsun.ttc"]),
    ("Songti SC", [
        "/System/Library/Fonts/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
    ]),
    ("Noto Serif CJK SC", [
        r"C:\Windows\Fonts\NotoSerifCJKsc-Regular.otf",
        "/usr/share/fonts/opentype/noto/NotoSerifCJK-Regular.ttc",
        "/usr/share/fonts/noto-cjk/NotoSerifCJK-Regular.ttc",
    ]),
    ("Source Han Serif SC", [
        r"C:\Windows\Fonts\SourceHanSerifSC-Regular.otf",
        "/usr/share/fonts/opentype/source-han-serif/SourceHanSerifSC-Regular.otf",
    ]),
]

FALLBACK_CJK_SERIF = ["Noto Serif CJK SC", "Source Han Serif SC", "SimSun", "serif"]


def _user_font_dir() -> list[str]:
    d = os.environ.get("LOCALAPPDATA")
    if d:
        p = Path(d) / "Microsoft" / "Windows" / "Fonts"
        if p.is_dir():
            return [str(p)]
    return []


def detect_cjk_serif() -> dict:
    """Return the chosen CJK serif family + the CSS stack.

    ``chosen`` is None when no known CJK font file was found (the stack then
    falls back to generic serif; the rendered PDF is still inspected afterwards
    so the report carries what the browser actually used).
    """
    extra_dirs = _user_font_dir()
    chosen = None
    for family, paths in CJK_SERIF_CANDIDATES:
        for p in paths:
            if os.path.exists(p):
                chosen = family
                break
        if chosen:
            break
    if chosen is None:
        # fall back to a broad name-based scan of the font directories
        seen = set()
        scan_dirs = [r"C:\Windows\Fonts", "/System/Library/Fonts",
                     "/System/Library/Fonts/Supplemental",
                     "/usr/share/fonts/opentype", "/usr/share/fonts/truetype"]
        scan_dirs += extra_dirs
        name_hints = ["simsun", "songti", "notoserifcjk", "sourcehanserif"]
        for d in scan_dirs:
            if not os.path.isdir(d):
                continue
            for f in os.listdir(d):
                low = f.lower()
                if any(h in low for h in name_hints) and low.endswith((".ttf", ".ttc", ".otf")):
                    if f not in seen:
                        seen.add(f)
        if seen:
            ordered = sorted(seen)
            # prefer a simsun/songti (serif) over everything else
            for f in ordered:
                if "simsun" in f.lower() or "songti" in f.lower():
                    chosen = "SimSun"
                    break
            if chosen is None:
                chosen = Path(ordered[0]).stem.replace("-", " ").title()

    stack = []
    for family, _paths in CJK_SERIF_CANDIDATES:
        if family == chosen:
            stack.append(family)
    for f in FALLBACK_CJK_SERIF:
        if f not in stack:
            stack.append(f)
    return {
        "chosen": chosen,
        "css_stack": stack,
        "css_family": ",".join("'%s'" % s for s in stack),
        "probe": {family: any(os.path.exists(p) for p in paths)
                  for family, paths in CJK_SERIF_CANDIDATES},
    }


def cjk_font_css() -> str:
    return detect_cjk_serif()["css_family"]
