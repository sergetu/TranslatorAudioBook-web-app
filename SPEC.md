# SPEC: TranslatorAudioBook v2 — мультипользовательская веб-платформа перевода и озвучки книг

## Overview

Превратить локальный однопользовательский пайплайн (txt → чанки → перевод HY-MT1.5 на koboldcpp → озвучка edge-tts → плеер) в веб-приложение с пользователями, библиотеками книг (исходник + перевод + аудио), загрузкой файлов через браузер, обработкой по главам с видимой очередью и скачиванием результатов. Целевая аудитория — до 5 человек в локальной сети, архитектура с заделом под будущий публичный сервис (изоляция пользователей, repository-слой, контейнеризируемый бэкенд).

**Допущения о машине (из логов прогонов):** koboldcpp один на машине, последовательная генерация (~6 ГБ VRAM), порт 5003; edge-tts — облачный Microsoft API, нужен интернет. Отсюда: лимит параллельных генераций = 1, всё остальное — очередь.

## Стек (утверждён)

- **Бэкенд:** Python 3.11+, FastAPI, Uvicorn; SQLite (stdlib `sqlite3` через repository-слой); Pydantic v2.
- **Хранение:** SQLite для метаданных/статусов/очереди; файловая система для текстов глав и mp3 (данные большие). Repository-слой абстрагирует сущности так, чтобы замена SQLite → MongoDB позже не трогала бизнес-логику. Двоичные файлы в MongoDB не переносим — только метаданные/индексы.
- **Фронтенд:** Vite + React (JS, без TypeScript) + Redux Toolkit + SCSS. Одна страница-приложение, API-клиент на fetch.
- **Инференс/перевод — два провайдера за общим интерфейсом:**
- **local (koboldcpp):** существующий, `D:\AI\koboldcpp.exe` + `HY-MT1.5-7B-Q4_K_M.gguf`, лимит 1 параллельная генерация, бесплатно, качество локальной 7B.
- **deepseek (API):** DeepSeek API (OpenAI-совместимый `https://api.deepseek.com`), ключ из настроек/env, модель на выбор (по умолчанию та, что уже используется пользователем, напр. `deepseek-v4-flash`). Выше качество/скорость на длинных текстах, но платно за токены и нужен интернет.

Выбор провайдера — глобально (Setting) и per-book (`book.translator`); одинаковые промпты/чистка результата для обоих, чтобы перевод был сопоставим. Очередь: local — строго 1 задача; deepseek — допускает 2–4 параллельных запроса (настраивается).
- **Старые скрипты** (`split_zh_chunks.py`, `translate_zh_chunks_local.py`, `repair_suspicious_chunks.py`, `make_tts_edge_reader.py`, `serve_audiobook.py`) остаются в `scripts/` как референс/CLI; новая логика — в `app/`, переиспользуя их функции (нарезка, промпты, clean, TTS-сплит).

## Data Models

```
User: id, username(unique), password_hash, role: user|admin,
      created_at, is_active

Book: id, owner_id->User, title, author?, source_lang (zh), target_lang (ru),
      mode: chaptered|stream,            # chaptered — новая обработка по главам;
                                          # stream — импортированная «готовая» книга без глав
      translator: local|deepseek,         # движок перевода для этой книги
      status: empty|chunked|translated|tts_done (агрегат, пересчитывается),
      source_filename, source_encoding (utf-8|gb18030),
      cover_path?, created_at, updated_at,
      source_chars, chapters_total,
      legacy_audio_dir?                  # для stream-книг: путь к готовому каталогу mp3

Chapter: id, book_id->Book, num (1..N), title (заголовок из исходника),
         source_path (файл-чанк/файлы главы на диске),
         status: none|queued|translated|repair|tts_queued|tts_done|error,
         error?, translated_chars?, audio_parts (кол-во mp3), updated_at

Job: id, book_id, chapter_id?, owner_id, type: chunk|translate|repair|tts|import,
     status: queued|running|done|error|canceled,
     priority (int), progress (0..100), error?, attempts,
     created_at, started_at, finished_at
     # Очередь: type translate/repair -> worker "llm" (лимит 1);
     #         type tts -> worker "tts" (edge-tts, лимит 1-2); chunk/import быстрые.

Setting: key, value  (голос TTS по умолчанию, rate, context tokens, пути к модели и koboldcpp,
                       deepseek_api_key, deepseek_model, deepseek_base_url, concurrency)
```

Файловая раскладка на диске (корень проекта):

```
data/library/{book_id}/source.txt           # оригинал как загружен
data/library/{book_id}/chapters/{num:03d}_zh.txt
data/library/{book_id}/chapters/{num:03d}_ru.txt
data/library/{book_id}/audio/{num:03d}_part*.mp3, {num:03d}.done
data/library/{book_id}/translated.txt       # склейка всего перевода
data/library/{book_id}/cover.jpg
data/uploads/                                # временная зона приёма файлов
```

## User Flows / API Contracts

### 1. Auth
- `POST /api/auth/register` {username,password} — только если включена регистрация (Setting); при локальном развёртывании admin создаёт пользователей.
- `POST /api/auth/login` → {token}; токен — подписанный JWT (задел под публичный сервис), хранится в localStorage.
- `GET /api/auth/me`; `GET /api/users` (admin).
- Авторизация: `Authorization: Bearer <token>`. Каждый пользователь видит только свои книги и свои задачи (owner_id-фильтр на уровне repository).

### 2. Библиотека
- `GET /api/books` — список своих книг {id,title,author,mode,status,chapters_total,progress}.
- `POST /api/books` {title?,author?} — создать пустую.
- `DELETE /api/books/{id}` — удалить запись + файлы (с подтверждением на UI).
- `POST /api/books/{id}/source` (multipart: файл .txt) — загрузка исходника; сервер детектит кодировку (utf-8/gb18030), считает символы, детектит главы.
- `POST /api/books/{id}/cover` (multipart: image) — обложка (опц.).
- `POST /api/books/{id}/scan` — (пере)определить главы: парсер `第N章` / `第N节` / `第N回`; результат — список глав {num,title,chars}; при mode=stream — только перевод в поток.

### 3. Обработка (очередь)
- `POST /api/books/{id}/pipeline` {stage: all|chunk|translate|tts, chapters?: [..]} — поставить задачи.
- `GET /api/books/{id}/chapters` — статусы глав + прогресс.
- `GET /api/jobs?book_id=` — история/очередь с живым статусом (polling 2–3 c).
- `POST /api/jobs/{id}/cancel`; `POST /api/chapters/{id}/retry` (пере-перевод/repair); `POST /api/chapters/{id}/regen-tts` — озвучить главу заново (после ручной правки перевода).
- Детект «подозрительных» глав (обрыв, «...», слишком короткий/длинный перевод) — как в `repair_suspicious_chunks.py`, предлагается в UI кнопкой «Проверить качество».
- Воркеры: процесс-диспетчер (внутри uvicorn или отдельный `python -m app.worker`), берёт из очереди: 1 задача LLM за раз, TTS — 1–2. Статус пишется в БД, mp3 появляются по главам.

### 4. Чтение и прослушивание
- `GET /api/books/{id}/chapters/{num}` → {zh, ru, title, audio: [url...]}.
- `GET /api/books/{id}/progress` PUT — позиция прослушивания/чтения на аккаунт (глава + секунда), синхронизация устройств.
- Плеер: глава = плейлист её mp3-частей, автопереход на следующую главу, сохранение позиции в БД. Текст главы (ru) синхронно под плеером; переключение zh/ru.

### 5. Скачивание
- `GET /api/books/{id}/download/translated.txt` — весь перевод (склейка).
- `GET /api/books/{id}/download/source.txt` — исходник.
- `GET /api/chapters/{id}/download.txt` | `/download.mp3` (глава одним mp3, склейка частей на лету через ffmpeg, если доступен; иначе zip частей).
- `GET /api/books/{id}/download/audio.zip` — все mp3 книги.
- Все скачивания — только владельцу.

### 6. Stream-книги (легаси «开局…»)
- `POST /api/books` {mode:stream, legacy_audio_dir} — зарегистрировать готовую книгу: исходник + готовый `_HYMT15_8K_RU.txt` + существующий каталог mp3 → плеер/скачивание работают как раньше (без глав), не трогая старые файлы. Позиция — на аккаунт.

## File Structure (новое)

```
TranslatorAudioBook-web-app/
├─ app/
│  ├─ main.py                  # FastAPI app, роуты, static
│  ├─ config.py                # пути, настройки, env
│  ├─ db.py                    # подключение sqlite + миграции (простой version-счётчик)
│  ├─ models.py                # Pydantic-схемы (API-контракты)
│  ├─ repositories/            # repository-слой: users.py, books.py, chapters.py, jobs.py
│  │   └─ _base.py             # интерфейс (задел под MongoDB-реализацию)
│  ├─ services/
│  │  ├─ auth.py               # JWT, хэши паролей
│  │  ├─ encoding.py           # детект utf-8/gb18030
│  │  ├─ chapterizer.py        # парсер глав 第N章/节/回, нарезка глав→чанки (внутри главы)
│  │  ├─ translator.py         # интерфейс TranslatorProvider: local (koboldcpp, переиспользует
  │  │                        #   промпты/clean из translate_zh_chunks_local) + deepseek_api
  │  │                        #   (OpenAI-совместимый клиент, ключ из config/env)
│  │  ├─ quality.py            # детект подозрительных глав (из repair_suspicious_chunks)
│  │  ├─ tts.py                # edge-tts (переиспользует split_for_tts/clean_for_tts из make_tts_edge_reader)
│  │  └─ download.py           # txt/mp3/zip, ffmpeg-склейка
│  ├─ workers.py               # диспетчер очереди: llm-воркер (лимит 1), tts-воркер
│  └─ legacy_import.py         # импорт старых артефактов в stream-книгу
├─ web/                        # фронт: Vite + React(JS) + RTK + SCSS
│  ├─ index.html, vite.config.js, src/{main.jsx, app/, pages/, api/, store/, styles/}
├─ scripts/                    # старые CLI-скрипты (референс, без изменений)
├─ data/...                    # см. раскладку выше (library/ внутри)
├─ run/                        # cmd: 01_start_model, 06_server (uvicorn), 07_worker, 08_web_dev/build
├─ README.md                   # обновить
└─ SPEC.md
```

Миграция старых данных: **не выполняем**. Старые чанки/аудио остаются как есть; для «开局…» создаётся stream-книга по желанию. Новые книги обрабатываются по-главно.

## Edge Cases & Error Handling

- **Загрузка не-китайского/бинарного файла** → 400. Пустой файл → 400. Файл > лимита (50 МБ) → 413.
- **Кодировка не определена** → 400 с просьбой сконвертировать; gb18030 конвертируем автоматически.
- **Нет ни одной главы** (`第N章` не найдено) → книга помечается mode=stream или предлагается ручная разметка (вне v1 — только сообщение).
- **koboldcpp недоступен** (порт 5003 не отвечает) → задачи translate встают в очередь со статусом waiting, на UI баннер «модель не запущена» + кнопка-подсказка (run/01_start_model.cmd). Ошибки генерации — retry с экспоненциальной паузой, max 3 попытки, потом chapter.status=error + текст ошибки.
- **DeepSeek API**: нет ключа/не задан → книга с translator=deepseek не запускается, UI просит задать ключ в настройках (ключ хранится только в Setting/env на сервере, в API наружу не отдаётся). 401/402/429/5xx → retry; 402 (insufficient balance) и 401 (bad key) → задача в error с понятным текстом, без слепых ретраев. Стоимость: до запуска pipeline UI показывает оценку токенов книги × тариф модели (ориентировочно).
- **edge-tts недоступен/лимит** → те же retry-правила (как в старом коде: transient → отложить).
- **Два пользователя запускают pipeline одновременно** → очередь едина, лимит LLM=1 гарантирует последовательность; прогресс по книгам независим.
- **Удаление книги во время обработки** → задачи книги отменяются (status=canceled), файлы удаляются после завершения активной генерации (или помечаются на удаление).
- **Обрыв сервера/воркера** → при старте: задачи running → queued (перезапуск), файлы проверяются на существование (чанк переведён, но статус не записан → детект по наличию файла, как в load_state).
- **Глава огромная/абзац длиннее контекста** → режется на части внутри главы (существующая логика split_long_text); части хранятся под той же главой.
- **Имя файла с кириллицей/пробелами/иероглифами** — только в БД/метаданных; на диске — числовые имена ({book_id}, {num:03d}).
- **Позиция прослушивания** при пере-озвучке главы — сброс только этой главы.
- **Скачивание во время активной озвучки** — zip по текущему состоянию (что готово).

## Out of Scope (v1)

- Редактор перевода в браузере (правка текста главы — только на диске + «regen-tts»; UI-редактор — следующий этап).
- Импорт epub/docx/html.
- m4b с маркерами глав.
- Авто-разметка глав для не-`第N章` текстов.
- Синтез на лету «слушать начало, пока переводится остальное».
- Email/восстановление пароля, OAuth (при локальном развёртывании admin создаёт пользователей).
- Деплой-инфраструктура публичного сервиса (только задел в коде: JWT, изоляция, repository-слой, конфиг через env).

## Open Questions — решения (утверждены пользователем)

1. **Модель перевода:** локальный провайдер должен позволять свою модель через koboldcpp (имя модели/URL в настройках); по умолчанию для тестов — HY-MT1.5-7B. Альтернатива — DeepSeek.
2. **Адрес:** uvicorn на `0.0.0.0:8090` (LAN, как старый плеер).
3. **Регистрация:** первый зарегистрировавшийся пользователь становится **admin**; далее — форма регистрации с **email + пароль** (email = логин). Восстановление пароля — **без подтверждения почты** в v1: admin сбрасывает пароль пользователю (endpoint админки). Открытая регистрация управляется Setting-флагом.
4. **ffmpeg** — есть (v9.0.1) → «глава одним mp3» через ffmpeg работает.
5. **Импорт «开局…» в v1 — ДА**, как stream-книга (для тестирования).
6. **DeepSeek:** ключ **прописывает пользователь сам в админке**; хранится в **отдельном файле-секрете** (`data/secrets/deepseek_api_key`), который агент/модель **не читает**; ключ не логируется, не попадает в API-ответы (наружу — только флаг «задан/не задан`). Модель по умолчанию — `deepseek-chat`, base_url `https://api.deepseek.com`.

## Changelog (v2)

- 2026-09-03: утверждены стек (FastAPI+SQLite/репозитории, Vite+React+RTK+SCSS), масштаб (до 5 чел LAN, задел под публичный), пакет A + DeepSeek-провайдер, решения Open Questions выше.

---

SPEC complete. Review above and confirm before I write any code.
Reply **«go»** to proceed, or tell me what to change.
