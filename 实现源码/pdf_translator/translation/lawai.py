from __future__ import annotations

import os

import requests


DEFAULT_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-v4-flash"


def get_translation_api_key(base_url: str = DEFAULT_BASE_URL) -> str:
    """Return the configured translation credential without persisting it."""

    if translation_backend_name(base_url) == "deepseek":
        return os.environ.get("DEEPSEEK_API_KEY", "").strip()
    return os.environ.get("LAWAI_AUTH_TOKEN", "").strip()


def translation_backend_name(base_url: str) -> str:
    return "deepseek" if "api.deepseek.com" in base_url.lower() else "lawai"


class LawAIError(RuntimeError):
    pass


class LawAIClient:
    """Translation client.

    The historical class name is retained to avoid breaking existing pipeline
    imports.  DeepSeek's OpenAI-compatible API is now the default backend;
    passing the legacy LawAI base URL keeps the previous request format.
    """

    def __init__(
        self,
        base_url: str,
        token: str,
        timeout: int = 180,
        model: str | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.model = model or os.environ.get("DEEPSEEK_MODEL", DEFAULT_MODEL)
        self.is_deepseek = "api.deepseek.com" in self.base_url.lower()
        self.session = requests.Session()
        self.session.headers.update({"Authorization": f"Bearer {token}"})

    def _raise_for_status(self, response: requests.Response) -> None:
        try:
            response.raise_for_status()
        except requests.HTTPError as exc:
            try:
                payload = response.json()
                detail = payload.get("detail", payload)
            except ValueError:
                detail = response.text
            raise LawAIError(
                f"翻译请求失败：HTTP {response.status_code}，{detail}"
            ) from exc

    def verify(self) -> dict:
        if self.is_deepseek:
            response = self.session.get(
                f"{self.base_url}/models", timeout=self.timeout
            )
            self._raise_for_status(response)
            payload = response.json()
            models = {
                item.get("id")
                for item in payload.get("data", [])
                if isinstance(item, dict)
            }
            if self.model not in models:
                raise LawAIError(f"DeepSeek 模型不可用：{self.model}")
            return {"backend": "deepseek", "model": self.model}
        response = self.session.get(
            f"{self.base_url}/api/v2/version", timeout=self.timeout
        )
        self._raise_for_status(response)
        return response.json()

    def translate_text(
        self,
        text: str,
        *,
        source_language: str = "en",
        target_language: str = "zh-CN",
    ) -> str:
        if self.is_deepseek:
            language_names = {
                "en": "English",
                "zh-CN": "Simplified Chinese",
                "zh-TW": "Traditional Chinese",
            }
            source_name = language_names.get(source_language, source_language)
            target_name = language_names.get(target_language, target_language)
            response = self.session.post(
                f"{self.base_url}/chat/completions",
                json={
                    "model": self.model,
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                f"Translate {source_name} into {target_name}. "
                                "Return only the translated text, without explanations "
                                "or Markdown fences. Preserve LaTeX, formulas, variable "
                                "names, code, URLs, citation keys, numeric values, and "
                                "placeholders such as {v1} exactly. Keep paragraph and "
                                "list structure. Do not omit content."
                            ),
                        },
                        {"role": "user", "content": text},
                    ],
                    "thinking": {"type": "disabled"},
                    "temperature": 0,
                },
                timeout=self.timeout,
            )
            self._raise_for_status(response)
            payload = response.json()
            try:
                translated = payload["choices"][0]["message"]["content"]
            except (KeyError, IndexError, TypeError) as exc:
                raise LawAIError("DeepSeek 接口没有返回有效译文") from exc
            if not isinstance(translated, str) or not translated.strip():
                raise LawAIError("DeepSeek 接口没有返回有效译文")
            return translated.strip()

        response = self.session.post(
            f"{self.base_url}/api/v2/text_translate",
            params=[
                ("text", text),
                ("src_language", source_language),
                ("to_language", target_language),
                ("temp_dict", ""),
                ("dict_names", "default"),
            ],
            timeout=self.timeout,
        )
        self._raise_for_status(response)
        translated = response.json().get("text")
        if not isinstance(translated, str) or not translated.strip():
            raise LawAIError("翻译接口没有返回有效译文")
        return translated.strip()
