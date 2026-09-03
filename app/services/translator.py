"""Translator providers: local (koboldcpp) и deepseek (OpenAI-совместимый).

Общий контракт: translate(text) -> str. Промпт — как в проверенных прогонах
translate_zh_chunks_local.py (raw-формат для HY-MT / chat-формат для своей модели).
"""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request

from .. import config
from ..repositories.sqlite_repo import repo

PROMPT_PREFIX = (
    "Translate the following Chinese web novel fragment into natural, fluent Russian. "
    "Preserve all meaning, names, dialogue, paragraph breaks, chapter headings, and tone. "
    "Do not summarize. Do not omit lines. Do not add comments, explanations, Markdown, or notes. "
    "Translate from the first line through the final line of the fragment; do not stop early. "
    "Output only the Russian translation.\n\n"
)

# Метки-префиксы, которые модель иногда добавляет к ответу
_STRIP_PREFIXES = [
    "Russian translation:", "Translation:", "Перевод:", "Русский перевод:",
]


class TranslatorError(RuntimeError):
    """Транзиентная ошибка (retry)."""


class FatalTranslatorError(RuntimeError):
    """Необратимая ошибка: ключ/баланс/формат — retry не поможет."""


def clean_translation(text: str) -> str:
    cleaned = text.strip()
    for prefix in _STRIP_PREFIXES:
        if cleaned.lower().startswith(prefix.lower()):
            cleaned = cleaned[len(prefix):].lstrip()
    return cleaned


def _current_prompt() -> str:
    """Системный промпт из настроек (редактируется в админке), fallback на константу."""
    return repo.get_setting("deepseek_system_prompt") or PROMPT_PREFIX


def _revise_prompt(feedback: str) -> str:
    """Промпт для пере-перевода с учётом замечания пользователя."""
    base = _current_prompt().strip()
    feedback = (feedback or "").strip()
    if feedback:
        base += (
            "\n\nThe user requested a NEW translation of this fragment because the previous one "
            "did not satisfy them. Their request/notes: " + feedback + "\n"
            "Translate the fragment again from scratch according to the request. "
            "Output only the new Russian translation."
        )
    return base


class LocalProvider:
    """koboldcpp: /v1/completions (raw) или /v1/chat/completions (своя модель)."""

    name = "local"

    def __init__(self) -> None:
        self.base = repo.get_setting("kobold_base_url", config.DEFAULT_SETTINGS["kobold_base_url"])
        self.model = repo.get_setting("kobold_model", config.DEFAULT_SETTINGS["kobold_model"])
        self.context = int(repo.get_setting("kobold_context_tokens", "8192"))
        self.chat_format = repo.get_setting("kobold_chat_format", "raw") == "chat"

    def _post(self, path: str, payload: dict, timeout: int) -> dict:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(
            self.base + path, data=data, headers={"Content-Type": "application/json"}
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.URLError as exc:
            raise TranslatorError(f"koboldcpp недоступен ({self.base}): {exc}") from exc

    def token_count(self, text: str) -> int:
        try:
            return int(self._post("/api/extra/tokencount", {"prompt": text}, timeout=60)["value"])
        except Exception:
            # грубая оценка: zh ~0.65 ток/символ
            return int(len(text) * 0.65)

    def translate(self, text: str, source_tokens: int | None = None, instruction: str | None = None) -> str:
        if not self._ping():
            raise TranslatorError("koboldcpp не отвечает — запустите run/01_start_model.cmd")
        source_tokens = source_tokens or self.token_count(text)
        max_tokens = max(1024, min(7800, int(source_tokens * 2.4) + 600))
        system_prompt = _revise_prompt(instruction) if instruction else _current_prompt().strip()
        if self.chat_format:
            payload = {
                "model": self.model,
                "messages": [
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text},
                ],
                "max_tokens": max_tokens,
                "temperature": 0.45,
                "top_p": 0.6,
                "repetition_penalty": 1.05,
                "stop": ["<|eos|>", "<|endoftext|>"],
            }
            result = self._post("/v1/chat/completions", payload, timeout=1800)
            return clean_translation(result["choices"][0]["message"]["content"])
        prefix = _revise_prompt(instruction) if instruction else _current_prompt()
        payload = {
            "model": self.model,
            "prompt": "<|startoftext|>" + prefix + text + "<|extra_0|>",
            "max_tokens": max_tokens,
            "temperature": 0.45,
            "top_p": 0.6,
            "top_k": 20,
            "repetition_penalty": 1.05,
            "stop": ["<|eos|>", "<|endoftext|>"],
        }
        result = self._post("/v1/completions", payload, timeout=1800)
        return clean_translation(result["choices"][0]["text"])

    def _ping(self) -> bool:
        try:
            req = urllib.request.Request(self.base + "/v1/models", method="GET")
            with urllib.request.urlopen(req, timeout=8) as resp:
                return resp.status == 200
        except Exception:
            return False


class DeepSeekProvider:
    """DeepSeek API (chat completions). Ключ — из секрет-файла, наружу не отдаётся."""

    name = "deepseek"

    def __init__(self) -> None:
        self.base = repo.get_setting("deepseek_base_url", "https://api.deepseek.com")
        self.model = repo.get_setting("deepseek_model", "deepseek-chat")

    @staticmethod
    def _key() -> str:
        key = config.deepseek_key()
        if not key:
            raise FatalTranslatorError("DeepSeek API-ключ не задан — пропишите в админке (Настройки)")
        return key

    def _post(self, payload: dict, timeout: int = 1200) -> dict:
        import http.client

        key = self._key()
        host = self.base.replace("https://", "").replace("http://", "").rstrip("/")
        conn = http.client.HTTPSConnection(host, timeout=timeout)
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {key}",
        }
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        try:
            conn.request("POST", "/chat/completions", body=body, headers=headers)
            resp = conn.getresponse()
            raw = resp.read().decode("utf-8", errors="replace")
        except Exception as exc:  # network/timeout
            raise TranslatorError(f"DeepSeek network error: {exc}") from exc
        finally:
            conn.close()
        if resp.status in (401, 402):
            reason = "неверный ключ" if resp.status == 401 else "недостаточно средств на балансе"
            raise FatalTranslatorError(f"DeepSeek {resp.status}: {reason}")
        if resp.status in (429, 500, 502, 503, 504):
            raise TranslatorError(f"DeepSeek {resp.status}: {raw[:200]}")
        if resp.status != 200:
            raise FatalTranslatorError(f"DeepSeek {resp.status}: {raw[:300]}")
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            raise FatalTranslatorError(f"DeepSeek: не-JSON ответ: {raw[:200]}") from exc

    def token_count(self, text: str) -> int:
        return int(len(text) * 0.6)

    def translate(self, text: str, source_tokens: int | None = None, instruction: str | None = None) -> str:
        source_tokens = source_tokens or self.token_count(text)
        max_tokens = max(1024, min(7800, int(source_tokens * 2.4) + 600))
        system_prompt = _revise_prompt(instruction) if instruction else _current_prompt().strip()
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": text},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.45,
            "top_p": 0.6,
        }
        result = self._post(payload)
        return clean_translation(result["choices"][0]["message"]["content"])


def get_provider(translator: str):
    if translator == "deepseek":
        return DeepSeekProvider()
    return LocalProvider()
