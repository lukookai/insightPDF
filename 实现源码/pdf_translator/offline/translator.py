from __future__ import annotations

import hashlib
import json
import re
import threading
from pathlib import Path


_PROTECTED = re.compile(
    r"("
    r"https?://\S+|www\.\S+|\bdoi:\s*\S+|\b10\.\d{4,9}/\S+|"
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}|"
    r"\[\s*\d+(?:\s*[,;–—-]\s*\d+)*\s*\]|"
    r"\{\s*v\s*\d+\s*\}|"
    r"\b(?:[A-Z]{2,}[A-Za-z0-9-]*|[A-Za-z]+[A-Z][A-Za-z0-9-]*|"
    r"[A-Za-z]+-[A-Za-z0-9]*\d[A-Za-z0-9-]*)\b"
    r")"
)

_DIRECT_TRANSLATIONS = {
    "abstract": "摘要",
    "introduction": "引言",
    "related work": "相关工作",
    "method": "方法",
    "methods": "方法",
    "experiments": "实验",
    "experiment": "实验",
    "results": "结果",
    "conclusion": "结论",
    "conclusions": "结论",
    "limitations": "局限性",
    "references": "参考文献",
    "acknowledgements": "致谢",
    "acknowledgments": "致谢",
}

_LIGATURES = str.maketrans(
    {
        "ﬁ": "fi",
        "ﬂ": "fl",
        "ﬀ": "ff",
        "ﬃ": "ffi",
        "ﬄ": "ffl",
    }
)


def _clean_source(text: str) -> str:
    text = text.translate(_LIGATURES).replace("\u00ad", "")
    text = re.sub(r"(?<=[A-Za-z])-\s+(?=[a-z])", "", text)
    return re.sub(r"\s+", " ", text).strip()


def _clean_chinese(text: str) -> str:
    text = re.sub(r"\s+([，。；：！？、）】])", r"\1", text)
    text = re.sub(r"([（【])\s+", r"\1", text)
    text = re.sub(r"(?<=[\u3400-\u9fff])\s+(?=[\u3400-\u9fff])", "", text)
    text = text.replace(" ,", "，").replace(" .", "。")
    return text.strip()


def _heading_translation(text: str) -> str | None:
    match = re.fullmatch(r"\s*((?:\d+(?:\.\d+)*)\.?)?\s*([A-Za-z ]+)\s*", text)
    if not match:
        return None
    heading = _DIRECT_TRANSLATIONS.get(match.group(2).strip().casefold())
    if not heading:
        return None
    number = (match.group(1) or "").strip()
    return f"{number} {heading}".strip()


def _split_chunks(text: str, maximum: int = 360) -> list[str]:
    if len(text) <= maximum:
        return [text]
    sentences = re.split(r"(?<=[.!?;:])\s+", text)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        if len(sentence) > maximum:
            pieces = [
                sentence[index : index + maximum]
                for index in range(0, len(sentence), maximum)
            ]
        else:
            pieces = [sentence]
        for piece in pieces:
            candidate = f"{current} {piece}".strip()
            if current and len(candidate) > maximum:
                chunks.append(current)
                current = piece
            else:
                current = candidate
    if current:
        chunks.append(current)
    return chunks


class OfflineCTranslate2Translator:
    """Small local NMT adapter with a persistent JSON cache.

    The expected model is the official Argos en→zh package unpacked locally.
    No model hub, HTTP endpoint, or LLM is consulted at runtime.
    """

    def __init__(
        self,
        model_dir: Path,
        *,
        cache_path: Path | None = None,
        compute_type: str = "int8",
    ):
        import ctranslate2
        import sentencepiece

        self.model_dir = Path(model_dir).resolve()
        if not self.model_dir.is_dir():
            raise FileNotFoundError(f"离线翻译模型目录不存在：{self.model_dir}")
        ctranslate_model = self.model_dir / "model"
        if not ctranslate_model.is_dir():
            ctranslate_model = self.model_dir
        sentencepiece_model = next(
            (
                candidate
                for candidate in (
                    self.model_dir / "sentencepiece.model",
                    self.model_dir / "sentencepiece.model.bin",
                    ctranslate_model / "sentencepiece.model",
                )
                if candidate.is_file()
            ),
            None,
        )
        if sentencepiece_model is None:
            raise FileNotFoundError(
                f"模型目录缺少 sentencepiece.model：{self.model_dir}"
            )
        self.processor = sentencepiece.SentencePieceProcessor(
            model_file=str(sentencepiece_model)
        )
        self.engine = ctranslate2.Translator(
            str(ctranslate_model), device="cpu", compute_type=compute_type
        )
        self.cache_path = Path(cache_path).resolve() if cache_path else None
        self.cache: dict[str, str] = {}
        if self.cache_path and self.cache_path.is_file():
            self.cache = json.loads(self.cache_path.read_text(encoding="utf-8"))
        self.lock = threading.Lock()
        self.calls = 0
        self.cache_hits = 0
        self.failures: list[dict] = []

    @staticmethod
    def _key(text: str) -> str:
        return hashlib.sha256(text.encode("utf-8")).hexdigest()

    def _translate_plain(self, text: str) -> str:
        if not text.strip():
            return text
        pieces = []
        for chunk in _split_chunks(text):
            tokens = self.processor.encode(chunk, out_type=str)
            if not tokens:
                continue
            result = self.engine.translate_batch(
                [tokens], beam_size=4, max_decoding_length=512
            )[0]
            pieces.append(self.processor.decode(result.hypotheses[0]))
        return _clean_chinese("".join(pieces))

    def _translate_with_protection(self, text: str) -> str:
        parts = _PROTECTED.split(text)
        output: list[str] = []
        for part in parts:
            if not part:
                continue
            if _PROTECTED.fullmatch(part):
                output.append(part)
            else:
                output.append(self._translate_plain(part))
        translated = "".join(output)
        translated = re.sub(r"\s+([，。；：！？、）】])", r"\1", translated)
        translated = re.sub(r"([（【])\s+", r"\1", translated)
        return _clean_chinese(translated)

    def translate(self, source: str) -> str:
        source = _clean_source(source)
        if not source:
            return ""
        direct = _heading_translation(source)
        if direct:
            return direct
        key = self._key(source)
        with self.lock:
            cached = self.cache.get(key)
            if cached is not None:
                self.cache_hits += 1
                return cached
            self.calls += 1
            try:
                translated = self._translate_with_protection(source)
            except Exception as exc:
                self.failures.append({"source": source, "error": str(exc)})
                translated = source
            if not translated.strip():
                self.failures.append({"source": source, "error": "empty output"})
                translated = source
            self.cache[key] = translated
            self._save_cache()
            return translated

    def _save_cache(self) -> None:
        if not self.cache_path:
            return
        self.cache_path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.cache_path.with_suffix(self.cache_path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(self.cache, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        temporary.replace(self.cache_path)

    def stats(self) -> dict:
        return {
            "engine": "CTranslate2/Argos en-zh 1.9",
            "model_dir": str(self.model_dir),
            "device": "cpu",
            "external_api_requests": 0,
            "new_translations": self.calls,
            "cache_hits": self.cache_hits,
            "failures": self.failures,
        }
