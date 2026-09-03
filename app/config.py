"""Config: paths, env overrides, secret-file locations."""
from __future__ import annotations

import os
import secrets as _secrets
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]

DATA_DIR = PROJECT_ROOT / "data"
LIBRARY_DIR = DATA_DIR / "library"
UPLOAD_DIR = DATA_DIR / "uploads"
SECRETS_DIR = DATA_DIR / "secrets"
DB_PATH = Path(os.environ.get("TAB_DB_PATH", str(DATA_DIR / "app.db")))
DIST_DIR = PROJECT_ROOT / "web" / "dist"          # built frontend, optional

JWT_ALG = "HS256"
JWT_TTL_HOURS = int(os.environ.get("TAB_JWT_TTL_HOURS", "720"))

DEFAULT_SETTINGS = {
    "allow_registration": "true",      # первый пользователь всегда admin
    "tts_voice": "ru-RU-SvetlanaNeural",
    "tts_rate": "-4%",
    "tts_volume": "+0%",
    "kobold_base_url": "http://127.0.0.1:5003",
    "kobold_model": "HY-MT1.5-7B-Q4_K_M",
    "kobold_context_tokens": "8192",
    "kobold_chat_format": "raw",        # raw = /v1/completions (как HY-MT), chat = /v1/chat/completions
    "deepseek_base_url": "https://api.deepseek.com",
    "deepseek_model": "deepseek-chat",
    "deepseek_max_concurrency": "3",
    "deepseek_system_prompt": (
        "Translate the following Chinese web novel fragment into natural, fluent Russian. "
        "Preserve all meaning, names, dialogue, paragraph breaks, chapter headings, and tone. "
        "Do not summarize. Do not omit lines. Do not add comments, explanations, Markdown, or notes. "
        "Translate from the first line through the final line of the fragment; do not stop early. "
        "Output only the Russian translation."
    ),
    "llm_max_concurrency": "1",         # локальный koboldcpp — последовательно
    "tts_max_concurrency": "2",
    "max_upload_mb": "50",
    "llm_max_retries": "3",
    "llm_retry_base_seconds": "10",
}

# ---- secret files (никогда не логируются; содержимое deepseek-ключа агент не читает) ----
JWT_SECRET_FILE = SECRETS_DIR / "jwt_secret"
DEEPSEEK_KEY_FILE = SECRETS_DIR / "deepseek_api_key"


def _ensure_dirs() -> None:
    for d in (DATA_DIR, LIBRARY_DIR, UPLOAD_DIR, SECRETS_DIR):
        d.mkdir(parents=True, exist_ok=True)
    if not DB_PATH.parent.exists():
        DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def jwt_secret() -> str:
    if JWT_SECRET_FILE.exists():
        return JWT_SECRET_FILE.read_text(encoding="utf-8").strip()
    _ensure_dirs()
    value = _secrets.token_hex(32)
    JWT_SECRET_FILE.write_text(value, encoding="utf-8")
    return value


def deepseek_key_set() -> bool:
    return DEEPSEEK_KEY_FILE.exists() and DEEPSEEK_KEY_FILE.stat().st_size > 0


def deepseek_key() -> str:
    """Читается ТОЛЬКО в момент реального API-вызова провайдером."""
    try:
        return DEEPSEEK_KEY_FILE.read_text(encoding="utf-8").strip()
    except FileNotFoundError:
        return ""


def save_deepseek_key(key: str) -> None:
    _ensure_dirs()
    DEEPSEEK_KEY_FILE.write_text(key.strip(), encoding="utf-8")


def delete_deepseek_key() -> None:
    if DEEPSEEK_KEY_FILE.exists():
        DEEPSEEK_KEY_FILE.unlink()


_ensure_dirs()
