"""Постановка задач в очередь (pipeline)."""
from __future__ import annotations

from ..repositories.sqlite_repo import repo


def enqueue_translate(book_id: int, owner_id: int, nums: list[int] | None = None,
                      job_type: str = "translate") -> int:
    """Ставит перевод глав. nums=None -> все непереведённые. Возвращает число задач."""
    chapters = repo.list_chapters(book_id)
    wanted = set(nums) if nums else None
    created = 0
    for ch in chapters:
        if wanted is not None and ch["num"] not in wanted:
            continue
        if ch["status"] == "queued":
            continue  # перевод уже в очереди — не плодим дубли
        if job_type == "repair":
            pass  # repair переводит даже готовые
        elif ch["status"] == "translated":
            continue
        repo.create_job(book_id=book_id, chapter_id=ch["id"], owner_id=owner_id,
                        type=job_type, priority=10 if job_type == "repair" else 5)
        repo.update_chapter(ch["id"], status="queued", error="")
        created += 1
    return created


def enqueue_tts(book_id: int, owner_id: int, nums: list[int] | None = None) -> int:
    """Ставит озвучку переведённых глав. nums=None -> все translated без done-озвучки."""
    chapters = repo.list_chapters(book_id)
    wanted = set(nums) if nums else None
    created = 0
    for ch in chapters:
        if wanted is not None and ch["num"] not in wanted:
            continue
        if ch["status"] != "translated":
            continue
        if ch["tts_status"] in ("done", "queued"):
            continue  # уже озвучена или в очереди — не плодим дубли
        repo.create_job(book_id=book_id, chapter_id=ch["id"], owner_id=owner_id, type="tts")
        repo.update_chapter(ch["id"], tts_status="queued", tts_error="")
        created += 1
    return created


def enqueue_revise(book_id: int, owner_id: int, num: int, feedback: str) -> int:
    """Пере-перевод главы с учётом замечания пользователя (тип revise)."""
    ch = repo.get_chapter_by_num(book_id, num)
    if not ch:
        return 0
    if ch["status"] != "translated":
        raise ValueError("глава ещё не переведена — пере-перевод возможен после первого перевода")
    repo.create_job(book_id=book_id, chapter_id=ch["id"], owner_id=owner_id,
                    type="revise", priority=10, note=(feedback or "").strip())
    repo.update_chapter(ch["id"], status="queued", error="")
    return 1


def cancel_book_jobs(book_id: int) -> None:
    for job in repo.list_jobs(book_id=book_id):
        if job["status"] in ("queued", "running"):
            repo.update_job(job["id"], status="canceled", finished_at=None)
