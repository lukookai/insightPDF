# -*- coding: utf-8 -*-
"""ResidualSourceLanguageQA - the FINAL zh.pdf must not retain untranslated
English in translatable regions (heading / body / caption / list_item /
appendix).

Operates on the final PDF text layer (NOT translation JSON).  Each text
span is classified by its containing region role (RegionMap from the
stitched PageModel); translatable roles are checked for residual English.

Exclusions (protected/nontranslatable):
* authors / affiliation lines
* bibliography / reference entries
* URL / DOI / arXiv ids
* code / model names / identifiers (LLM, AutoSurvey, DeepSeek-R1, ...)
* formula placeholders / math glyphs

Residual heuristics:
* >= 20 consecutive alphabetic chars of English
* >= 4 English words forming an ordinary sentence
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

import pymupdf

HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
for path in (HERE, REPO / "tools" / "table_html_render",
             REPO / "tools" / "formula_html_render",
             REPO / "实现源码" / "pdf_translator"):
    if str(path) not in sys.path:
        sys.path.insert(0, str(path))

CJK_RE = re.compile(r"[\u4e00-\u9fff\u3400-\u4dbf]")
ENGLISH_RUN_RE = re.compile(r"[A-Za-z]{4,}")
ENGLISH_SENTENCE_RE = re.compile(
    r"(?:\b[A-Za-z][A-Za-z'’.-]*\b\s*){4,}")
# protected tokens that may stay English inside any region
PROTECTED_TOKEN_RE = re.compile(
    r"\b(?:LLM|LLMs|AutoSurvey|SurveyEval|DeepSeek-R1|DeepSeek|GPT|RAG|"
    r"NLP|BERT|Transformer|arXiv|DOI|Wang|Liu|Zhang|Li|Chen|He|Zhou|Bai|"
    r"Min|Fu|Ren|Shi|Gu|Hu|Jiang|Lu|Shao|Wu|Xu|Yu|Zhao|Zhu|Krishna|"
    r"SurveyEval|IB|CLAIM|SOURCE|Test-Time|Table|Figure|Fig\.?|Eq\.?|"
    r"Section|Appendix|References|Limitations|Conclusion|Introduction|"
    r"Abstract|Algorithm|Model|Dataset|Density|Faithfulness|Relevance|"
    r"Language|Criticism|Structure|Content|Coverage|InfoDensity|"
    r"self_refine|top_k|nomic|best_of|convolution|entropy|digest|"
    r"AutoSurvey|overview|Qwen|Qwen2\.5|Mistral|Llama|Gemma|T5|BART|"
    r"Pegasus|Long-form|long-to-long|short-to-long|Test-time|time|"
    r"Instruct|AWQ|YARN)\b", re.I)
# model identifier with dashed/plus suffix: Qwen2.5-72B-Instruct-AWQ-YARN-128k
MODEL_IDENT_RE = re.compile(
    r"\b[A-Za-z0-9]+(?:[-+][A-Za-z0-9]+){2,}\b"
    r"|\bVanilla\+Skeleton\b")
URL_RE = re.compile(r"https?://\S+|www\.\S+|doi\.org/\S+|\b\d{4}\.\d{4,}\b")

# translatable region roles (must be translated)
TRANSLATABLE_ROLES = {"heading", "body", "caption", "list_item",
                      "appendix_heading", "appendix_body", "table_caption",
                      "figure_caption"}
# roles that are allowed to stay English (metadata / references)
NON_TRANSLATABLE_ROLES = {"author", "affiliation", "bibliography",
                          "reference", "footer", "header"}


def _norm_eq(text):
    """Normalise for exact source==translation comparison (heading audit)."""
    t = MODEL_IDENT_RE.sub(" ", text or "")
    t = PROTECTED_TOKEN_RE.sub(" ", t)
    t = URL_RE.sub(" ", t)
    t = re.sub(r"\{\{[A-Z_0-9]+\}\}", " ", t)
    t = re.sub(r"[0-9]+[.,]?[0-9]*", " ", t)
    t = re.sub(r"[^\w\s]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip().lower()


def _para_rendered_text(page_model, pid, pdf_path):
    """Gather the final-PDF text spans belonging to paragraph ``pid`` (bbox
    or content match via _pdf_spans_by_role).  Returns joined text or ''."""
    try:
        mapped, _ = _pdf_spans_by_role(pdf_path, page_model)
    except Exception:  # noqa: BLE001
        return ""
    parts = [s["text"] for s in mapped if s.get("para") == pid]
    return " ".join(parts).strip() if parts else ""


def _pdf_plain_text(pdf_path):
    """All text of the final PDF page (for residual-token search)."""
    try:
        doc = pymupdf.open(str(pdf_path))
        page = doc[0]
        text = page.get_text("text")
        doc.close()
        return re.sub(r"\s+", " ", text)
    except Exception:  # noqa: BLE001
        return ""


def _residual_tokens(source, rendered, pdf_path=None, page_model=None,
                     para_scoped=False):
    """English lexical tokens from ``source`` that survive verbatim into the
    rendered PDF text (Phase 4C.2R section 19).  Protected identifiers
    (model names, code, citations, formula children) are subtracted first;
    the remaining source-language tokens must NOT appear in the final text.

    ``rendered``: the paragraph's rendered text if the para could be mapped
    in the final PDF; if empty (flow moved the para, or bbox mismatch), the
    whole-page plain text is searched instead -- a token that appears ANY-
    WHERE in the final PDF is a residue (sections 19/20: no third state).

    ``para_scoped``: True when ``rendered`` is the paragraph's OWN text.
    In that mode even connective stopwords ("and", "if") count -- a bare
    ", and" fragment rendered in the final PDF (p14) is a residual, not a
    protected token.  The STOPWORDS filter applies ONLY to the whole-page
    fallback search, where "and"/"the" would otherwise match bibliography
    entries whose English is intentional.
    """
    if pdf_path is not None and not rendered:
        rendered = _pdf_plain_text(pdf_path)
    if not rendered:
        return []
    # Phase 4C.2R: search only the NON-CJK fragments of the rendered text.
    # A translated page still carries legitimate English proper nouns inside
    # Chinese sentences ("Web", "Vanilla+Skeleton") -- those are NOT
    # residual.  Only English tokens sitting in a purely-English fragment
    # (the source was never translated at all, e.g. p15 'where') count.
    # Split the rendered text into runs; keep only runs with no CJK.  CJK
    # punctuation (。，：、（）) glues to the surrounding word, so a run like
    # '。•Vanilla+Skeleton：' still surrounds the name with CJK marks --
    # drop runs that are adjacent to CJK punctuation.
    cjk_splits = re.split(r"[\u4e00-\u9fff\u3400-\u4dbf]+", rendered)
    english_only = []
    for run in cjk_splits:
        if not run.strip():
            continue
        core = run.strip(" \t\r\n。，：；、！？（）()\"'‘’“”·•∙▪●")
        if len(core) >= 2:
            english_only.append(run)
    if not english_only:
        return []
    src_clean = MODEL_IDENT_RE.sub(" ", source or "")
    src_clean = PROTECTED_TOKEN_RE.sub(" ", src_clean)
    src_clean = URL_RE.sub(" ", src_clean)
    src_clean = re.sub(r"\{\{[A-Z_0-9]+\}\}", " ", src_clean)
    src_clean = re.sub(r"\([^()]*?(?:et\s+al\.?|,\s*(?:19|20)\d{2}[a-z]?\s*)[^()]*?\)",
                       " ", src_clean)
    src_clean = re.sub(r"(?:19|20)\d{2}[a-z]?[\s;,]+", " ", src_clean)
    src_clean = re.sub(r"[0-9]+[.,]?[0-9]*", " ", src_clean)
    src_clean = re.sub(r"[^\w\s'’.-]", " ", src_clean)
    tokens = [t for t in re.split(r"\s+", src_clean.strip()) if t]
    # keep only meaningful English words (>=2 letters, alphabetic)
    tokens = [t for t in tokens if re.fullmatch(r"[A-Za-z'’.-]{2,}", t)]
    if not para_scoped:
        tokens = [t for t in tokens if t.lower() not in STOPWORDS]
    if not tokens:
        return []
    low = " ".join(english_only).lower()
    return sorted({t for t in tokens
                   if re.search(r"(?<![a-z0-9+])%s(?![a-z0-9+])" % re.escape(t),
                                low)})


STOPWORDS = {
    "the", "of", "and", "or", "to", "in", "for", "on", "with", "as", "by",
    "is", "are", "was", "were", "be", "been", "it", "its", "at", "from",
    "that", "this", "these", "those", "we", "our", "a", "an", "each",
    "their", "they", "using", "which", "while", "such",
    # NOTE: "where" is deliberately NOT a stopword -- a bare "where" under
    # a display formula is a formula-semantic condition token that must be
    # absorbed into the FormulaGroup or translated (p15), never left as a
    # floating English residue.
}

# Phase 4C.2R.1: short connective tokens that are never acceptable as a
# bare English fragment inside a translatable region of the final PDF
# (p14 renders a bare ", and" -- the untranslated tail of a formula
# condition sentence).  Prepositions that legitimately appear inside kept
# identifiers ("long-to-long", "Test-Time") are deliberately NOT listed.
SHORT_RESIDUAL_CONNECTIVES = {
    "and", "or", "if", "where", "when", "while", "whereas", "but",
    "without", "otherwise", "thus", "hence", "therefore", "moreover",
    "however", "namely", "furthermore", "since", "because", "although",
    "though", "unless", "until", "among", "between", "within", "versus",
    "regardless",
}


def _short_connective_tokens(text):
    """Connective tokens present in a non-CJK span (Phase 4C.2R.1)."""
    clean = _norm_english(text)
    words = [w.lower() for w in clean.split()
             if re.fullmatch(r"[A-Za-z'’.-]{2,}", w)]
    return sorted({w for w in words if w in SHORT_RESIDUAL_CONNECTIVES})


def _norm_english(text):
    """Remove protected tokens, URLs, citations, numbers, formula tags."""
    t = MODEL_IDENT_RE.sub(" ", text or "")  # model ids FIRST (dashed names)
    t = PROTECTED_TOKEN_RE.sub(" ", t)
    t = URL_RE.sub(" ", t)
    t = re.sub(r"\{\{[A-Z_0-9]+\}\}", " ", t)
    t = re.sub(r"\(\s*(?:19|20)\d{2}[a-z]?\s*\)", " ", t)  # (2023)
    # (Bai et al., 2024b; Shao et al., 2024; ...) citation groups
    t = re.sub(r"\([^()]*?(?:et\s+al\.?|,\s*(?:19|20)\d{2}[a-z]?\s*)[^()]*?\)",
               " ", t)
    t = re.sub(r"et al\.?", " ", t)
    # citation fragments without parentheses (line-wrapped citations):
    # "al.,2024b; Shao et al., 2024; Xi et al.,2024"
    t = re.sub(r"\bal\.?,?\s*(?:19|20)\d{2}[a-z]?[\s;,]*", " ", t)
    t = re.sub(r"(?:19|20)\d{2}[a-z]?[\s;,]+", " ", t)
    t = re.sub(r"[0-9]+[.,]?[0-9]*", " ", t)
    t = re.sub(r"[^\w\s'’.-]", " ", t)
    t = re.sub(r"\s+", " ", t)
    return t.strip()


def _residual_details(text):
    """Return list of residual English fragments."""
    clean = _norm_english(text)
    out = []
    for m in ENGLISH_RUN_RE.finditer(clean):
        if len(m.group(0)) >= 20:
            out.append({"type": "long_run", "text": m.group(0)})
    words = clean.split()
    if len(words) >= 4:
        out.append({"type": "english_sentence", "text": " ".join(words[:16])})
    return out


def _pdf_spans_by_role(pdf_path, page_model):
    """Map final-PDF text spans to region roles.

    Bbox mapping is unreliable because source-PDF coordinates differ from
    rendered-PDF coordinates (translation reflows paragraphs).  We first try
    bbox; when a paragraph's source text actually appears in the PDF text
    layer, we attach the paragraph id by CONTENT match instead.
    """
    doc = pymupdf.open(str(pdf_path))
    page = doc[0]
    spans = []
    for block in page.get_text("dict").get("blocks", []):
        if block.get("type") == 1:
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                t = (span.get("text") or "").strip()
                if not t:
                    continue
                spans.append({"text": t,
                              "bbox": [float(v) for v in span["bbox"]],
                              "font": span.get("font") or ""})
    doc.close()

    # RegionMap: translatable paragraph bboxes + roles
    regions = []
    para_texts = {}  # paragraph_id -> normalized source prefix
    semantic_roles = {}  # paragraph_id -> document semantic_role (4E.1B)
    for region in page_model.get("regions", []):
        if region.get("type") == "text":
            para = region["payload"]
            role = para.get("style_role") or "body"
            regions.append({"bbox": [float(v) for v in para.get("bbox", [])],
                            "role": role,
                            "para": para.get("paragraph_id"),
                            "semantic_role": para.get("semantic_role") or ""})
            semantic_roles[para.get("paragraph_id")] = \
                para.get("semantic_role") or ""
            # normalized 12-token prefix of the SOURCE text (English stays
            # identical in the PDF for untranslated regions; for translated
            # regions we fall back to bbox)
            src = re.sub(r"\{\{[A-Z_0-9]+\}\}", "", para.get("source_text") or "")
            words = re.findall(r"[A-Za-z][A-Za-z'’.\-]{2,}", src)
            if words:
                para_texts[para.get("paragraph_id")] = " ".join(words[:12]).lower()
        elif region.get("type") == "formula":
            fm = region["payload"]
            b = fm.get("layout_bbox")
            if b:
                regions.append({"bbox": [float(v) for v in b],
                                "role": "formula",
                                "para": fm.get("formula_id")})

    def role_of(bbox):
        best = None
        best_area = 0
        for rg in regions:
            x = min(bbox[2], rg["bbox"][2]) - max(bbox[0], rg["bbox"][0])
            y = min(bbox[3], rg["bbox"][3]) - max(bbox[1], rg["bbox"][1])
            area = max(x, 0) * max(y, 0)
            if area > best_area:
                best_area = area
                best = rg
        return (best["role"], best["para"]) if best else ("unmapped", None)

    out = []
    for s in spans:
        role, para = role_of(s["bbox"])
        # content-match fallback: if this span's text starts a known
        # paragraph's source prefix, attach that paragraph id
        low = s["text"].lower()
        low_clean = re.sub(r"[^a-z'’.\- ]", "", low)
        if para is None or not para_texts.get(para):
            for pid, prefix in para_texts.items():
                words = [w for w in low_clean.split() if len(w) >= 3]
                probe = " ".join(words[:6])
                if probe and len(probe) >= 12 and probe in prefix:
                    para = pid
                    break
        out.append({**s, "role": role, "para": para})
    return out, semantic_roles


def residual_source_language_qa(page_model, pdf_path, out_dir=None,
                                flows=None):
    """Audit the final PDF for residual English in translatable regions."""
    out_dir = Path(out_dir) if out_dir else Path(pdf_path).parent
    from translation_status import is_reference_item
    # bibliography/reference paragraphs are exempt: their English is
    # intentional (entries are protected_nontranslatable).  We collect both
    # the paragraph ids AND their author-name prefixes so continuation lines
    # ("Jialong Wu, Runnan Fang, ..." of a Zekun Xi entry) are also exempt.
    ref_paras = set()
    ref_prefixes = []
    ref_bboxes = []
    for region in page_model.get("regions", []):
        if region.get("type") == "text":
            para = region["payload"]
            src = para.get("source_text") or ""
            if is_reference_item({"type": "paragraph", "source_text": src}):
                ref_paras.add(para.get("paragraph_id"))
                ref_bboxes.append([float(v) for v in para.get("bbox", [])])
                words = re.findall(r"[A-Z][A-Za-z'’.\-]+", src)[:3]
                if words:
                    ref_prefixes.append(" ".join(words).lower())
    # pure author-name list (capitalized names separated by commas) is a
    # bibliography continuation, even without paragraph mapping; likewise
    # "Name Surname. 2025. Title..." lines inside a reference entry.
    AUTHOR_LIST_RE = re.compile(
        r"^[A-Z][a-z'’.\-]+(?:\s+[A-Z][a-z'’.\-]+)?"
        r"(?:,\s*[A-Z][a-z'’.\-]+(?:\s+[A-Z][a-z'’.\-]+)?){1,5}"
        r"(?:\s+(?:and|&)\s+[A-Z][a-z'’.\-]+(?:\s+[A-Z][a-z'’.\-]+)?)?")
    REF_TAIL_RE = re.compile(
        r"^[A-Z][a-z'’.\-]+(?:\.\s+|\s+[A-Z][a-z'’.\-]+\.\s+)"
        r"(?:19|20)\d{2}[a-z]?\.",
        re.M)
    # Phase 4D.2C: the balanced Latin font fragments one bibliography entry
    # into many small spans, so source-bbox proximity and content-prefix
    # mapping both fail for title/venue continuation lines.  The RENDERED
    # reference flow boxes (flow positions of is_reference paragraphs) are the
    # authoritative exemption region -- a span inside one is bibliography
    # text regardless of how the font fragments it.
    ref_flow_boxes = []
    for flow in flows or []:
        f_x0 = float(flow.get("col_x0", 0.0))
        f_x1 = float(flow.get("col_x1", f_x0))
        for item in flow.get("items", []):
            if item.get("kind") != "paragraph" or not item.get("is_reference"):
                continue
            r_y0 = float(item.get("flow_y", 0.0))
            r_y1 = r_y0 + max(float(item.get("est_height", 0.0)), 1.0)
            ref_flow_boxes.append([f_x0, r_y0, f_x1, r_y1])
    if ref_flow_boxes:
        ref_bboxes = [list(b) for b in ref_flow_boxes] + [
            [float(v) for v in para.get("bbox", [])] for para in
            (r["payload"] for r in page_model.get("regions", [])
             if r.get("type") == "text" and is_reference_item(
                 {"type": "paragraph",
                  "source_text": r["payload"].get("source_text") or ""}))]

    # ---- Phase 4C.2R-C: heading-exact + residual-token audit --------------
    untranslated_headings = []
    residual_tokens = []
    para_regions = [r for r in page_model.get("regions", [])
                    if r.get("type") == "text"]
    for region in para_regions:
        para = region["payload"]
        role = para.get("style_role") or "body"
        src = (para.get("source_text") or "").strip()
        tr = (para.get("translated_text") or "").strip()
        if not src or CJK_RE.search(src):
            continue
        if is_reference_item({"type": "paragraph", "source_text": src}):
            continue
        pid = para.get("paragraph_id")
        # heading exact: English source, translatable role; if the FINAL
        # rendered text (PDF text layer, not the model field) still equals
        # the source, the heading was never translated -> HARD FAIL.
        if role in ("heading", "appendix_heading") and src:
            rendered_para = _para_rendered_text(page_model, pid, pdf_path)
            if rendered_para and not CJK_RE.search(rendered_para) \
                    and _norm_eq(src) == _norm_eq(rendered_para):
                untranslated_headings.append({
                    "para": pid, "role": role, "text": src[:120],
                    "translation": rendered_para[:120],
                    "bbox": [round(v, 1) for v in para.get("bbox", [])]})
        # residual tokens: English lexical tokens in a translatable para
        # whose source tokens survive into the final PDF text layer
        # (Phase 4C.2R section 19: any source token still appearing in the
        # final PDF counts -- no sentence-length threshold; spans in
        # reference/formula/author regions are excluded separately).
        if role in ("heading", "body", "caption", "appendix_heading",
                    "appendix_body", "list_item", "table_caption",
                    "figure_caption"):
            # Phase 4C.2R: if the paragraph's OWN translation already has
            # Chinese, any surviving English words are legitimate proper
            # nouns kept by the translator ("Web" in "Web应用程序") -- not
            # residual.  Only paragraphs that were NEVER translated
            # (translation empty / equals source) are audited.
            if tr and CJK_RE.search(tr):
                continue
            para_span = _para_rendered_text(page_model, pid, pdf_path)
            if para_span and CJK_RE.search(para_span):
                continue  # rendered into Chinese -> proper nouns only
            # para_scoped: the para's own rendered text is precise -- even
            # connective stopwords count (p14 bare ", and" fragment).
            tokens = _residual_tokens(src, para_span, pdf_path, page_model,
                                      para_scoped=bool(para_span))
            for tok in tokens:
                residual_tokens.append({
                    "para": pid, "role": role, "token": tok,
                    "source": src[:120],
                    "rendered": (para_span or _pdf_plain_text(pdf_path))[:160]})

    # de-duplicate (para, token): the paragraph-level audit and the
    # span-level connective audit can both report the same residue.
    _seen, _uniq = set(), []
    for _t in residual_tokens:
        _key = (_t.get("para"), _t.get("token"))
        if _key not in _seen:
            _seen.add(_key)
            _uniq.append(_t)
    residual_tokens = _uniq

    mapped, semantic_roles = _pdf_spans_by_role(pdf_path, page_model)

    # spans that look like bibliography tails ("Name Surname. 2025.")
    # Phase 4D.2C: the balanced Latin font fragments a bibliography line into
    # many small spans ("Zekun Xi," "Wenbiao Yin," ... "2025."), so a
    # per-span REF_TAIL_RE check misses the "Author. 2025." tail.  Group the
    # spans per visual line and run the reference-tail pattern on the
    # concatenated line text instead.
    _line_groups = {}
    for s in mapped:
        key = round((s["bbox"][1] + s["bbox"][3]) / 2, 0)
        _line_groups.setdefault(key, []).append(s)
    ref_tail_spans = []
    for grp in _line_groups.values():
        grp = sorted(grp, key=lambda s: s["bbox"][0])
        line_text = "".join(s["text"] for s in grp)
        if REF_TAIL_RE.search(line_text):
            ref_tail_spans.append({
                "bbox": [min(s["bbox"][0] for s in grp),
                         min(s["bbox"][1] for s in grp),
                         max(s["bbox"][2] for s in grp),
                         max(s["bbox"][3] for s in grp)]})
    ref_tail_bboxes = [s["bbox"] for s in ref_tail_spans]

    residual = {"body": [], "heading": [], "caption": [], "list_item": [],
                "appendix": [], "other": []}
    english_count = 0
    total_english_chars = 0

    for s in mapped:
        role = s["role"]
        text = s["text"]
        if CJK_RE.search(text):
            continue  # already has Chinese - not a residual block
        if role in NON_TRANSLATABLE_ROLES:
            continue
        # FrontMatter author-name rows are protected named entities, not
        # translatable prose.  They can contain more than four lexical words.
        _name_words = re.findall(r"[A-Z][a-z]{1,}", text)
        if len(_name_words) >= 3 and len(_name_words) == len(
                re.findall(r"[A-Za-z][A-Za-z'\-]*", text)):
            continue
        if role == "formula":
            continue
        if s.get("para") in ref_paras:
            continue  # bibliography entry: English is intended
        if semantic_roles.get(s.get("para"), "").startswith("reference"):
            continue  # Phase 4E.1B: document semantic state is the truth
        low = re.sub(r"\s+", " ", text).strip().lower()
        # Phase 4D.2C: a bibliography continuation line may START on the final
        # author connective ("... and Maosong Sun. 2024. ...") when the line
        # break lands on "and".  Re-check the reference patterns after
        # dropping a leading connective -- the rest is an author-year tail.
        probe = re.sub(r"^(?:and|&)\s+", "", low)
        if ref_prefixes and any(low.startswith(p) for p in ref_prefixes):
            continue  # continuation line of a bibliography entry
        if ref_prefixes and probe != low and any(
                probe.startswith(p) for p in ref_prefixes):
            continue
        if AUTHOR_LIST_RE.match(text):
            continue  # author-name continuation of a bibliography entry
        if REF_TAIL_RE.search(text):
            continue  # "Name Surname. 2025." tail of a bibliography entry
        if probe != low and (AUTHOR_LIST_RE.match(
                probe[:1].upper() + probe[1:])
                or REF_TAIL_RE.search(probe[:1].upper() + probe[1:])):
            continue
        # title/venue continuation line right below a bibliography tail
        # ("Omnithink: Expanding" -> "knowledge boundaries in machine
        # writing") - same column, within one line-height below
        if ref_tail_bboxes:
            sb = s["bbox"]
            near_tail = False
            for tb in ref_tail_bboxes:
                x_overlap = min(sb[2], tb[2]) - max(sb[0], tb[0])
                y_gap = sb[1] - tb[3]
                if x_overlap > 20 and 0 <= y_gap <= 45:
                    near_tail = True
                    break
            if near_tail:
                continue
        # bbox proximity to a reference paragraph (source-coordinate based)
        if ref_bboxes:
            sb = s["bbox"]
            near_ref = False
            for rb in ref_bboxes:
                x_overlap = min(sb[2], rb[2]) - max(sb[0], rb[0])
                y_gap = sb[1] - rb[3]
                if x_overlap > 20 and -5 <= y_gap <= 40:
                    near_ref = True
                    break
            if near_ref:
                continue
        # Phase 4D.2C: span CENTER inside a RENDERED reference flow box is
        # bibliography text regardless of how the font fragments the entry
        # (the source-bbox check above can miss reflowed continuation lines).
        if ref_flow_boxes:
            sb = s["bbox"]
            cx = (sb[0] + sb[2]) / 2
            cy = (sb[1] + sb[3]) / 2
            if any(fb[0] - 2 <= cx <= fb[2] + 2 and fb[1] - 2 <= cy <= fb[3] + 2
                   for fb in ref_flow_boxes):
                continue
        # Phase 4C.2R.1: a bare connective token inside a Chinese-free span
        # of a translatable region is a residual fragment (p14 renders the
        # untranslated tail ", and" of a formula-condition sentence at
        # y~630, mapped by bbox to a neighbouring paragraph).  Span-level
        # detection is independent of paragraph mapping.
        # Phase 4D.2C: the balanced Latin font fragments citations into small
        # spans, so a connective can land in its own span ("Tishby and",
        # "and Maosong Sun", "Wang et al.").  A connective adjacent to a
        # capitalized author name is a citation fragment, NOT a residual
        # fragment -- exempt it.
        _author_conn = re.compile(
            r"(?:[A-Z][a-z]{1,}(?:[.']|,)?\s+)?"
            r"(?:and|&|et\s+al\.?)(?:\s+[A-Z][a-z]{1,})?")
        if _author_conn.search(text):
            continue
        span_conn = _short_connective_tokens(text)
        if span_conn:
            # attribute to the fragment's true SOURCE paragraph when an
            # exact source match exists (p14's ", and" tail belongs to
            # DLP00183 though its bbox overlaps DLP00184)
            owner = s.get("para")
            norm_span = _norm_eq(text)
            for region in para_regions:
                p2 = region["payload"]
                if norm_span and _norm_eq(p2.get("source_text") or "") == norm_span:
                    owner = p2.get("paragraph_id")
                    break
            residual_tokens.append({
                "para": owner, "role": role,
                "token": span_conn[0], "source": "",
                "rendered": text[:160],
                "span_bbox": [round(v, 1) for v in s["bbox"]],
            })
            continue
        details = _residual_details(text)
        if not details:
            continue
        english_count += 1
        total_english_chars += len(re.sub(r"\s+", "", text))
        bucket = "body"
        if role in ("heading", "appendix_heading"):
            bucket = "heading"
        elif role in ("caption", "table_caption", "figure_caption"):
            bucket = "caption"
        elif role == "list_item":
            bucket = "list_item"
        elif role.startswith("appendix"):
            bucket = "appendix"
        else:
            bucket = "other"
        residual[bucket].append({
            "role": role, "para": s.get("para"),
            "text": text[:120], "bbox": [round(v, 1) for v in s["bbox"]],
            "details": details[:2]})

    result = {
        "residual_untranslated_body_count": len(residual["body"])
        + len(residual["other"]),
        "residual_untranslated_heading_count": len(residual["heading"]),
        "residual_untranslated_caption_count": len(residual["caption"]),
        "residual_untranslated_list_item_count": len(residual["list_item"]),
        "residual_untranslated_appendix_count": len(residual["appendix"]),
        "residual_english_span_count": english_count,
        "residual_english_char_count": total_english_chars,
        "untranslated_heading_exact_count": len(untranslated_headings),
        "untranslated_residual_token_count": len(residual_tokens),
        "short_residual_token_count": len(residual_tokens),
        "untranslated_heading_exact_details": untranslated_headings[:12],
        "untranslated_residual_token_details": residual_tokens[:16],
        "details": residual,
        "residual_clean": (
            not residual["body"] and not residual["other"]
            and not residual["heading"] and not residual["caption"]
            and not residual["list_item"] and not residual["appendix"]
            and not untranslated_headings and not residual_tokens),
    }
    if out_dir:
        _dump(out_dir / "residual_source_language_qa.json", result)
    return result


def _dump(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2),
                    encoding="utf-8")


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--model", required=True)
    parser.add_argument("--pdf", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()
    model = json.loads(Path(args.model).read_text(encoding="utf-8"))
    result = residual_source_language_qa(model, args.pdf, out_dir=args.out)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["residual_clean"] else 2


if __name__ == "__main__":
    sys.exit(main())
