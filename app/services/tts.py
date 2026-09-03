"""Озвучка глав через edge-tts (голос ru-RU-SvetlanaNeural).

Логика очистки/нарезки — из проверенного make_tts_edge_reader.py.
"""
from __future__ import annotations

import asyncio
import json
import re

MAX_TTS_CHARS = 1800


def clean_for_tts(text: str) -> str:
    replacements = {
        "『": "", "』": "", "【": "", "】": "",
        "……": "...", "…………": "...",
    }
    cleaned = text
    for old, new in replacements.items():
        cleaned = cleaned.replace(old, new)
    cleaned = re.sub(r"https?://\S+", " ссылка ", cleaned)
    cleaned = re.sub(r"[*_`#>]+", "", cleaned)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned)
    return cleaned.strip()


def split_for_tts(text: str, max_chars: int = MAX_TTS_CHARS) -> list[str]:
    paragraphs = [part.strip() for part in re.split(r"\n+", text) if part.strip()]
    parts: list[str] = []
    current = ""

    def push_current():
        nonlocal current
        if current.strip():
            parts.append(current.strip())
            current = ""

    for paragraph in paragraphs:
        if len(paragraph) > max_chars:
            push_current()
            sentences = re.findall(r".*?(?:[.!?…]+[»”\" ]?\s+|$)", paragraph, flags=re.S)
            buf = ""
            for sentence in [s.strip() for s in sentences if s.strip()]:
                if len(sentence) > max_chars:
                    if buf:
                        parts.append(buf.strip())
                        buf = ""
                    for i in range(0, len(sentence), max_chars):
                        parts.append(sentence[i : i + max_chars].strip())
                elif len(buf) + len(sentence) + 1 > max_chars:
                    parts.append(buf.strip())
                    buf = sentence
                else:
                    buf = (buf + " " + sentence).strip()
            if buf:
                parts.append(buf.strip())
            continue
        candidate = (current + "\n" + paragraph).strip() if current else paragraph
        if len(candidate) > max_chars:
            push_current()
            current = paragraph
        else:
            current = candidate
    push_current()
    return parts


async def _synthesize(text: str, output, voice: str, rate: str, volume: str, timeout_s: int) -> None:
    import edge_tts

    communicate = edge_tts.Communicate(text, voice=voice, rate=rate, volume=volume)
    await asyncio.wait_for(communicate.save(str(output)), timeout=timeout_s)


def synthesize_text_to_parts(
    text: str,
    output_dir,
    prefix: str,          # напр. "001_ru"
    voice: str,
    rate: str = "-4%",
    volume: str = "+0%",
    timeout_s: int = 90,
) -> int:
    """Синтез текста в {prefix}_partNNN.mp3 внутри output_dir. Возвращает число частей."""
    parts = split_for_tts(clean_for_tts(text))
    count = 0
    for idx, part in enumerate(parts, start=1):
        target = output_dir / f"{prefix}_part{idx:03d}.mp3"
        if target.exists() and target.stat().st_size > 0:
            count += 1
            continue
        tmp = output_dir / f"{prefix}_part{idx:03d}.mp3.tmp"
        try:
            asyncio.run(_synthesize(part, tmp, voice, rate, volume, timeout_s))
            tmp.replace(target)  # атомарно: оборванный синтез не оставит «битую» часть
        except asyncio.TimeoutError:
            tmp.unlink(missing_ok=True)
            raise
        count += 1
    done = output_dir / f"{prefix}.done"
    done.write_text(
        json.dumps({"parts": len(parts), "chars": len(text)}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return len(parts)
