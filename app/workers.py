"""Worker dispatcher: вытягивает задачи из БД-очереди и исполняет.

Лимиты: LLM (translate/repair) — 1 параллельно; TTS — tts_max_concurrency (по умолчанию 2).
Запускается как фоновый поток внутри uvicorn (lifespan) либо отдельным процессом
`python -m app.worker`.
"""
from __future__ import annotations

import threading
import time
from pathlib import Path

from . import config, db
from .repositories.sqlite_repo import repo
from .services import books as books_svc
from .services import tts as tts_svc
from .services.chapterizer import chunks_of_chapter
from .services.translator import (
    FatalTranslatorError,
    TranslatorError,
    get_provider,
)


def _settings() -> dict:
    return repo.all_settings()


class Dispatcher:
    def __init__(self, poll_seconds: float = 2.0) -> None:
        self.poll = poll_seconds
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._running_llm: set[int] = set()
        self._running_tts: set[int] = set()

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        # восстановление после падения: running -> queued
        with db.conn_ctx() as conn:
            conn.execute("UPDATE jobs SET status='queued', started_at=NULL WHERE status='running'")
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, daemon=True, name="tab-dispatcher")
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=5)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception:
                pass  # пусть цикл живёт; ошибки логируются в задаче
            self._stop.wait(self.poll)

    def _tick(self) -> None:
        # добиваем «повисшие» running (воркер упал)
        for jid in list(self._running_llm):
            job = repo.get_job(jid)
            if job is None or job["status"] != "running":
                self._running_llm.discard(jid)
        for jid in list(self._running_tts):
            job = repo.get_job(jid)
            if job is None or job["status"] != "running":
                self._running_tts.discard(jid)

        s = _settings()
        llm_limit = int(s.get("llm_max_concurrency", "1"))
        tts_limit = int(s.get("tts_max_concurrency", "2"))

        if len(self._running_llm) < llm_limit:
            exclude = tuple(self._running_llm)
            for job in repo.next_llm_jobs(llm_limit - len(self._running_llm), exclude):
                self._launch(job, "llm")

        if len(self._running_tts) < tts_limit:
            exclude = tuple(self._running_tts)
            for job in repo.next_queued_by_type("tts", tts_limit - len(self._running_tts), exclude):
                self._launch(job, "tts")

    def _launch(self, job: dict, kind: str) -> None:
        if not repo.claim_job(job["id"]):
            return
        if kind == "llm":
            self._running_llm.add(job["id"])
            target = self._run_llm_job
        else:
            self._running_tts.add(job["id"])
            target = self._run_tts_job
        threading.Thread(target=self._guard, args=(job["id"], kind, target), daemon=True).start()

    def _guard(self, job_id: int, kind: str, target) -> None:
        try:
            target(job_id)
        except Exception:
            # финальная страховка
            try:
                repo.update_job(job_id, status="error", error="internal worker failure",
                                finished_at=db.utcnow())
            except Exception:
                pass
        finally:
            if kind == "llm":
                self._running_llm.discard(job_id)
            else:
                self._running_tts.discard(job_id)

    # ---------- LLM: translate / repair ----------
    def _run_llm_job(self, job_id: int) -> None:
        job = repo.get_job(job_id)
        if not job:
            return
        book = repo.get_book(job["book_id"])
        chapter = repo.get_chapter(job["chapter_id"]) if job["chapter_id"] else None
        if not book or not chapter:
            repo.update_job(job_id, status="error", error="book/chapter missing", finished_at=db.utcnow())
            return
        provider = get_provider(book.get("translator") or "local")
        zh = Path(chapter["zh_path"])
        if not zh.exists():
            repo.update_job(job_id, status="error", error=f"нет исходника {zh}", finished_at=db.utcnow())
            return
        text = zh.read_text(encoding="utf-8")
        chunks = chunks_of_chapter(text)
        translated_parts: list[str] = []
        attempts = job["attempts"]
        instruction = job.get("note") or None   # замечание пользователя (revise)
        if instruction:
            repo.update_job(job_id, error="", progress=1)
        try:
            for i, chunk in enumerate(chunks):
                repo.update_job(job_id, progress=round(i / max(1, len(chunks)) * 100, 1))
                try:
                    out = provider.translate(chunk, instruction=instruction)
                except FatalTranslatorError as exc:
                    repo.update_job(job_id, status="error", error=str(exc), finished_at=db.utcnow())
                    repo.update_chapter(chapter["id"], status="error", error=str(exc))
                    return
                except TranslatorError:
                    attempts += 1
                    if attempts >= 3:
                        raise
                    time.sleep(5 * attempts)
                    repo.update_job(job_id, attempts=attempts)
                    out = provider.translate(chunk, instruction=instruction)
                translated_parts.append(out)
        except (TranslatorError, TimeoutError) as exc:
            repo.update_chapter(chapter["id"], status="error", error=str(exc)[:500])
            repo.update_job(job_id, status="error", error=str(exc)[:500], finished_at=db.utcnow())
            return

        ru = books_svc.ru_path(book["id"], chapter["num"])
        full = "\n\n".join(p.strip() for p in translated_parts if p.strip()).strip() + "\n"
        ru.write_text(full, encoding="utf-8", newline="")
        if job["type"] == "revise":
            # перевод изменился: аудио старой версии больше не соответствует
            ad = books_svc.audio_dir(book["id"])
            for f in ad.glob(f"{chapter['num']:03d}_ru_part*.mp3"):
                f.unlink(missing_ok=True)
            done = ad / f"{chapter['num']:03d}_ru.done"
            done.unlink(missing_ok=True)
            repo.update_chapter(chapter["id"], status="translated", error="", ru_path=str(ru),
                                tts_status="none", tts_error="", audio_parts=0)
        else:
            repo.update_chapter(chapter["id"], status="translated", error="", ru_path=str(ru))
        repo.update_job(job_id, status="done", progress=100, finished_at=db.utcnow())
        books_svc.rebuild_translated_txt(book["id"])

    # ---------- TTS ----------
    def _run_tts_job(self, job_id: int) -> None:
        job = repo.get_job(job_id)
        if not job:
            return
        book = repo.get_book(job["book_id"])
        chapter = repo.get_chapter(job["chapter_id"]) if job["chapter_id"] else None
        if not book or not chapter:
            repo.update_job(job_id, status="error", error="book/chapter missing", finished_at=db.utcnow())
            return
        ru = Path(chapter["ru_path"]) if chapter["ru_path"] else books_svc.ru_path(book["id"], chapter["num"])
        if not ru.exists():
            repo.update_job(job_id, status="error", error="глава не переведена", finished_at=db.utcnow())
            return
        s = _settings()
        voice = s.get("tts_voice", "ru-RU-SvetlanaNeural")
        rate = s.get("tts_rate", "-4%")
        volume = s.get("tts_volume", "+0%")
        out_dir = books_svc.audio_dir(book["id"])
        text = ru.read_text(encoding="utf-8")
        prefix = f"{chapter['num']:03d}_ru"
        try:
            parts = tts_svc.synthesize_text_to_parts(text, out_dir, prefix, voice, rate, volume)
        except Exception as exc:
            repo.update_chapter(chapter["id"], tts_status="error", tts_error=str(exc)[:300])
            repo.update_job(job_id, status="error", error=str(exc)[:500], finished_at=db.utcnow())
            return
        repo.update_chapter(chapter["id"], tts_status="done", tts_error="", audio_parts=parts)
        repo.update_job(job_id, status="done", progress=100, finished_at=db.utcnow())
        # агрегат книги: все ПЕРЕВЕДЁННЫЕ главы озвучены?
        chapters = repo.list_chapters(book["id"])
        translated = [c for c in chapters if c["status"] == "translated"]
        if translated and all(c["tts_status"] == "done" for c in translated):
            repo.update_book(book["id"], status="tts_done")


_dispatcher = Dispatcher()


def start_dispatcher() -> None:
    _dispatcher.start()


def stop_dispatcher() -> None:
    _dispatcher.stop()
