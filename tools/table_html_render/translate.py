"""Cell-level translation for Phase 2B.

* Cell content classification: pure numbers / percentages are kept verbatim
  and never sent to DeepSeek.  Only text cells are translated.
* Batch translation: one HTTP round-trip per batch of cells, payload is a
  JSON array ``[{cell_id, source}, ...]``; DeepSeek must answer with
  ``[{cell_id, translation}, ...]`` and results are back-filled **by cell_id**
  (order never matters).
* temperature = 0, thinking disabled, ``response_format`` = json_object.
* Short-translation retry: only for cells that overflowed after the normal
  translation rendered at the original font size.
"""

from __future__ import annotations

import json
import re
import time

import requests

DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"
BATCH_SIZE = 30
RETRY_ATTEMPTS = 3

_NUM_RE = re.compile(r"^[-+]?[\d,]+(\.\d+)?$")
_PCT_RE = re.compile(r"^[-+]?[\d,]+(\.\d+)?%$")

SYSTEM_PROMPT = (
    "You are a technical paper table translator. Translate English cell text "
    "into Simplified Chinese for a table cell.\n"
    "Rules:\n"
    "1. Translate into concise Simplified Chinese, keeping the paper/technical "
    "meaning accurate.\n"
    "2. Do not add any explanation or commentary.\n"
    "3. Do not add parenthetical notes, glosses or the original English.\n"
    "4. Do not change any number, digit, symbol or percentage.\n"
    "5. Preserve technical abbreviations and proper nouns as-is when they are "
    "standard in the field (e.g. NLP, LLM, PLMs, RAG, BERT, Transformer, "
    "model names, dataset names, code identifiers).\n"
    "6. Do not output Markdown. Do not wrap in fences. Plain text only.\n"
    "7. Table titles and headings: prefer the compact translation style "
    "typical of table cells (short, no full sentences).\n"
    "8. Output STRICTLY a JSON object with key \"translations\", whose value "
    "is an array of objects, each exactly {\"cell_id\": <same id as input>, "
    "\"translation\": <translated text>}. Return every cell_id from the input "
    "exactly once. Order does not matter."
)

SHORT_SYSTEM_PROMPT = (
    "You are a technical paper table translator. A previous translation is "
    "too long for its table cell and overflows. Rewrite the given translation "
    "into a MORE COMPACT Chinese table-cell rendering.\n"
    "Rules:\n"
    "1. Keep the technical meaning, key entities, method names and any "
    "qualifying meaning. Do NOT drop numbers.\n"
    "2. Use short noun phrases, omit function words, drop non-essential "
    "modifiers.\n"
    "3. Do not add explanations, parentheses, or the original English.\n"
    "4. Preserve standard technical abbreviations (NLP, LLM, RAG, etc.).\n"
    "5. Output STRICTLY a JSON object with key \"translations\": an array of "
    "{\"cell_id\": ..., \"translation\": ...}. Return every cell_id exactly once."
)


class TranslationError(RuntimeError):
    pass


def classify_cell_content(text: str) -> str:
    """Return 'numeric' | 'percent' | 'text' | 'empty'."""
    t = (text or "").strip()
    if not t:
        return "empty"
    if _PCT_RE.fullmatch(t):
        return "percent"
    if _NUM_RE.fullmatch(t):
        return "numeric"
    return "text"


def _client(base_url: str, token: str, model: str, timeout: int = 240):
    s = requests.Session()
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s, base_url, model, timeout


def _chat_completion(session, base_url, model, system_prompt, user_payload,
                     timeout):
    payload = {
        "model": model,
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_payload},
        ],
        "thinking": {"type": "disabled"},
        "temperature": 0,
        "response_format": {"type": "json_object"},
    }
    last_err = None
    for attempt in range(RETRY_ATTEMPTS):
        try:
            resp = session.post(f"{base_url}/chat/completions", json=payload,
                                timeout=timeout)
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            obj = json.loads(content)
            if isinstance(obj, dict) and "translations" in obj:
                items = obj["translations"]
            elif isinstance(obj, list):
                items = obj
            else:
                raise ValueError("unexpected JSON shape: %r" % (str(obj)[:120]))
            return items
        except Exception as exc:  # noqa: BLE001 - retry on any failure
            last_err = exc
            time.sleep(1.5 * (attempt + 1))
    raise TranslationError(f"DeepSeek batch failed: {last_err}")


def _parse_items(items, batch):
    """Back-fill by cell_id. Raises if any required id is missing."""
    by_id = {}
    for it in items:
        if isinstance(it, dict) and it.get("cell_id"):
            by_id[it["cell_id"]] = str(it.get("translation") or "").strip()
    result = {}
    for cell in batch:
        cid = cell["cell_id"]
        if cid not in by_id:
            raise TranslationError(f"missing translation for {cid}")
        result[cid] = by_id[cid]
    return result


def translate_batch(batch, *, base_url=DEFAULT_BASE_URL, token="", model=DEFAULT_MODEL):
    """Translate a list of {cell_id, source} dicts; return {cell_id: zh}."""
    if not batch:
        return {}
    session, base, mdl, timeout = _client(base_url, token, model)
    out = {}
    for i in range(0, len(batch), BATCH_SIZE):
        chunk = batch[i:i + BATCH_SIZE]
        payload = json.dumps(chunk, ensure_ascii=False)
        items = _chat_completion(session, base, mdl, SYSTEM_PROMPT, payload,
                                 timeout)
        out.update(_parse_items(items, chunk))
    return out


def translate_short(batch, *, base_url=DEFAULT_BASE_URL, token="",
                    model=DEFAULT_MODEL):
    """Compact retry for overflowed cells; same back-fill contract."""
    if not batch:
        return {}
    session, base, mdl, timeout = _client(base_url, token, model)
    out = {}
    for i in range(0, len(batch), BATCH_SIZE):
        chunk = batch[i:i + BATCH_SIZE]
        payload = json.dumps(chunk, ensure_ascii=False)
        items = _chat_completion(session, base, mdl, SHORT_SYSTEM_PROMPT,
                                 payload, timeout)
        out.update(_parse_items(items, chunk))
    return out
