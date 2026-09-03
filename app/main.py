"""FastAPI app: роуты, lifespan (диспетчер), статика фронта."""
from __future__ import annotations

import mimetypes
import re
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import (
    Depends, FastAPI, File, Form, HTTPException, Request, UploadFile,
)
from fastapi.responses import FileResponse, JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles

from . import config, db
from .repositories.sqlite_repo import repo
from .services import auth as auth_svc
from .services import books as books_svc
from .services import download as download_svc
from .services import legacy_import as legacy_svc
from .services import pipeline as pipeline_svc
from .services import quality as quality_svc
from .workers import start_dispatcher, stop_dispatcher


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    start_dispatcher()
    yield
    stop_dispatcher()


app = FastAPI(title="TranslatorAudioBook", version="2.0.0", lifespan=lifespan)


def _now() -> str:
    return db.utcnow()


# ---------------------------------------------------------------- auth
@app.post("/api/auth/register")
def register(email: str = Form(...), password: str = Form(...), name: str = Form("")):
    email = email.strip().lower()
    if len(password) < 6:
        raise HTTPException(400, "Пароль должен быть не короче 6 символов")
    if repo.get_user_by_email(email):
        raise HTTPException(409, "Пользователь с таким email уже существует")
    if repo.count_users() == 0:
        role = "admin"          # первый пользователь — администратор
    else:
        if repo.get_setting("allow_registration", "true").lower() != "true":
            raise HTTPException(403, "Регистрация закрыта администратором")
        role = "user"
    user = repo.create_user(email, auth_svc.hash_password(password), name.strip(), role)
    return {"token": auth_svc.create_token(user), "user": _user_out(user)}


@app.post("/api/auth/login")
def login(email: str = Form(...), password: str = Form(...)):
    user = repo.get_user_by_email(email.strip().lower())
    if not user or not auth_svc.verify_password(password, user["password_hash"]):
        raise HTTPException(401, "Неверный email или пароль")
    if not user["is_active"]:
        raise HTTPException(403, "Пользователь отключён")
    return {"token": auth_svc.create_token(user), "user": _user_out(user)}


@app.get("/api/auth/me")
def me(user: dict = Depends(auth_svc.current_user)):
    return _user_out(user)


def _user_out(u: dict) -> dict:
    return {"id": u["id"], "email": u["email"], "name": u["name"],
            "role": u["role"], "is_active": u["is_active"],
            "created_at": u["created_at"]}


# ---------------------------------------------------------------- admin: users
@app.get("/api/admin/users")
def admin_users(_: dict = Depends(auth_svc.current_admin)):
    return [_user_out(u) for u in repo.list_users()]


@app.post("/api/admin/users")
def admin_create_user(email: str = Form(...), password: str = Form(...),
                      name: str = Form(""), role: str = Form("user"),
                      _: dict = Depends(auth_svc.current_admin)):
    email = email.strip().lower()
    if repo.get_user_by_email(email):
        raise HTTPException(409, "email уже занят")
    if role not in ("user", "admin"):
        raise HTTPException(400, "role: user|admin")
    user = repo.create_user(email, auth_svc.hash_password(password), name, role)
    return _user_out(user)


@app.post("/api/admin/users/{user_id}/reset-password")
def admin_reset_password(user_id: int, new_password: str = Form(...),
                         _: dict = Depends(auth_svc.current_admin)):
    if len(new_password) < 6:
        raise HTTPException(400, "Пароль должен быть не короче 6 символов")
    target = repo.get_user(user_id)
    if not target:
        raise HTTPException(404, "пользователь не найден")
    repo.update_user(user_id, password_hash=auth_svc.hash_password(new_password))
    return {"ok": True}


@app.post("/api/admin/users/{user_id}/toggle")
def admin_toggle_user(user_id: int, _: dict = Depends(auth_svc.current_admin)):
    target = repo.get_user(user_id)
    if not target:
        raise HTTPException(404, "пользователь не найден")
    repo.update_user(user_id, is_active=0 if target["is_active"] else 1)
    return {"ok": True, "is_active": 0 if target["is_active"] else 1}


# ---------------------------------------------------------------- admin: settings
@app.get("/api/admin/settings")
def admin_get_settings(_: dict = Depends(auth_svc.current_admin)):
    s = repo.all_settings()
    return {"settings": s, "deepseek_key_set": config.deepseek_key_set()}


@app.put("/api/admin/settings")
def admin_put_settings(payload: dict, _: dict = Depends(auth_svc.current_admin)):
    allowed = set(config.DEFAULT_SETTINGS.keys())
    for key, value in payload.items():
        if key not in allowed:
            raise HTTPException(400, f"неизвестный параметр: {key}")
        repo.set_setting(key, str(value))
    return {"ok": True}


@app.api_route("/api/admin/settings/deepseek-key", methods=["PUT", "POST"])
def admin_put_deepseek_key(key: str = Form(...), _: dict = Depends(auth_svc.current_admin)):
    """Ключ пишется в файл-секрет. Наружу не возвращается, не логируется."""
    if not key.strip():
        raise HTTPException(400, "пустой ключ")
    config.save_deepseek_key(key)
    return {"ok": True, "deepseek_key_set": True}


@app.delete("/api/admin/settings/deepseek-key")
def admin_delete_deepseek_key(_: dict = Depends(auth_svc.current_admin)):
    config.delete_deepseek_key()
    return {"ok": True, "deepseek_key_set": False}


# ---------------------------------------------------------------- books
def _book_out(b: dict) -> dict:
    chapters = repo.list_chapters(b["id"]) if b["mode"] == "chaptered" else []
    translated = sum(1 for c in chapters if c["status"] == "translated")
    tts_done = sum(1 for c in chapters if c["tts_status"] == "done")
    out = {**dict(b),
           "has_legacy_ru": bool(b.get("legacy_ru_file")),
           "has_legacy_audio": bool(b.get("legacy_audio_dir")),
           "translated_chapters": None if b["mode"] == "stream" else translated,
           "tts_done_chapters": None if b["mode"] == "stream" else tts_done}
    if b["mode"] == "stream":
        tracks = legacy_svc.stream_tracks(b)
        out["tracks"] = len(tracks)
        out["audio_size"] = sum(t["size"] for t in tracks)
    return out


def _owner_book(book_id: int, user: dict) -> dict:
    b = repo.get_book(book_id)
    if not b:
        raise HTTPException(404, "книга не найдена")
    if b["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403, "нет доступа к книге")
    return b


@app.get("/api/books")
def list_books(user: dict = Depends(auth_svc.current_user)):
    books = repo.list_books(user["id"])
    if user["role"] == "admin":
        # админ видит все книги (задел под публичный сервис)
        books = []
        for u in repo.list_users():
            for b in repo.list_books(u["id"]):
                books.append(b)
    return [_book_out(b) for b in books]


@app.post("/api/books")
def create_book(title: str = Form(...), author: str = Form(""),
                translator: str = Form("local"),
                user: dict = Depends(auth_svc.current_user)):
    title = title.strip()
    if not title:
        raise HTTPException(400, "title обязателен")
    if translator not in ("local", "deepseek"):
        raise HTTPException(400, "translator: local|deepseek")
    book_id = repo.create_book(owner_id=user["id"], title=title, author=author.strip(),
                               translator=translator, status="empty",
                               source_encoding="", created_at=_now())
    return _book_out(repo.get_book(book_id))


@app.get("/api/books/{book_id}")
def get_book(book_id: int, user: dict = Depends(auth_svc.current_user)):
    return _book_out(_owner_book(book_id, user))


@app.patch("/api/books/{book_id}")
def patch_book(book_id: int, payload: dict, user: dict = Depends(auth_svc.current_user)):
    b = _owner_book(book_id, user)
    upd = {}
    if "title" in payload and payload["title"]:
        upd["title"] = payload["title"].strip()
    if "author" in payload:
        upd["author"] = payload["author"].strip()
    if "translator" in payload:
        if payload["translator"] not in ("local", "deepseek"):
            raise HTTPException(400, "translator: local|deepseek")
        upd["translator"] = payload["translator"]
    if upd:
        repo.update_book(book_id, **upd)
    return _book_out(repo.get_book(book_id))


@app.delete("/api/books/{book_id}")
def delete_book(book_id: int, user: dict = Depends(auth_svc.current_user)):
    b = _owner_book(book_id, user)
    pipeline_svc.cancel_book_jobs(book_id)
    books_svc.delete_book_files(book_id)
    repo.delete_book(book_id)
    return {"ok": True}


@app.post("/api/books/{book_id}/source")
async def upload_source(book_id: int, file: UploadFile = File(...),
                        user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    max_mb = int(repo.get_setting("max_upload_mb", "50"))
    data = await file.read()
    if len(data) > max_mb * 1024 * 1024:
        raise HTTPException(413, f"файл больше {max_mb} МБ")
    if not data:
        raise HTTPException(400, "пустой файл")
    try:
        books_svc.save_source(book_id, file.filename or "source.txt", data)
    except Exception as exc:
        raise HTTPException(400, f"не удалось прочитать файл: {exc}")
    return _book_out(repo.get_book(book_id))


@app.post("/api/books/{book_id}/cover")
async def upload_cover(book_id: int, file: UploadFile = File(...),
                       user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    data = await file.read()
    if len(data) > 8 * 1024 * 1024:
        raise HTTPException(413, "обложка больше 8 МБ")
    ext = Path(file.filename or "cover.jpg").suffix.lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        raise HTTPException(400, "обложка: jpg/png/webp")
    cover = books_svc.book_dir(book_id) / f"cover{ext}"
    cover.write_bytes(data)
    repo.update_book(book_id, cover_path=str(cover))
    return {"ok": True, "cover_url": f"/api/books/{book_id}/cover"}


@app.get("/api/books/{book_id}/cover")
def get_cover(book_id: int, request: Request,
              user: dict | None = Depends(auth_svc.current_optional)):
    _audio_auth(request, book_id, user)
    b = repo.get_book(book_id)
    if b["cover_path"] and Path(b["cover_path"]).exists():
        return FileResponse(b["cover_path"])
    raise HTTPException(404)


@app.post("/api/books/{book_id}/scan")
def scan_book(book_id: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    try:
        result = books_svc.scan_chapters(book_id)
    except FileNotFoundError:
        raise HTTPException(400, "сначала загрузите исходник")
    return {"ok": True, **result}


@app.post("/api/books/{book_id}/convert")
def convert_book(book_id: int, user: dict = Depends(auth_svc.current_user)):
    """stream -> chaptered: нарезать исходник на главы (легаси перевод/аудио сохраняются)."""
    _owner_book(book_id, user)
    try:
        result = books_svc.convert_stream_to_chaptered(book_id)
    except FileNotFoundError:
        raise HTTPException(400, "сначала загрузите исходник")
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, **result}


@app.get("/api/books/{book_id}/legacy/tracks")
def legacy_tracks(book_id: int, user: dict = Depends(auth_svc.current_user)):
    b = _owner_book(book_id, user)
    return {"mode": "legacy", "tracks": legacy_svc.stream_tracks(b)}


@app.get("/api/books/{book_id}/legacy/translated.txt")
def legacy_translated_txt(book_id: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    p = Path(books_svc.book_dir(book_id) / "translated_legacy.txt")
    if not p.exists():
        raise HTTPException(404, "полного перевода (легаси) нет")
    return FileResponse(p, media_type="text/plain; charset=utf-8", filename="translated_legacy.txt")


@app.get("/api/books/{book_id}/chapters")
def list_chapters(book_id: int, user: dict = Depends(auth_svc.current_user)):
    b = _owner_book(book_id, user)
    if b["mode"] == "stream":
        return {"mode": "stream", "chapters": legacy_svc.stream_tracks(b)}
    chapters = []
    for c in repo.list_chapters(book_id):
        chapters.append({
            "id": c["id"], "num": c["num"], "title": c["title"],
            "status": c["status"], "tts_status": c["tts_status"],
            "error": c["error"], "tts_error": c["tts_error"],
            "source_chars": c["source_chars"], "audio_parts": c["audio_parts"],
        })
    return {"mode": "chaptered", "chapters": chapters}


@app.get("/api/books/{book_id}/chapters/{num}")
def chapter_content(book_id: int, num: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    c = repo.get_chapter_by_num(book_id, num)
    if not c:
        raise HTTPException(404, "глава не найдена")
    zh = Path(c["zh_path"]) if c["zh_path"] else books_svc.zh_path(book_id, num)
    ru = Path(c["ru_path"]) if c["ru_path"] else books_svc.ru_path(book_id, num)
    audio = download_svc.chapter_audio_parts(book_id, num)
    return {
        "num": num, "title": c["title"], "status": c["status"], "tts_status": c["tts_status"],
        "error": c["error"], "tts_error": c["tts_error"],
        "zh": zh.read_text(encoding="utf-8") if zh.exists() else "",
        "ru": ru.read_text(encoding="utf-8") if ru.exists() else "",
        "audio": [f"/api/books/{book_id}/audio/{f.name}" for f in audio],
    }


@app.get("/api/books/{book_id}/translated.txt")
def translated_txt(book_id: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    p = books_svc.book_dir(book_id) / "translated.txt"
    if not p.exists():
        raise HTTPException(404, "перевода пока нет")
    return FileResponse(p, media_type="text/plain; charset=utf-8", filename="translated.txt")


@app.get("/api/books/{book_id}/source.txt")
def source_txt(book_id: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    p = books_svc.book_dir(book_id) / "source.txt"
    if not p.exists():
        raise HTTPException(404, "исходник не загружен")
    return FileResponse(p, media_type="text/plain; charset=utf-8", filename="source.txt")


# ---------------------------------------------------------------- pipeline
@app.post("/api/books/{book_id}/pipeline")
def run_pipeline(book_id: int, payload: dict, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    stage = payload.get("stage", "all")          # all|translate|tts
    nums = payload.get("chapters")               # список номеров глав или null
    created: dict = {"translate": 0, "tts": 0}
    if stage in ("all", "translate"):
        created["translate"] = pipeline_svc.enqueue_translate(book_id, user["id"], nums)
    if stage in ("all", "tts"):
        created["tts"] = pipeline_svc.enqueue_tts(book_id, user["id"], nums)
    return {"ok": True, "created": created}


@app.get("/api/jobs")
def list_jobs(book_id: int | None = None, user: dict = Depends(auth_svc.current_user)):
    if book_id is not None:
        b = repo.get_book(book_id)
        if not b:
            raise HTTPException(404)
        if b["owner_id"] != user["id"] and user["role"] != "admin":
            raise HTTPException(403)
        return repo.list_jobs(book_id=book_id)
    if user["role"] == "admin":
        return repo.list_jobs()
    return repo.list_jobs(owner_id=user["id"])


@app.post("/api/jobs/{job_id}/cancel")
def cancel_job(job_id: int, user: dict = Depends(auth_svc.current_user)):
    job = repo.get_job(job_id)
    if not job:
        raise HTTPException(404)
    if job["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403)
    if job["status"] in ("queued", "running"):
        repo.update_job(job_id, status="canceled", finished_at=_now())
        _restore_chapter_after_cancel(job)
    return {"ok": True}


def _restore_chapter_after_cancel(job: dict) -> None:
    """Отмена задачи не должна оставлять главу в вечном queued — возвращаем
    статус по факту файлов (задача могла быть отменена в середине работы)."""
    if not job.get("chapter_id") or not job.get("book_id"):
        return
    ch = repo.get_chapter(job["chapter_id"])
    if not ch:
        return
    jt = job["type"]
    if jt in ("translate", "repair", "revise"):
        if ch["status"] != "queued":
            return
        # для revise/repair старый перевод на диске цел (перезаписывается только по завершении)
        ru = books_svc.ru_path(job["book_id"], ch["num"])
        st = "translated" if ru.exists() and ru.stat().st_size > 0 else "none"
        repo.update_chapter(ch["id"], status=st, error="")
    elif jt == "tts":
        if ch["tts_status"] != "queued":
            return
        ad = books_svc.audio_dir(job["book_id"])
        done = ad / f"{ch['num']:03d}_ru.done"
        st = "done" if done.exists() else "none"
        repo.update_chapter(ch["id"], tts_status=st, tts_error="",
                            audio_parts=0 if st != "done" else ch.get("audio_parts") or 0)


@app.post("/api/chapters/{chapter_id}/retry")
def chapter_retry(chapter_id: int, user: dict = Depends(auth_svc.current_user)):
    c = repo.get_chapter(chapter_id)
    if not c:
        raise HTTPException(404)
    b = repo.get_book(c["book_id"])
    if b["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403)
    pipeline_svc.enqueue_translate(b["id"], user["id"], [c["num"]], job_type="repair")
    return {"ok": True}


@app.post("/api/chapters/{chapter_id}/regen-tts")
def chapter_regen_tts(chapter_id: int, user: dict = Depends(auth_svc.current_user)):
    c = repo.get_chapter(chapter_id)
    if not c:
        raise HTTPException(404)
    b = repo.get_book(c["book_id"])
    if b["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403)
    if c["status"] != "translated":
        raise HTTPException(400, "глава ещё не переведена")
    if c["tts_status"] == "queued":
        raise HTTPException(409, "глава уже в очереди на озвучку")
    repo.update_chapter(chapter_id, tts_status="queued", tts_error="", audio_parts=0)
    # удаляем старые mp3 части и маркер готовности
    ad = books_svc.audio_dir(b["id"])
    for f in ad.glob(f"{c['num']:03d}_ru_part*.mp3"):
        f.unlink(missing_ok=True)
    (ad / f"{c['num']:03d}_ru.done").unlink(missing_ok=True)
    repo.create_job(book_id=b["id"], chapter_id=chapter_id, owner_id=user["id"], type="tts")
    return {"ok": True}


@app.put("/api/chapters/{chapter_id}/translation")
def chapter_save_translation(chapter_id: int, payload: dict,
                             user: dict = Depends(auth_svc.current_user)):
    """Ручное сохранение/правка перевода главы (из диф-режима)."""
    c = repo.get_chapter(chapter_id)
    if not c:
        raise HTTPException(404)
    b = repo.get_book(c["book_id"])
    if b["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403)
    text = (payload.get("text") or "").strip()
    if not text:
        raise HTTPException(400, "текст пуст")
    ru = books_svc.ru_path(b["id"], c["num"])
    ru.write_text(text + "\n", encoding="utf-8", newline="")
    # перевод изменён вручную: аудио старой версии недействительно
    ad = books_svc.audio_dir(b["id"])
    for f in ad.glob(f"{c['num']:03d}_ru_part*.mp3"):
        f.unlink(missing_ok=True)
    (ad / f"{c['num']:03d}_ru.done").unlink(missing_ok=True)
    repo.update_chapter(chapter_id, status="translated", error="", ru_path=str(ru),
                        tts_status="none", tts_error="", audio_parts=0)
    books_svc.rebuild_translated_txt(b["id"])
    return {"ok": True, "status": "translated", "tts_status": "none"}


@app.post("/api/chapters/{chapter_id}/revise")
def chapter_revise(chapter_id: int, payload: dict, user: dict = Depends(auth_svc.current_user)):
    """Пере-перевод главы с учётом замечания пользователя (отдельный revise-промпт)."""
    c = repo.get_chapter(chapter_id)
    if not c:
        raise HTTPException(404)
    b = repo.get_book(c["book_id"])
    if b["owner_id"] != user["id"] and user["role"] != "admin":
        raise HTTPException(403)
    feedback = (payload.get("feedback") or "").strip()
    if not feedback:
        raise HTTPException(400, "напишите, что поправить в переводе")
    try:
        n = pipeline_svc.enqueue_revise(b["id"], user["id"], c["num"], feedback)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    return {"ok": True, "created": n}


# ---------------------------------------------------------------- quality
@app.post("/api/books/{book_id}/quality-check")
def quality_check(book_id: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    issues_map: dict[int, list[str]] = {}
    for c in repo.list_chapters(book_id):
        if c["status"] != "translated":
            continue
        ru = Path(c["ru_path"]) if c["ru_path"] else books_svc.ru_path(book_id, c["num"])
        zh = Path(c["zh_path"]) if c["zh_path"] else books_svc.zh_path(book_id, c["num"])
        if not ru.exists() or not zh.exists():
            continue
        issues = quality_svc.suspicious_issues(
            zh.read_text(encoding="utf-8"), ru.read_text(encoding="utf-8"))
        if issues:
            issues_map[c["num"]] = issues
    return {"ok": True, "suspicious": quality_svc.summarize_issues(issues_map)}


def _auth_from_query(request: Request, user: dict) -> dict:
    """Аудио-тег <audio> не умеет слать Bearer-заголовок — разрешаем ?token=."""
    qtoken = request.query_params.get("token")
    if qtoken and user is None:
        from .services import auth as _a
        try:
            payload = _a._decode_token(qtoken)  # noqa: SLF001
            u = repo.get_user(int(payload["sub"]))
            if u and u["is_active"]:
                return u
        except HTTPException:
            pass
    return user


def _audio_auth(request: Request, book_id: int, header_user: dict | None):
    user = _auth_from_query(request, header_user)
    return _owner_book(book_id, user)


# ---------------------------------------------------------------- audio serving
@app.get("/api/books/{book_id}/audio/{name}")
def serve_audio(book_id: int, name: str, request: Request,
                user: dict | None = Depends(auth_svc.current_optional)):
    """Аудио ГЛАВЫ (новый конвейер): всегда из data/library/{id}/audio.
    НЕ legacy-каталог — иначе новая озвучка главы подменяется старой записью
    с тем же именем файла из legacy_audio_dir."""
    _audio_auth(request, book_id, user)
    safe = Path(name).name
    base = books_svc.audio_dir(book_id)
    file = base / safe
    if not file.exists() or file.suffix.lower() != ".mp3":
        raise HTTPException(404)
    return _serve_mp3(file, request)


@app.get("/api/books/{book_id}/stream/{name}")
def serve_stream(book_id: int, name: str, request: Request,
                 user: dict | None = Depends(auth_svc.current_optional)):
    """Легаси-поток (старые mp3 из legacy_audio_dir)."""
    _audio_auth(request, book_id, user)
    b = repo.get_book(book_id)
    base = Path(b["legacy_audio_dir"]) if b and b.get("legacy_audio_dir") else books_svc.audio_dir(book_id)
    safe = Path(name).name
    file = base / safe
    if not file.exists() or file.suffix.lower() != ".mp3":
        raise HTTPException(404)
    return _serve_mp3(file, request)


def _serve_mp3(file: Path, request: Request):
    size = file.stat().st_size
    ctype = mimetypes.guess_type(str(file))[0] or "audio/mpeg"
    rng = request.headers.get("Range")
    if rng:
        m = re.match(r"bytes=(\d*)-(\d*)", rng)
        if m:
            start = int(m.group(1)) if m.group(1) else 0
            end = int(m.group(2)) if m.group(2) else size - 1
            end = min(end, size - 1)
            if start >= size or end < start:
                return JSONResponse(status_code=416, content={})
            length = end - start + 1
            headers = {
                "Content-Range": f"bytes {start}-{end}/{size}",
                "Accept-Ranges": "bytes",
                "Cache-Control": "public, max-age=60",
            }
            return StreamingResponse(
                _file_chunks(file, start, length),
                status_code=206, media_type=ctype, headers=headers)
    return FileResponse(file, media_type=ctype, headers={"Accept-Ranges": "bytes"})


def _file_chunks(path: Path, start: int, length: int):
    with path.open("rb") as fh:
        fh.seek(start)
        remaining = length
        while remaining > 0:
            chunk = fh.read(min(262144, remaining))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk


# ---------------------------------------------------------------- downloads
@app.get("/api/books/{book_id}/download/chapter/{num}.txt")
def download_chapter_txt(book_id: int, num: int, lang: str = "ru",
                         user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    p = books_svc.ru_path(book_id, num) if lang == "ru" else books_svc.zh_path(book_id, num)
    if not p.exists():
        raise HTTPException(404)
    return FileResponse(p, media_type="text/plain; charset=utf-8",
                        filename=f"{num:03d}_{lang}.txt")


@app.get("/api/books/{book_id}/download/chapter/{num}.mp3")
def download_chapter_mp3(book_id: int, num: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    parts = download_svc.chapter_audio_parts(book_id, num)
    if not parts:
        raise HTTPException(404, "аудио для главы нет")
    if len(parts) == 1:
        return FileResponse(parts[0], media_type="audio/mpeg", filename=f"{num:03d}.mp3")
    tmp = Path(tempfile.gettempdir()) / f"tab_{book_id}_{num:03d}.mp3"
    download_svc.concat_mp3(parts, tmp)
    return FileResponse(tmp, media_type="audio/mpeg", filename=f"{num:03d}.mp3")


@app.get("/api/books/{book_id}/download/audio.zip")
def download_audio_zip(book_id: int, user: dict = Depends(auth_svc.current_user)):
    """Аудио книги целиком. chaptered -> новый каталог глав (data/library/{id}/audio);
    stream -> legacy_audio_dir. Не путать: для chaptered НЕ брать legacy (там старые записи)."""
    _owner_book(book_id, user)
    b = repo.get_book(book_id)
    if b.get("mode") == "stream" and b.get("legacy_audio_dir"):
        base = Path(b["legacy_audio_dir"])
        files = sorted(base.glob("*_ru_part*.mp3"))
        import io, zipfile
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_STORED) as zf:
            for f in files:
                zf.write(f, arcname=f.name)
        buf.seek(0)
        return StreamingResponse(buf, media_type="application/zip",
                                 headers={"Content-Disposition": "attachment; filename=audio.zip"})
    tmp = Path(tempfile.gettempdir()) / f"tab_{book_id}_audio.zip"
    download_svc.build_audio_zip(book_id, tmp)
    return FileResponse(tmp, media_type="application/zip", filename="audio.zip")


# ---------------------------------------------------------------- progress
@app.get("/api/books/{book_id}/progress")
def get_progress(book_id: int, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    p = repo.get_progress(user["id"], book_id)
    return p or {"chapter_num": 1, "position_sec": 0}


@app.put("/api/books/{book_id}/progress")
def put_progress(book_id: int, payload: dict, user: dict = Depends(auth_svc.current_user)):
    _owner_book(book_id, user)
    num = int(payload.get("chapter_num", 1))
    pos = float(payload.get("position_sec", 0) or 0)
    repo.set_progress(user["id"], book_id, num, pos)
    return {"ok": True}


# ---------------------------------------------------------------- legacy import (тест)
@app.post("/api/admin/import-stream")
def admin_import_stream(title: str = Form(...), source: str = Form(""),
                        translated: str = Form(""), audio_dir: str = Form(""),
                        owner_email: str = Form(""),
                        _: dict = Depends(auth_svc.current_admin)):
    owner = repo.get_user_by_email(owner_email.strip().lower()) if owner_email else None
    if not owner:
        raise HTTPException(400, "укажите существующий email владельца")
    res = legacy_svc.import_stream_book(
        owner_id=owner["id"], title=title,
        source_file=source or None, translated_file=translated or None,
        audio_dir=audio_dir or None)
    return {"ok": True, **res}


# ---------------------------------------------------------------- static frontend
def _mount_frontend(app: FastAPI) -> None:
    dist = config.DIST_DIR
    assets = dist / "assets"
    if assets.exists():
        app.mount("/assets", StaticFiles(directory=str(assets)), name="assets")

    @app.get("/{full_path:path}", include_in_schema=False)
    def spa_fallback(full_path: str):
        # /api/* уже обработан роутами выше; неизвестные пути — отдаём index.html (SPA)
        if full_path.startswith("api/"):
            raise HTTPException(404, "Not Found")
        index = dist / "index.html"
        if index.exists():
            return FileResponse(index, media_type="text/html")
        return {"app": "TranslatorAudioBook API", "docs": "/docs",
                "hint": "фронтенд не собран — запустите сборку web/ (vite build)"}


if config.DIST_DIR.exists() and (config.DIST_DIR / "index.html").exists():
    _mount_frontend(app)
else:
    @app.get("/")
    def root():
        return {"app": "TranslatorAudioBook API", "docs": "/docs",
                "hint": "фронтенд не собран — запустите сборку web/ (vite build)"}
