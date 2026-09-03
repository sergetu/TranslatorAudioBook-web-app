"""Детект кодировки исходников: utf-8 строго, иначе gb18030."""
from __future__ import annotations


class EncodingError(ValueError):
    pass


def decode_bytes(data: bytes) -> tuple[str, str]:
    for enc in ("utf-8", "gb18030"):
        try:
            return data.decode(enc), enc
        except UnicodeDecodeError:
            continue
    raise EncodingError("Не удалось определить кодировку (ожидались utf-8 или gb18030)")


def read_text_detect(path) -> tuple[str, str]:
    data = path.read_bytes()
    return decode_bytes(data)
