# -*- coding: utf-8 -*-
"""Read-only character/font/PDF evidence for math glyph diagnostics.

This module never rewrites HTML, changes a renderer, or creates a replacement
PDF.  Its browser probes are temporary, off-canvas single-character elements
used only with Chrome DevTools' ``CSS.getPlatformFontsForNode`` API.
"""
from __future__ import annotations

import io
import re
from pathlib import Path
from typing import Any

import pymupdf
from fontTools.ttLib import TTCollection, TTFont


PT_PER_CSS_PX = 72.0 / 96.0


def codepoint(character: str | None) -> str | None:
    return "U+%04X" % ord(character) if character else None


def bbox_pt(rect: dict[str, Any] | None) -> list[float]:
    if not rect:
        return []
    return [round(float(rect[key]) * PT_PER_CSS_PX, 3)
            for key in ("x", "y", "right", "bottom")]


def extract_pdf_characters(pdf_path: str | Path, page_index: int = 0
                           ) -> list[dict[str, Any]]:
    """Flatten PyMuPDF raw character records with their real span font."""
    document = pymupdf.open(str(pdf_path))
    try:
        raw = document[int(page_index)].get_text("rawdict")
    finally:
        document.close()
    output = []
    order = 0
    for block in raw.get("blocks") or []:
        for line in block.get("lines") or []:
            line_chars = []
            for span in line.get("spans") or []:
                for char in span.get("chars") or []:
                    value = str(char.get("c") or "")
                    if not value:
                        continue
                    row = {
                        "character": value,
                        "unicode_codepoint": codepoint(value),
                        "font": span.get("font"),
                        "font_size": round(float(span.get("size") or 0), 3),
                        "bbox": [round(float(item), 3)
                                 for item in char.get("bbox") or []],
                        "origin": [round(float(item), 3)
                                   for item in char.get("origin") or []],
                        "order": order,
                    }
                    output.append(row)
                    line_chars.append(row)
                    order += 1
            if line_chars:
                line_text = "".join(row["character"] for row in line_chars)
                for row in line_chars:
                    row["line_text"] = line_text
    return output


def find_pdf_sequence(characters: list[dict[str, Any]], needle: str,
                      occurrence: int = 0) -> list[dict[str, Any]]:
    """Find a Unicode sequence in reading-order PDF character records."""
    haystack = "".join(row["character"] for row in characters)
    start = -1
    cursor = 0
    for _ in range(int(occurrence) + 1):
        start = haystack.find(needle, cursor)
        if start < 0:
            return []
        cursor = start + len(needle)
    return characters[start:start + len(needle)]


def _probe_script() -> str:
    return r"""(payload) => {
      const rect = r => ({x:r.x,y:r.y,width:r.width,height:r.height,
                          right:r.right,bottom:r.bottom});
      const textMatches = (root, needle) => {
        const output=[];
        const walker=document.createTreeWalker(root,NodeFilter.SHOW_TEXT);
        let node;
        while(node=walker.nextNode()){
          let cursor=0;
          while(cursor<=node.data.length){
            const start=node.data.indexOf(needle,cursor);
            if(start<0) break;
            output.push({node,start});
            cursor=start+Math.max(needle.length,1);
          }
        }
        return output;
      };
      const styleRecord = cs => ({
        font_family:cs.fontFamily,font_size:cs.fontSize,
        font_style:cs.fontStyle,font_weight:cs.fontWeight,
        font_stretch:cs.fontStretch,font_variant:cs.fontVariant,
        line_height:cs.lineHeight
      });
      const makeProbe = (character,cs,id) => {
        const probe=document.createElement('span');
        probe.id=id;
        probe.textContent=character;
        probe.setAttribute('data-math-font-audit-probe','true');
        Object.assign(probe.style,{
          position:'fixed',left:'-2000px',top:'0',opacity:'0.01',
          pointerEvents:'none',whiteSpace:'pre',zIndex:'-2147483648',
          fontFamily:cs.fontFamily,fontSize:cs.fontSize,
          fontStyle:cs.fontStyle,fontWeight:cs.fontWeight,
          fontStretch:cs.fontStretch,fontVariant:cs.fontVariant,
          lineHeight:cs.lineHeight
        });
        document.body.appendChild(probe);
        return probe;
      };
      const records=[];
      let probeIndex=0;
      for(const spec of payload.locators){
        const root=document.querySelector(spec.selector);
        if(!root){
          records.push({sample_id:spec.sample_id,found:false,
                        reason:'selector_not_found'});
          continue;
        }
        const matches=textMatches(root,spec.needle);
        const hit=matches[spec.occurrence||0];
        if(!hit){
          records.push({sample_id:spec.sample_id,found:false,
                        reason:'needle_not_found',needle:spec.needle,
                        root_text:root.innerText||root.textContent||''});
          continue;
        }
        const parent=hit.node.parentElement;
        const cs=getComputedStyle(parent);
        const whole=document.createRange();
        whole.setStart(hit.node,hit.start);
        whole.setEnd(hit.node,hit.start+spec.needle.length);
        let utf16=hit.start;
        const chars=[];
        for(const character of Array.from(spec.needle)){
          const range=document.createRange();
          range.setStart(hit.node,utf16);
          range.setEnd(hit.node,utf16+character.length);
          const probeId='math-font-audit-probe-'+probeIndex++;
          const probe=makeProbe(character,cs,probeId);
          chars.push({character,probe_id:probeId,
                      dom_rect:rect(range.getBoundingClientRect()),
                      probe_rect:rect(probe.getBoundingClientRect())});
          utf16+=character.length;
        }
        records.push({sample_id:spec.sample_id,found:true,
                      selector:spec.selector,needle:spec.needle,
                      occurrence:spec.occurrence||0,
                      text_node_value:hit.node.data,
                      parent_tag:parent.tagName,
                      parent_class:parent.className||'',
                      css:styleRecord(cs),
                      dom_rect:rect(whole.getBoundingClientRect()),chars});
      }
      const variantRoot=document.querySelector(payload.variant_selector);
      const variantCss=getComputedStyle(variantRoot||document.body);
      const variants=[];
      for(const character of payload.variant_characters){
        const probeId='math-font-audit-probe-'+probeIndex++;
        const probe=makeProbe(character,variantCss,probeId);
        variants.push({character,probe_id:probeId,
                       probe_rect:rect(probe.getBoundingClientRect()),
                       css:styleRecord(variantCss)});
      }
      return {records,variants,document_fonts_status:document.fonts.status};
    }"""


def capture_chromium_font_trace(
        html_path: str | Path, locators: list[dict[str, Any]],
        variant_selector: str, variant_characters: list[str],
        ) -> dict[str, Any]:
    """Capture computed CSS and the actual platform font for each glyph."""
    from playwright.sync_api import sync_playwright

    html_path = Path(html_path).resolve()
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(args=["--no-sandbox",
                                                   "--disable-gpu"])
        page = browser.new_page(viewport={"width": 900, "height": 1200})
        page.goto(html_path.as_uri(), wait_until="networkidle")
        page.evaluate("document.fonts && document.fonts.ready")
        page.wait_for_function(
            "Array.from(document.images).every(image => image.complete)")
        result = page.evaluate(_probe_script(), {
            "locators": locators,
            "variant_selector": variant_selector,
            "variant_characters": variant_characters,
        })
        session = page.context.new_cdp_session(page)
        session.send("DOM.enable")
        session.send("CSS.enable")
        root_id = session.send(
            "DOM.getDocument", {"depth": -1, "pierce": True})["root"][
                "nodeId"]

        def attach_platform_fonts(row: dict[str, Any]) -> None:
            selector = "#" + str(row["probe_id"])
            node_id = session.send("DOM.querySelector", {
                "nodeId": root_id, "selector": selector})["nodeId"]
            response = session.send(
                "CSS.getPlatformFontsForNode", {"nodeId": node_id})
            fonts = response.get("fonts") or []
            row["platform_fonts"] = fonts
            used = [font for font in fonts
                    if int(font.get("glyphCount") or 0) > 0]
            row["actual_fallback_font"] = (
                used[0].get("familyName") if used else None)
            row["actual_font_is_custom"] = (
                bool(used[0].get("isCustomFont")) if used else None)

        for record in result.get("records") or []:
            if not record.get("found"):
                continue
            record["dom_bbox_pt"] = bbox_pt(record.get("dom_rect"))
            for row in record.get("chars") or []:
                row["unicode_codepoint"] = codepoint(row.get("character"))
                row["dom_bbox_pt"] = bbox_pt(row.get("dom_rect"))
                attach_platform_fonts(row)
        for row in result.get("variants") or []:
            row["unicode_codepoint"] = codepoint(row.get("character"))
            row["probe_bbox_pt"] = bbox_pt(row.get("probe_rect"))
            attach_platform_fonts(row)
        browser.close()
    result["html_path"] = str(html_path)
    return result


def _name_values(font: TTFont) -> set[str]:
    output = set()
    if "name" not in font:
        return output
    for record in font["name"].names:
        if record.nameID not in (1, 2, 4, 6, 16, 17):
            continue
        try:
            value = record.toUnicode().strip()
        except Exception:  # noqa: BLE001 - corrupt font name is evidence only
            continue
        if value:
            output.add(value)
    return output


def locate_platform_font(family_name: str,
                         font_root: str | Path = "C:/Windows/Fonts"
                         ) -> dict[str, Any] | None:
    """Find a Windows font face by its internal family/full/PostScript name."""
    target = re.sub(r"\s+", " ", family_name).strip().casefold()
    candidates = []
    for path in sorted(Path(font_root).glob("*")):
        if path.suffix.lower() not in (".ttf", ".ttc", ".otf"):
            continue
        try:
            collection = (TTCollection(str(path), lazy=True)
                          if path.suffix.lower() == ".ttc" else None)
            faces = (list(enumerate(collection.fonts)) if collection
                     else [(0, TTFont(str(path), lazy=True))])
            for index, font in faces:
                names = _name_values(font)
                normalized = {re.sub(r"\s+", " ", value).strip().casefold()
                              for value in names}
                if target in normalized:
                    regular = any(value.casefold() in ("regular", "normal")
                                  for value in names)
                    candidates.append({
                        "path": str(path), "font_index": index,
                        "names": sorted(names), "regular": regular,
                    })
            if collection:
                collection.close()
            else:
                faces[0][1].close()
        except Exception:  # noqa: BLE001 - skip unreadable font containers
            continue
    if not candidates:
        return None
    candidates.sort(key=lambda row: (not row["regular"], row["path"],
                                     row["font_index"]))
    return candidates[0]


def glyph_evidence(font_path: str | Path, font_index: int,
                   character: str) -> dict[str, Any]:
    path = Path(font_path)
    if path.suffix.lower() == ".ttc":
        font = TTCollection(str(path), lazy=False).fonts[int(font_index)]
    else:
        font = TTFont(str(path), fontNumber=int(font_index), lazy=False)
    cp = ord(character)
    cmap = font.getBestCmap() or {}
    glyph_name = cmap.get(cp)
    glyph_id = font.getGlyphID(glyph_name) if glyph_name else 0
    advance = None
    left_bearing = None
    if glyph_name and "hmtx" in font:
        advance, left_bearing = font["hmtx"].metrics.get(
            glyph_name, (None, None))
    units_per_em = int(font["head"].unitsPerEm) if "head" in font else None
    alias_match = re.fullmatch(r"uni([0-9A-Fa-f]{4,6})", glyph_name or "")
    alias_codepoint = int(alias_match.group(1), 16) if alias_match else None
    alias_mismatch = bool(alias_codepoint is not None and alias_codepoint != cp)
    missing = []
    if not glyph_name:
        missing.append("codepoint_absent_from_cmap")
    if glyph_id == 0:
        missing.append("glyph_id_is_notdef_or_zero")
    if advance is not None and int(advance) == 0:
        missing.append("zero_advance_width")
    if alias_mismatch:
        missing.append("cmap_alias_points_to_%s" % codepoint(
            chr(alias_codepoint)))
    supported = bool(glyph_name and glyph_id > 0 and (advance or 0) > 0
                     and not alias_mismatch)
    result = {
        "font_path": str(path),
        "font_index": int(font_index),
        "font_names": sorted(_name_values(font)),
        "character": character,
        "unicode_codepoint": codepoint(character),
        "cmap_entry_present": bool(glyph_name),
        "glyph_name": glyph_name,
        "glyph_id": glyph_id,
        "advance_width_font_units": advance,
        "left_side_bearing_font_units": left_bearing,
        "units_per_em": units_per_em,
        "glyph_supported": supported,
        "missing_glyph_evidence": missing,
    }
    font.close()
    return result


def platform_font_glyph_trace(
        browser_trace: dict[str, Any]) -> dict[str, Any]:
    """Attach system-font cmap/glyph evidence to all actual font selections."""
    selections = []
    for record in browser_trace.get("records") or []:
        for row in record.get("chars") or []:
            selections.append(row)
    selections.extend(browser_trace.get("variants") or [])
    families = sorted({str(row.get("actual_fallback_font") or "")
                       for row in selections
                       if row.get("actual_fallback_font")})
    files = {family: locate_platform_font(family) for family in families}
    glyphs = []
    for row in selections:
        family = str(row.get("actual_fallback_font") or "")
        located = files.get(family)
        evidence = (glyph_evidence(
            located["path"], located["font_index"], row["character"])
                    if located else {
                        "character": row.get("character"),
                        "unicode_codepoint": row.get("unicode_codepoint"),
                        "glyph_supported": None,
                        "missing_glyph_evidence": [
                            "actual_platform_font_file_not_located"],
                    })
        glyphs.append({
            "probe_id": row.get("probe_id"),
            "actual_fallback_font": family or None,
            "font_location": located,
            "glyph": evidence,
        })
    return {
        "platform_font_files": files,
        "glyph_records": glyphs,
    }


def pdf_font_inventory(pdf_path: str | Path,
                       page_index: int = 0) -> list[dict[str, Any]]:
    document = pymupdf.open(str(pdf_path))
    try:
        rows = []
        for values in document[int(page_index)].get_fonts(full=True):
            xref, extension, font_type, base_font, resource_name, encoding, \
                referencer = values
            rows.append({
                "xref": xref, "extension": extension,
                "font_type": font_type, "base_font": base_font,
                "resource_name": resource_name, "encoding": encoding,
                "referencer": referencer,
            })
        return rows
    finally:
        document.close()


def embedded_pdf_glyph_evidence(
        pdf_path: str | Path, xref: int, character: str) -> dict[str, Any]:
    document = pymupdf.open(str(pdf_path))
    try:
        name, extension, font_type, data = document.extract_font(int(xref))
    finally:
        document.close()
    result = {
        "xref": int(xref), "embedded_name": name,
        "extension": extension, "font_type": font_type,
        "embedded_bytes": len(data or b""),
    }
    if not data:
        result.update({
            "glyph_supported": None,
            "missing_glyph_evidence": ["embedded_font_bytes_unavailable"],
        })
        return result
    try:
        font = TTFont(io.BytesIO(data), lazy=False)
        cp = ord(character)
        cmap = font.getBestCmap() or {}
        glyph_name = cmap.get(cp)
        glyph_id = font.getGlyphID(glyph_name) if glyph_name else 0
        advance = (font["hmtx"].metrics.get(glyph_name, (None, None))[0]
                   if glyph_name and "hmtx" in font else None)
        alias = re.fullmatch(r"uni([0-9A-Fa-f]{4,6})", glyph_name or "")
        alias_cp = int(alias.group(1), 16) if alias else None
        missing = []
        if not glyph_name:
            missing.append("codepoint_absent_from_embedded_subset_cmap")
        if glyph_id == 0:
            missing.append("embedded_glyph_id_is_notdef_or_zero")
        if advance is not None and int(advance) == 0:
            missing.append("embedded_glyph_zero_advance_width")
        if alias_cp is not None and alias_cp != cp:
            missing.append("embedded_cmap_alias_points_to_%s" % codepoint(
                chr(alias_cp)))
        result.update({
            "character": character,
            "unicode_codepoint": codepoint(character),
            "glyph_name": glyph_name,
            "glyph_id": glyph_id,
            "advance_width_font_units": advance,
            "glyph_supported": bool(
                glyph_name and glyph_id > 0 and (advance or 0) > 0
                and not (alias_cp is not None and alias_cp != cp)),
            "missing_glyph_evidence": missing,
        })
        font.close()
    except Exception as error:  # noqa: BLE001 - evidence records the failure
        result.update({
            "glyph_supported": None,
            "missing_glyph_evidence": [
                "embedded_font_parse_failed: %s" % error],
        })
    return result
