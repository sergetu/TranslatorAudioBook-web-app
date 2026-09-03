import { useCallback, useEffect, useRef, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { useParams, Link, useNavigate } from 'react-router-dom'
import {
  loadBooks, scanBook, runPipeline, qualityCheck, uploadSource, uploadCover,
  deleteBookThunk, loadJobs, clearError,
} from '../store'
import { api, getToken, downloadFile } from '../api'

export default function BookPage() {
  const { id } = useParams()
  const bookId = Number(id)
  const navigate = useNavigate()
  const dispatch = useDispatch()
  const user = useSelector((s) => s.auth.user)
  const [book, setBook] = useState(null)
  const [jobs, setJobs] = useState([])
  const [busy, setBusy] = useState(false)
  const [msg, setMsg] = useState('')
  const token = getToken()

  const load = useCallback(async () => {
    try { setBook(await api.get(`/api/books/${bookId}`)) } catch { /* 404 */ }
    try { setJobs(await api.get(`/api/jobs?book_id=${bookId}`)) } catch { /* */ }
  }, [bookId])

  useEffect(() => { load() }, [load])
  const activeJobs = jobs.filter((j) => j.status === 'queued' || j.status === 'running')
  useEffect(() => {
    if (!activeJobs.length) return
    const t = setInterval(load, 2500)
    return () => clearInterval(t)
  }, [activeJobs.length, load])

  const srcInput = useRef(null)
  const covInput = useRef(null)

  if (!book) return <main className="page"><p>Загрузка…</p></main>

  const isStream = book.mode === 'stream'
  const total = book.chapters_total || 0
  const translated = book.translated_chapters || 0
  const ttsDone = book.tts_done_chapters || 0
  const coverUrl = book.cover_path ? `/api/books/${bookId}/cover?token=${token}` : null

  async function refresh() { await load(); dispatch(loadBooks()) }

  async function act(fn, okMsg) {
    setBusy(true); setMsg('')
    try { await fn(); okMsg && setMsg(okMsg); await refresh() }
    catch (e) { setMsg('Ошибка: ' + (e.message || e)) }
    finally { setBusy(false) }
  }

  async function onFile(e, kind) {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    await act(async () => { await dispatch(uploadSource({ bookId, file })).unwrap() }, 'Исходник загружен. Нажмите «Скан глав».')
    e.target.value = ''
  }
  async function onCover(e) {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    await act(async () => { await dispatch(uploadCover({ bookId, file })).unwrap() }, 'Обложка обновлена')
    e.target.value = ''
  }
  async function doConvert() {
    await act(async () => { await api.post(`/api/books/${bookId}/convert`, new FormData()) },
      'Книга нарезана на главы. Прежний полный перевод и аудио сохранены как «легаси».')
  }
  async function doScan() {
    await act(async () => { await dispatch(scanBook(bookId)).unwrap() }, 'Главы обновлены')
  }
  async function doPipeline(stage) {
    await act(async () => { await dispatch(runPipeline({ bookId, stage })).unwrap() }, 'Задачи поставлены в очередь')
  }
  async function doQuality() {
    await act(async () => {
      const r = await dispatch(qualityCheck(bookId)).unwrap()
      setMsg(r.suspicious.length ? `Проверено: ${r.suspicious.length} подозрительных глав (см. читалку).` : 'Все переведённые главы выглядят нормально')
    })
  }
  async function doDelete() {
    if (!confirm(`Удалить книгу «${book.title}» и все её файлы?`)) return
    await dispatch(deleteBookThunk(bookId)).unwrap()
    navigate('/')
  }

  const canEdit = user?.role === 'admin' || book.owner_id === user?.id

  return (
    <main className="page">
      <div className="page-head">
        <Link to="/" className="back">← Библиотека</Link>
        <h2>{book.title}</h2>
        {book.author && <span className="muted">{book.author}</span>}
        <div className="spacer" />
        {activeJobs.length > 0 && <span className="badge pulse">⏳ {activeJobs.length} задач</span>}
        <button className="danger-ghost" onClick={doDelete}>🗑 Удалить</button>
      </div>

      {msg && <div className={msg.startsWith('Ошибка') ? 'error' : 'ok'}>{msg}</div>}

      <div className="book-home">
        <div className="book-cover big">
          {coverUrl ? <img src={coverUrl} alt="" /> : <span className="cover-ph">📖</span>}
          <input ref={covInput} type="file" accept=".jpg,.jpeg,.png,.webp" hidden onChange={onCover} />
          <button className="ghost small-btn" onClick={() => covInput.current && covInput.current.click()}>обложка</button>
        </div>

        <div className="card home-main">
          <div className="stats">
            <span className="badge">{isStream ? 'поток (легаси)' : 'по главам'}</span>
            <span className={'badge ' + (book.translator === 'deepseek' ? 'ds' : 'local')}>
              перевод: {book.translator === 'deepseek' ? 'DeepSeek' : 'локальная модель'}
            </span>
            {!isStream && total > 0 && (
              <>
                <span>глав: <b>{total}</b></span>
                <span>переведено: <b>{translated}</b></span>
                <span>озвучено: <b>{ttsDone}</b></span>
              </>
            )}
            {book.source_filename && <span className="muted small">файл: {book.source_filename}</span>}
          </div>
          {!isStream && total > 0 && (
            <div className="progress"><div className="bar" style={{ width: Math.round(ttsDone / total * 100) + '%' }} /></div>
          )}

          {!isStream && total > 0 && (
            <div className="home-actions">
              <Link className="button primary big" to={`/book/${bookId}/read?lang=zh&ch=1`}>📖 Читать оригинал</Link>
              <Link className="button primary big" to={`/book/${bookId}/read?lang=ru&ch=1`}>📗 Читать перевод</Link>
              <Link className="button big" to={`/book/${bookId}/player`}>🎧 Слушать</Link>
            </div>
          )}
          {isStream && (
            <div className="home-actions">
              <Link className="button primary big" to={`/book/${bookId}/player`}>🎧 Слушать аудиокнигу</Link>
            </div>
          )}

          {canEdit && (
            <div className="actions">
              <button onClick={() => srcInput.current && srcInput.current.click()} disabled={busy || !!activeJobs.length}>
                {book.source_filename ? 'Заменить txt' : 'Загрузить txt-исходник'}</button>
              <input ref={srcInput} type="file" accept=".txt,text/plain" hidden onChange={onFile} />
              {isStream && book.source_filename && (
                <button onClick={doConvert} disabled={busy}>🔪 Нарезать на главы</button>
              )}
              {!isStream && book.source_filename && (
                <button onClick={doScan} disabled={busy || !!activeJobs.length}>⟳ Скан глав заново</button>
              )}
              {!isStream && total > 0 && (
                <>
                  <button className="primary" disabled={busy || !!activeJobs.length} onClick={() => doPipeline('translate')}>Перевести недостающие</button>
                  <button disabled={busy || !!activeJobs.length || translated === 0} onClick={() => doPipeline('tts')}>Озвучить переведённые</button>
                  <button disabled={busy || !!activeJobs.length || translated === 0} onClick={doQuality}>Проверить качество</button>
                </>
              )}
            </div>
          )}

          <div className="legend">
            {!isStream && total > 0 && (
              <Link className="button ghost" to={`/book/${bookId}/read?lang=zh&ch=1&diff=1`}>Сравнить оригинал с переводом (диф)</Link>
            )}
            <a className="button ghost" onClick={(e) => { e.preventDefault(); downloadFile(`/api/books/${bookId}/source.txt`, 'source.txt') }} href="#">⬇ Исходник txt</a>
            {book.has_legacy_ru ? (
              <a className="button ghost" onClick={(e) => { e.preventDefault(); downloadFile(`/api/books/${bookId}/legacy/translated.txt`, 'translated_legacy.txt') }} href="#">⬇ Полный перевод (легаси)</a>
            ) : (
              <a className="button ghost" onClick={(e) => { e.preventDefault(); downloadFile(`/api/books/${bookId}/translated.txt`, 'translated.txt') }} href="#">⬇ Перевод txt</a>
            )}
            {book.has_legacy_audio && (
              <a className="button ghost" onClick={(e) => { e.preventDefault(); downloadFile(`/api/books/${bookId}/download/audio.zip`, 'audio.zip') }} href="#">⬇ Аудио (легаси) zip</a>
            )}
          </div>

          {!isStream && total === 0 && (
            <p className="hint">Книга пуста. Загрузите txt-исходник (кнопка выше) — после этого появится кнопка нарезки на главы.</p>
          )}
          {isStream && book.source_filename && (
            <p className="hint">Книга импортирована как поток (без глав). Нажмите «Нарезать на главы» — оригинал будет разрезан поиском по заголовкам «第N章», прежние перевод и аудио останутся доступны как «легаси».</p>
          )}
        </div>
      </div>
    </main>
  )
}
