"""Книги: файловая раскладка, загрузка исходника, скан глав."""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from ..repositories.sqlite_repo import repo
from . import chapterizer, encoding


def book_dir(book_id: int) -> Path:
    d = config.LIBRARY_DIR / str(book_id)
    d.mkdir(parents=True, exist_ok=True)
    return d


def chapters_dir(book_id: int) -> Path:
    d = book_dir(book_id) / "chapters"
    d.mkdir(parents=True, exist_ok=True)
    return d


def audio_dir(book_id: int) -> Path:
    d = book_dir(book_id) / "audio"
    d.mkdir(parents=True, exist_ok=True)
    return d


def zh_path(book_id: int, num: int) -> Path:
    return chapters_dir(book_id) / f"{num:03d}_zh.txt"


def ru_path(book_id: int, num: int) -> Path:
    return chapters_dir(book_id) / f"{num:03d}_ru.txt"


def natural_key(name: str):
    import re

    return [int(p) if p.isdigit() else p for p in re.split(r"(\d+)", name)]


def save_source(book_id: int, filename: str, data: bytes) -> dict:
    """Сохраняет исходник, определяет кодировку. Возвращает {encoding, chars, text}."""
    text, enc = encoding.decode_bytes(data)
    d = book_dir(book_id)
    src = d / "source.txt"
    src.write_bytes(data)
    repo.update_book(book_id, source_filename=filename, source_encoding=enc,
                     source_chars=len(text), status="empty", chapters_total=0)
    return {"encoding": enc, "chars": len(text)}


def scan_chapters(book_id: int) -> dict:
    """Детект глав, запись chapter-строк + файлов глав. mode -> chaptered (если главы есть).

    Если маркеры глав не найдены — режим/статус не меняем, возвращаем found=False
    (фронт предложит импорт как stream или другой формат).
    """
    book = repo.get_book(book_id)
    if not book:
        raise KeyError("book not found")
    src = book_dir(book_id) / "source.txt"
    if not src.exists():
        raise FileNotFoundError("source.txt не загружен")
    text, _ = encoding.read_text_detect(src)
    chapters = chapterizer.split_chapters(text)
    if not chapters:
        return {"mode": book.get("mode", "chaptered"), "chapters": 0, "found": False}

    ch_dir = chapters_dir(book_id)
    # чистим старые файлы глав
    for old in ch_dir.glob("*_zh.txt"):
        old.unlink()
    rows = []
    for c in chapters:
        body = c["text"]
        path = zh_path(book_id, c["num"])
        path.write_text(body, encoding="utf-8", newline="")
        rows.append({
            "num": c["num"], "title": c["title"], "source_chars": len(body),
            "zh_path": str(path), "status": "none", "tts_status": "none",
        })
    repo.replace_chapters(book_id, rows)
    repo.update_book(book_id, mode="chaptered", status="chunked",
                     chapters_total=len(chapters), source_chars=len(text))
    return {"mode": "chaptered", "chapters": len(chapters), "found": True}


def convert_stream_to_chaptered(book_id: int) -> dict:
    """Конвертация stream-книги в chaptered: нарезает исходник на главы.

    Полный перевод (translated.txt) при наличии переименовывается в
    translated_legacy.txt и сохраняется в books.legacy_ru_file — он продолжает
    отдаваться отдельным эндпоинтом, пока главы не переведены заново.
    legacy_audio_dir не трогаем (старое аудио остаётся доступным как «легаси»).
    """
    book = repo.get_book(book_id)
    if not book:
        raise KeyError("book not found")
    if book["mode"] != "stream":
        raise ValueError("книга уже в режиме по главам")

    bdir = book_dir(book_id)
    legacy_txt = bdir / "translated.txt"
    if legacy_txt.exists():
        legacy_target = bdir / "translated_legacy.txt"
        if not legacy_target.exists():
            shutil.move(str(legacy_txt), str(legacy_target))
        repo.update_book(book_id, legacy_ru_file=str(legacy_target))

    return scan_chapters(book_id)


def rebuild_translated_txt(book_id: int) -> int:
    """Склейка всех переведённых глав в translated.txt. Возвращает число включённых глав."""
    book = repo.get_book(book_id)
    if not book:
        return 0
    chapters = repo.list_chapters(book_id)
    out = book_dir(book_id) / "translated.txt"
    included = 0
    with out.open("w", encoding="utf-8", newline="") as fh:
        for ch in chapters:
            rp = Path(ch["ru_path"]) if ch["ru_path"] else ru_path(book_id, ch["num"])
            if rp.exists():
                text = rp.read_text(encoding="utf-8").strip()
                if text:
                    fh.write(text + "\n\n")
                    included += 1
    if included:
        repo.update_book(book_id, status="translated")
    return included


def delete_book_files(book_id: int) -> None:
    d = config.LIBRARY_DIR / str(book_id)
    if d.exists():
        shutil.rmtree(d, ignore_errors=True)
