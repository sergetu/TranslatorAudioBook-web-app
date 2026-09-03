"""SQLite-реализация repository-слоя. Каждый вызов открывает своё соединение (WAL)."""
from __future__ import annotations

from typing import Any

from .. import db


def _row_to_dict(row) -> dict:
    return dict(row) if row is not None else None


class SqliteRepo:
    # ---- users ----
    def create_user(self, email, password_hash, name, role, is_active=1) -> dict:
        with db.conn_ctx() as conn:
            cur = conn.execute(
                "INSERT INTO users (email, password_hash, name, role, is_active, created_at) "
                "VALUES (?,?,?,?,?,?)",
                (email, password_hash, name, role, is_active, db.utcnow()),
            )
            uid = cur.lastrowid
        return self.get_user(uid)

    def get_user_by_email(self, email: str) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(conn.execute("SELECT * FROM users WHERE email = ?", (email,)).fetchone())

    def get_user(self, user_id: int) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(conn.execute("SELECT * FROM users WHERE id = ?", (user_id,)).fetchone())

    def list_users(self) -> list[dict]:
        with db.conn_ctx() as conn:
            rows = conn.execute("SELECT * FROM users ORDER BY id").fetchall()
        return [dict(r) for r in rows]

    def update_user(self, user_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.conn_ctx() as conn:
            conn.execute(f"UPDATE users SET {cols} WHERE id = ?", (*fields.values(), user_id))

    def count_users(self) -> int:
        with db.conn_ctx() as conn:
            return conn.execute("SELECT COUNT(*) AS c FROM users").fetchone()["c"]

    # ---- books ----
    def create_book(self, owner_id: int, **fields: Any) -> int:
        cols = ("owner_id", *fields.keys())
        vals = (owner_id, *fields.values())
        now = db.utcnow()
        with db.conn_ctx() as conn:
            cur = conn.execute(
                f"INSERT INTO books ({', '.join(cols)}, created_at, updated_at) "
                f"VALUES ({', '.join('?' * len(cols))}, ?, ?)",
                (*vals, now, now),
            )
            return cur.lastrowid

    def get_book(self, book_id: int) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(conn.execute("SELECT * FROM books WHERE id = ?", (book_id,)).fetchone())

    def list_books(self, owner_id: int) -> list[dict]:
        with db.conn_ctx() as conn:
            rows = conn.execute("SELECT * FROM books WHERE owner_id = ? ORDER BY updated_at DESC", (owner_id,)).fetchall()
        return [dict(r) for r in rows]

    def update_book(self, book_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.conn_ctx() as conn:
            conn.execute(f"UPDATE books SET {cols}, updated_at = ? WHERE id = ?",
                         (*fields.values(), db.utcnow(), book_id))

    def delete_book(self, book_id: int) -> None:
        with db.conn_ctx() as conn:
            conn.execute("DELETE FROM books WHERE id = ?", (book_id,))

    def touch_book(self, book_id: int) -> None:
        self.update_book(book_id)

    # ---- chapters ----
    def replace_chapters(self, book_id: int, chapters: list[dict]) -> None:
        now = db.utcnow()
        with db.conn_ctx() as conn:
            conn.execute("DELETE FROM chapters WHERE book_id = ?", (book_id,))
            conn.executemany(
                "INSERT INTO chapters (book_id, num, title, source_chars, zh_path, ru_path, "
                "status, tts_status, updated_at) VALUES (?,?,?,?,?,?,?,?,?)",
                [
                    (book_id, c["num"], c["title"], c.get("source_chars", 0),
                     c.get("zh_path", ""), c.get("ru_path", ""),
                     c.get("status", "none"), c.get("tts_status", "none"), now)
                    for c in chapters
                ],
            )

    def list_chapters(self, book_id: int) -> list[dict]:
        with db.conn_ctx() as conn:
            rows = conn.execute("SELECT * FROM chapters WHERE book_id = ? ORDER BY num", (book_id,)).fetchall()
        return [dict(r) for r in rows]

    def get_chapter(self, chapter_id: int) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(conn.execute("SELECT * FROM chapters WHERE id = ?", (chapter_id,)).fetchone())

    def get_chapter_by_num(self, book_id: int, num: int) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(
                conn.execute("SELECT * FROM chapters WHERE book_id = ? AND num = ?", (book_id, num)).fetchone()
            )

    def update_chapter(self, chapter_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.conn_ctx() as conn:
            conn.execute(f"UPDATE chapters SET {cols}, updated_at = ? WHERE id = ?",
                         (*fields.values(), db.utcnow(), chapter_id))

    def count_chapters(self, book_id: int, status: str | None = None) -> int:
        with db.conn_ctx() as conn:
            if status is None:
                return conn.execute("SELECT COUNT(*) AS c FROM chapters WHERE book_id = ?", (book_id,)).fetchone()["c"]
            return conn.execute(
                "SELECT COUNT(*) AS c FROM chapters WHERE book_id = ? AND status = ?", (book_id, status)
            ).fetchone()["c"]

    # ---- jobs ----
    def create_job(self, **fields: Any) -> int:
        now = db.utcnow()
        with db.conn_ctx() as conn:
            cur = conn.execute(
                "INSERT INTO jobs (book_id, chapter_id, owner_id, type, status, priority, "
                "progress, error, note, attempts, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (fields.get("book_id"), fields.get("chapter_id"), fields.get("owner_id"),
                 fields["type"], fields.get("status", "queued"), fields.get("priority", 5),
                 fields.get("progress", 0), "", fields.get("note", ""), 0, now),
            )
            return cur.lastrowid

    def get_job(self, job_id: int) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(conn.execute("SELECT * FROM jobs WHERE id = ?", (job_id,)).fetchone())

    def list_jobs(self, book_id: int | None = None, owner_id: int | None = None) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE 1=1"
        args: list = []
        if book_id is not None:
            sql += " AND book_id = ?"
            args.append(book_id)
        if owner_id is not None:
            sql += " AND owner_id = ?"
            args.append(owner_id)
        sql += " ORDER BY id DESC LIMIT 500"
        with db.conn_ctx() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def update_job(self, job_id: int, **fields: Any) -> None:
        if not fields:
            return
        cols = ", ".join(f"{k} = ?" for k in fields)
        with db.conn_ctx() as conn:
            conn.execute(f"UPDATE jobs SET {cols} WHERE id = ?", (*fields.values(), job_id))

    def claim_job(self, job_id: int) -> bool:
        """queued -> running атомарно; False если кто-то уже взял."""
        with db.conn_ctx() as conn:
            cur = conn.execute(
                "UPDATE jobs SET status='running', started_at=? WHERE id=? AND status='queued'",
                (db.utcnow(), job_id),
            )
            return cur.rowcount == 1

    def next_queued_jobs(self, limit: int, exclude_ids: tuple[int, ...] = ()) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE status='queued' AND type != 'tts'"
        args: list = []
        if exclude_ids:
            sql += f" AND id NOT IN ({','.join('?' * len(exclude_ids))})"
            args.extend(exclude_ids)
        sql += " ORDER BY priority DESC, id ASC LIMIT ?"
        args.append(limit)
        with db.conn_ctx() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def next_queued_by_type(self, job_type: str, limit: int, exclude_ids: tuple[int, ...] = ()) -> list[dict]:
        sql = "SELECT * FROM jobs WHERE status='queued' AND type = ?"
        args: list = [job_type]
        if exclude_ids:
            sql += f" AND id NOT IN ({','.join('?' * len(exclude_ids))})"
            args.extend(exclude_ids)
        sql += " ORDER BY priority DESC, id ASC LIMIT ?"
        args.append(limit)
        with db.conn_ctx() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    def next_llm_jobs(self, limit: int, exclude_ids: tuple[int, ...] = ()) -> list[dict]:
        """Все LLM-задачи (translate|repair|revise) — под один слот параллельности."""
        sql = "SELECT * FROM jobs WHERE status='queued' AND type IN ('translate','repair','revise')"
        args: list = []
        if exclude_ids:
            sql += f" AND id NOT IN ({','.join('?' * len(exclude_ids))})"
            args.extend(exclude_ids)
        sql += " ORDER BY priority DESC, id ASC LIMIT ?"
        args.append(limit)
        with db.conn_ctx() as conn:
            rows = conn.execute(sql, args).fetchall()
        return [dict(r) for r in rows]

    # ---- settings / progress ----
    def get_setting(self, key: str, default: str = "") -> str:
        with db.conn_ctx() as conn:
            return db.get_setting(conn, key, default)

    def set_setting(self, key: str, value: str) -> None:
        with db.conn_ctx() as conn:
            db.set_setting(conn, key, value)

    def all_settings(self) -> dict:
        with db.conn_ctx() as conn:
            rows = conn.execute("SELECT key, value FROM settings").fetchall()
        return {r["key"]: r["value"] for r in rows}

    def get_progress(self, user_id: int, book_id: int) -> dict | None:
        with db.conn_ctx() as conn:
            return _row_to_dict(
                conn.execute("SELECT * FROM progress WHERE user_id = ? AND book_id = ?",
                             (user_id, book_id)).fetchone()
            )

    def set_progress(self, user_id: int, book_id: int, chapter_num: int, position_sec: float) -> None:
        with db.conn_ctx() as conn:
            conn.execute(
                "INSERT INTO progress (user_id, book_id, chapter_num, position_sec, updated_at) "
                "VALUES (?,?,?,?,?) ON CONFLICT(user_id, book_id) DO UPDATE SET "
                "chapter_num=excluded.chapter_num, position_sec=excluded.position_sec, "
                "updated_at=excluded.updated_at",
                (user_id, book_id, chapter_num, position_sec, db.utcnow()),
            )


repo = SqliteRepo()
