# -*- coding: utf-8 -*-
"""Formula placeholders + unified DeepSeek batch for Phase 4A.

* inline FormulaGroups: their source spans are removed from the paragraph
  text and replaced by ``{{FORMULA_Bn}}`` (protected token).  The formula
  raw_component_text is NEVER sent to the translator.
* display formulas: never enter paragraph translation; the renderer places
  their SVG at the original position.
* one batch mixes paragraphs and untranslated table cells; back-fill is by
  item_id (order never matters).
"""
from __future__ import annotations

import json
import time

import requests

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
BATCH_SIZE = 30
RETRY_ATTEMPTS = 3

SYSTEM_PROMPT = (
    "You translate technical paper text from English into Simplified Chinese.\n"
    "Input is a JSON array of items: [{\"item_id\": ..., \"type\": "
    "\"paragraph\"|\"table_cell\", \"source_text\": ...}, ...].\n"
    "Rules:\n"
    "1. Translate every item into accurate, natural Simplified Chinese, "
    "keeping the paper/technical meaning.\n"
    "2. Placeholders of the form {{TOKEN}} MUST be reproduced VERBATIM in the "
    "translation at their original relative position (they are formula "
    "images restored later). Never translate, drop or reorder them.\n"
    "3. Do not change numbers, digits, symbols, percentages.\n"
    "4. Preserve technical abbreviations, proper nouns, model names, code "
    "identifiers, URLs and citations as-is when standard (NLP, LLM, RAG, "
    "BERT, Transformer, GPT, arXiv, DOI, etc.).\n"
    "5. Do not add explanations, parenthetical glosses or the original "
    "English.\n"
    "6. Table cells: use compact noun-phrase style. Paragraphs: natural "
    "prose.\n"
    "7. Do not output Markdown. Plain text only.\n"
    "8. Output STRICTLY a JSON object with key \"translations\": an array of "
    "{\"item_id\": <same id as input>, \"translation\": <zh text>}. Return "
    "every item_id exactly once. Order does not matter."
)


class TranslationError(RuntimeError):
    pass


def _tokens(text):
    import re
    return re.findall(r"\{\{[A-Z_0-9]+\}\}", text or "")


def inject_placeholders(paragraphs, formula_by_span_id, formulas):
    """Replace inline-formula spans in paragraph text by {{FORMULA_Bn}}.

    ``formula_by_span_id``: {span_id: formula_id} from the page model.
    ``formulas``: list of FormulaModels (for inline vs display).
    Returns a copy of paragraphs with ``source_text`` containing placeholders
    and a list of used tokens.
    """
    inline_ids = {fm["formula_id"] for fm in formulas
                  if fm.get("placement") == "inline"}
    tokens = []
    for para in paragraphs:
        parts = []
        for sid in para["span_ids"]:
            fid = formula_by_span_id.get(sid)
            if fid is not None and fid in inline_ids:
                tok = "{{FORMULA_%s}}" % fid
                if tok not in parts:
                    parts.append(tok)
                continue
            # find the span text by id
            for ln in para["lines"]:
                for s in ln["spans"]:
                    if s["id"] == sid:
                        parts.append(s["text"])
                        break
        para["source_text"] = "".join(parts)
        tokens.extend(t for t in parts if t.startswith("{{"))
    return paragraphs, sorted(set(tokens))


def display_placeholder_map(formulas):
    """{display formula_id: '{{DISPLAY_FORMULA_Bn}}'} for reporting."""
    out = {}
    for fm in formulas:
        if fm.get("placement") != "inline":
            out[fm["formula_id"]] = "{{DISPLAY_FORMULA_%s}}" % fm["formula_id"]
    return out


def build_batch(paragraphs, table_model=None):
    """Unified item list: {item_id, type, source_text}."""
    items = []
    for p in paragraphs:
        items.append({"item_id": p["paragraph_id"], "type": "paragraph",
                      "source_text": p.get("translation_source_text")
                      or p["source_text"]})
    if table_model:
        for cell in table_model.get("cells", []):
            if cell.get("translation_status") in ("translated", "unchanged"):
                continue
            src = cell.get("source_text") or ""
            if not src.strip():
                continue
            items.append({"item_id": cell.get("cell_id"),
                          "type": "table_cell", "source_text": src})
    return items


def _chat(session, base_url, model, payload_json, timeout):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": payload_json},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    last = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = session.post(f"{base_url}/chat/completions", json=payload,
                                timeout=timeout)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            obj = json.loads(content)
            items = obj.get("translations") if isinstance(obj, dict) else obj
            if not isinstance(items, list):
                raise ValueError("unexpected shape: %r" % str(obj)[:120])
            return items
        except Exception as exc:  # noqa: BLE001
            last = exc
            time.sleep(1.5 * (attempt + 1))
    raise TranslationError("DeepSeek batch failed: %s" % last)


def translate_batch(items, *, token="", base_url=DEFAULT_BASE_URL,
                    model=DEFAULT_MODEL, dry_run=False):
    """Translate mixed items; return {item_id: zh}.  dry_run returns the
    source text unchanged (no API call)."""
    if not items:
        return {}
    if dry_run:
        return {it["item_id"]: it["source_text"] for it in items}
    s = requests.Session()
    s.headers.update({"Authorization": "Bearer %s" % token})
    out = {}
    for i in range(0, len(items), BATCH_SIZE):
        chunk = items[i:i + BATCH_SIZE]
        got = _chat(s, base_url, model,
                    json.dumps(chunk, ensure_ascii=False), 240)
        by_id = {}
        for it in got:
            if isinstance(it, dict) and it.get("item_id"):
                by_id[it["item_id"]] = str(it.get("translation") or "").strip()
        for it in chunk:
            if it["item_id"] not in by_id:
                raise TranslationError(
                    "missing translation for %s" % it["item_id"])
            out[it["item_id"]] = by_id[it["item_id"]]
    return out


def check_placeholder_preserved(zh_text, tokens):
    """Return tokens missing from a translated text."""
    return [t for t in tokens if t not in zh_text]
