import { useEffect, useRef, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { Link, useNavigate } from 'react-router-dom'
import { createBook, deleteBookThunk, loadBooks, uploadCover, uploadSource, scanBook, runPipeline, loadJobs, clearError } from '../store'
import { getToken } from '../api'

function fmtSize(n) {
  if (n == null) return ''
  if (n > 1e9) return (n / 1e9).toFixed(1) + ' ГБ'
  if (n > 1e6) return (n / 1e6).toFixed(1) + ' МБ'
  return (n / 1e3).toFixed(0) + ' КБ'
}

function BookCard({ book, onNeedRefresh }) {
  const dispatch = useDispatch()
  const navigate = useNavigate()
  const [menu, setMenu] = useState(false)
  const srcInput = useRef(null)
  const covInput = useRef(null)
  const token = getToken()
  const coverUrl = book.cover_path ? `/api/books/${book.id}/cover?token=${token}` : null
  const prog = book.mode === 'chaptered'
    ? (book.chapters_total ? Math.round((book.tts_done_chapters || 0) / book.chapters_total * 100) : 0)
    : null

  async function onFile(e, kind) {
    const file = e.target.files && e.target.files[0]
    if (!file) return
    if (kind === 'source') { await dispatch(uploadSource({ bookId: book.id, file })); onNeedRefresh() }
    else { await dispatch(uploadCover({ bookId: book.id, file })); onNeedRefresh() }
    e.target.value = ''
  }

  async function doScan() {
    await dispatch(scanBook(book.id)).unwrap()
    onNeedRefresh()
  }

  async function doPipeline(stage) {
    await dispatch(runPipeline({ bookId: book.id, stage })).unwrap()
    onNeedRefresh()
  }

  async function doDelete() {
    if (!confirm(`Удалить книгу «${book.title}» вместе с файлами?`)) return
    await dispatch(deleteBookThunk(book.id)).unwrap()
    onNeedRefresh()
  }

  return (
    <div className="card book-card">
      <div className="book-cover" onClick={() => navigate(`/book/${book.id}`)}>
        {coverUrl ? <img src={coverUrl} alt="" loading="lazy" /> : <span className="cover-ph">📖</span>}
      </div>
      <div className="book-info">
        <h3 onClick={() => navigate(`/book/${book.id}`)}>{book.title}</h3>
        {book.author && <div className="muted small">{book.author}</div>}
        <div className="meta">
          <span className={'badge ' + (book.translator === 'deepseek' ? 'ds' : 'local')}>
            {book.translator === 'deepseek' ? 'DeepSeek' : 'Локальная'}
          </span>
          <span className="badge">{book.mode === 'stream' ? 'поток (легаси)' : 'по главам'}</span>
          {book.chapters_total > 0 && <span className="muted small">{book.chapters_total} гл.</span>}
          {book.audio_size ? <span className="muted small">{fmtSize(book.audio_size)} аудио</span> : null}
        </div>
        {prog !== null && book.chapters_total > 0 && (
          <div className="progress"><div className="bar" style={{ width: prog + '%' }} /></div>
        )}
        <div className="actions">
          <button onClick={() => navigate(`/book/${book.id}`)}>Открыть</button>
          {book.mode === 'chaptered' && book.status !== 'empty' && (
            <button onClick={() => doPipeline('all')} title="Перевести и озвучить недостающее">▶ Обработать</button>
          )}
          <button onClick={() => srcInput.current && srcInput.current.click()}>Загрузить txt</button>
          <input ref={srcInput} type="file" accept=".txt,text/plain" hidden onChange={(e) => onFile(e, 'source')} />
          <button onClick={() => covInput.current && covInput.current.click()} title="Обложка">🖼</button>
          <input ref={covInput} type="file" accept=".jpg,.jpeg,.png,.webp" hidden onChange={(e) => onFile(e, 'cover')} />
          {book.mode === 'chaptered' && book.source_filename && (
            <button onClick={doScan} title="Пересканировать главы">⟳ Скан глав</button>
          )}
          <button className="danger-ghost" onClick={doDelete} title="Удалить">🗑</button>
        </div>
      </div>
    </div>
  )
}

export default function LibraryPage() {
  const dispatch = useDispatch()
  const books = useSelector((s) => s.books.items)
  const error = useSelector((s) => s.books.error)
  const user = useSelector((s) => s.auth.user)
  const [showNew, setShowNew] = useState(false)
  const [title, setTitle] = useState('')
  const [author, setAuthor] = useState('')
  const [translator, setTranslator] = useState('local')
  const refreshJobsTimer = useRef(null)

  async function refresh() {
    await dispatch(loadBooks()).unwrap()
    await dispatch(loadJobs()).unwrap()
  }
  useEffect(() => { refresh() }, [])
  useEffect(() => {
    refreshJobsTimer.current = setInterval(() => dispatch(loadJobs()), 4000)
    return () => clearInterval(refreshJobsTimer.current)
  }, [])

  async function submitNew(e) {
    e.preventDefault()
    if (!title.trim()) return
    await dispatch(createBook({ title, author, translator })).unwrap()
    setTitle(''); setAuthor(''); setShowNew(false)
    refresh()
  }

  return (
    <main className="page">
      <div className="page-head">
        <h2>Библиотека</h2>
        <button className="primary" onClick={() => setShowNew(!showNew)}>+ Новая книга</button>
      </div>
      {error && <div className="error" onClick={() => dispatch(clearError())}>{error}</div>}
      {showNew && (
        <form className="card row-form" onSubmit={submitNew}>
          <input required placeholder="Название книги" value={title} onChange={(e) => setTitle(e.target.value)} />
          <input placeholder="Автор" value={author} onChange={(e) => setAuthor(e.target.value)} />
          <select value={translator} onChange={(e) => setTranslator(e.target.value)}>
            <option value="local">Локальная (koboldcpp)</option>
            <option value="deepseek">DeepSeek API</option>
          </select>
          <button className="primary" type="submit">Создать</button>
        </form>
      )}
      <div className="book-grid">
        {books.map((b) => <BookCard key={b.id} book={b} onNeedRefresh={refresh} />)}
        {books.length === 0 && <p className="muted">Пока нет книг. Создайте новую и загрузите txt-исходник.</p>}
      </div>
      {user && user.role === 'admin' && (
        <div className="hint" style={{ marginTop: 16 }}>
          Админ: импорт готовой книги — в разделе <Link to="/admin">Админ</Link>.
        </div>
      )}
    </main>
  )
}
