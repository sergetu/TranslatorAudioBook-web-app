"""Скачивание: txt (глава/книга), mp3-глава (ffmpeg concat), zip всех аудио."""
from __future__ import annotations

import subprocess
import tempfile
import zipfile
from pathlib import Path

from ..repositories.sqlite_repo import repo
from . import books as books_svc

FFMPEG = "ffmpeg"


def chapter_txt_path(book_id: int, num: int, lang: str) -> Path:
    if lang == "zh":
        return books_svc.zh_path(book_id, num)
    return books_svc.ru_path(book_id, num)


def chapter_audio_parts(book_id: int, num: int) -> list[Path]:
    ad = books_svc.audio_dir(book_id)
    files = sorted(ad.glob(f"{num:03d}_ru_part*.mp3"))
    return [f for f in files if f.stat().st_size > 0]


def concat_mp3(parts: list[Path], out_path: Path) -> Path:
    """Склейка частей главы в один mp3 через ffmpeg concat demuxer."""
    with tempfile.TemporaryDirectory() as tmp:
        lst = Path(tmp) / "list.txt"
        lst.write_text("".join(f"file '{p.as_posix()}'\n" for p in parts), encoding="utf-8")
        subprocess.run(
            [FFMPEG, "-y", "-f", "concat", "-safe", "0", "-i", str(lst),
             "-c", "copy", str(out_path)],
            check=True, capture_output=True,
        )
    return out_path


def build_audio_zip(book_id: int, dest: Path) -> Path:
    """zip всех mp3 книги (глава -> папка вида 001/)."""
    ad = books_svc.audio_dir(book_id)
    with zipfile.ZipFile(dest, "w", zipfile.ZIP_DEFLATED) as zf:
        for num_dir in sorted({f.name.split("_")[0] for f in ad.glob("*_ru_part*.mp3")},
                              key=lambda x: int(x)):
            for f in sorted(ad.glob(f"{num_dir}_ru_part*.mp3")):
                zf.write(f, arcname=f"{num_dir}/{f.name}")
    return dest
