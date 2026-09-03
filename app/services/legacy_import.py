"""Импорт существующих артефактов как stream-книги (без копирования 508МБ аудио).

«开局三个老婆，先当土皇，再当君» — пример: source.txt, готовый перевод
translated.txt, аудио-каталог остаётся на месте (legacy_audio_dir).
"""
from __future__ import annotations

import shutil
from pathlib import Path

from .. import config
from ..repositories.sqlite_repo import repo
from . import books as books_svc


def import_stream_book(
    owner_id: int,
    title: str,
    source_file: str | Path | None,
    translated_file: str | Path | None,
    audio_dir: str | Path | None,
    author: str = "",
) -> dict:
    book_id = repo.create_book(
        owner_id=owner_id,
        title=title,
        author=author,
        mode="stream",
        status="tts_done" if audio_dir else "translated",
        source_filename=str(source_file or ""),
        source_encoding="",
        legacy_audio_dir=str(audio_dir or ""),
    )
    bdir = books_svc.book_dir(book_id)
    if source_file and Path(source_file).exists():
        shutil.copy2(Path(source_file), bdir / "source.txt")
        repo.update_book(book_id, source_chars=len((bdir / "source.txt").read_text(encoding="utf-8", errors="replace")))
    if translated_file and Path(translated_file).exists():
        shutil.copy2(Path(translated_file), bdir / "translated.txt")
    if audio_dir:
        ad = Path(audio_dir)
        if ad.exists():
            mp3 = list(ad.glob("*_ru_part*.mp3"))
            if mp3:
                chapters_total = len({f.name.split("_")[0] for f in mp3})
                repo.update_book(book_id, chapters_total=chapters_total)
    book = repo.get_book(book_id)
    return {"book_id": book_id, "title": book["title"], "mode": book["mode"]}


def stream_audio_dir(book: dict) -> Path | None:
    """Каталог mp3: legacy_audio_dir (если задан) либо штатный data/library/{id}/audio."""
    if book.get("legacy_audio_dir"):
        p = Path(book["legacy_audio_dir"])
        return p if p.exists() else None
    if book.get("mode") == "stream":
        return None
    ad = books_svc.audio_dir(book["id"])
    return ad if ad.exists() else None


def stream_tracks(book: dict) -> list[dict]:
    """Список mp3 (имя, url, размер, mtime) для stream-книги."""
    ad = stream_audio_dir(book)
    if ad is None:
        return []
    files = sorted(ad.glob("*_ru_part*.mp3"), key=lambda f: books_svc.natural_key(f.name))
    return [
        {"name": f.name, "url": f"/api/books/{book['id']}/stream/{f.name}",
         "size": f.stat().st_size, "mtime": f.stat().st_mtime}
        for f in files if f.stat().st_size > 0
    ]
