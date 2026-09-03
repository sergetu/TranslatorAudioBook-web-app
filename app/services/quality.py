"""Детект «подозрительных» глав: обрыв, слишком короткий/длинный перевод, мусор."""
from __future__ import annotations

import re


def suspicious_issues(source_text: str, ru_text: str) -> list[str]:
    """Возвращает список проблем перевода главы (пусто = выглядит нормально)."""
    issues: list[str] = []
    src_chars = len(source_text.strip())
    ru_chars = len(ru_text.strip())
    if not ru_text.strip():
        issues.append("перевод пуст")
        return issues

    if src_chars > 0 and ru_chars < src_chars * 0.25:
        issues.append(f"подозрительно короткий перевод ({ru_chars} симв. против {src_chars} в исходнике)")
    if ru_chars < 200 and src_chars > 800:
        issues.append("очень короткий перевод при длинной главе")

    tail = ru_text.strip()[-60:]
    if not re.search(r"[.!?…»\"”]$", tail) and len(tail) > 0 and not tail.rstrip().endswith(("...", "…")):
        # обрыв на середине предложения — частая болезнь HY-MT
        if not re.search(r"[。！？.!?…]$", ru_text.strip()[-3:]):
            issues.append("перевод, похоже, оборван на полуслове (нет знака конца в хвосте)")

    if re.search(r"(?:\uFFFD){3,}", ru_text):
        issues.append("найдены битые символы замены (�)")
    if "Russian translation:" in ru_text or "Перевод:" in ru_text:
        issues.append("в текст попал служебный префикс")
    return issues


def summarize_issues(chapter_issues: dict[int, list[str]], max_items: int = 20) -> list[dict]:
    """{num, issues[]} отсортированные; только главы с проблемами."""
    out = [
        {"num": num, "issues": issues}
        for num, issues in chapter_issues.items()
        if issues
    ]
    out.sort(key=lambda x: x["num"])
    return out[:max_items]
