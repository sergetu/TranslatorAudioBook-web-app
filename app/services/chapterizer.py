"""Разбивка исходника на главы (第N章/节/回) и нарезка главы на LLM-чанки."""
from __future__ import annotations

import re

CHAPTER_RE = re.compile(r"^\s*第\s*(\d+)\s*([章回节卷])")
# Строка-заголовок главы: "第12章 标题..." (может идти без пробела)
HEADING_RE = re.compile(r"^\s*第\s*\d+\s*[章回节卷]\s*\S.*$|^\s*第\s*\d+\s*[章回节卷]\s*$")

# Управляющие символы-мусор из исходников (разделители между главами: \x06, \x0e, \x10 и т.п.)
CTRL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


def clean_source_text(text: str) -> str:
    """Убирает управляющие символы и мусорные строки-разделители (\u3000-отступы).

    Сохраняет \n, \t, идеографический пробел \u3000 в содержательных строках.
    """
    text = CTRL_RE.sub("", text)
    lines = text.splitlines(keepends=True)
    out = []
    for line in lines:
        if line.strip(" \u3000\t\r\n"):
            out.append(line)          # содержательная строка (отступ \u3000 сохраняется)
        # иначе: строка-разделитель из пробелов/отступов — выбросить
    cleaned = "".join(out)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip() + "\n" if cleaned.strip() else ""


# ~1700-2400 токенов на чанк при zh≈0.65 ток/символ => до ~3300 символов,
# берём консервативно, чтобы влезать в 8k-контекст вместе с промптом.
MAX_CHARS_PER_CHUNK = 3000


def detect_chapter_boundaries(text: str) -> list[int]:
    """Индексы строк, с которых начинаются главы (заголовки)."""
    lines = text.splitlines(keepends=True)
    starts: list[int] = []
    for i, line in enumerate(lines):
        if CHAPTER_RE.match(line):
            starts.append(i)
    return starts


def split_chapters(text: str) -> list[dict]:
    """Возвращает [{num, title, text}] или [] если глав не найдено.

    Текст до первой главы (пролог/шапка сайта) выбрасывается.
    Adjacent-дубли заголовков («第94章…» + следующая строка «　第94章»-разделитель)
    схлопываются: повторный заголовок с тем же номером без контента между ними
    игнорируется. Номера нормализуются (дубликаты-не-разделители сдвигаются),
    так что num всегда уникален и соответствует порядку файлов.
    """
    lines = text.splitlines(keepends=True)
    starts = detect_chapter_boundaries(text)
    if not starts:
        return []

    # 1) схлопываем adjacent-дубли: тот же num и между стартами нет контента
    cleaned: list[int] = []
    for idx, start in enumerate(starts):
        if cleaned:
            prev = cleaned[-1]
            m_prev = CHAPTER_RE.match(lines[prev].strip())
            m_cur = CHAPTER_RE.match(lines[start].strip())
            gap = "".join(lines[prev + 1:start])
            if (m_prev and m_cur and int(m_prev.group(1)) == int(m_cur.group(1))
                    and not gap.strip()):
                continue  # разделитель-дубль
        cleaned.append(start)

    # 2) собираем главы и нормализуем номера
    chapters: list[dict] = []
    seen: set[int] = set()
    next_free = 1
    for idx, start in enumerate(cleaned):
        end = cleaned[idx + 1] if idx + 1 < len(cleaned) else len(lines)
        heading = lines[start].strip()
        m = CHAPTER_RE.match(heading)
        raw_num = int(m.group(1))
        num = raw_num
        while num in seen:          # не-разделительный дубль: сдвигаем номер
            num = next_free
            next_free += 1
        seen.add(num)
        if num >= next_free:
            next_free = num + 1
        title = re.sub(r"\s+", " ", heading).strip()
        body = clean_source_text("".join(lines[start:end]))
        # заголовок из тела не дублируем (он уже в title), но абзацы сохраняем
        chapters.append({"num": num, "title": title, "text": body})
    return chapters


def chunks_of_chapter(text: str, max_chars: int = MAX_CHARS_PER_CHUNK) -> list[str]:
    """Нарезка текста главы на фрагменты <= max_chars по границам абзацев/предложений."""
    if len(text) <= max_chars:
        return [text]
    paragraphs = re.split(r"(\n+)", text)
    pieces: list[str] = []
    buf = ""
    for para in paragraphs:
        if len(para) > max_chars:  # абзац-монстр: режем по предложениям
            if buf:
                pieces.append(buf)
                buf = ""
            for sentence in re.findall(r".*?(?:[。！？!?；;\n]|\Z)", para, flags=re.S):
                if len(sentence) > max_chars:
                    for i in range(0, len(sentence), max_chars):
                        pieces.append(sentence[i : i + max_chars])
                elif len(buf) + len(sentence) > max_chars:
                    if buf:
                        pieces.append(buf)
                    buf = sentence
                else:
                    buf += sentence
            if buf:
                pieces.append(buf)
                buf = ""
        elif len(buf) + len(para) > max_chars:
            if buf:
                pieces.append(buf)
            buf = para
        else:
            buf += para
    if buf and buf.strip():
        pieces.append(buf)
    return [p for p in pieces if p.strip()]
