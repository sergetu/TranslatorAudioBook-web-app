import { useCallback, useEffect, useRef, useState } from 'react'
import { Link, useNavigate, useParams, useSearchParams } from 'react-router-dom'
import { api, downloadFile } from '../api'

// Пара абзацев для диф-режима; при расхождении числа абзацев — просто две колонки.
function pairParagraphs(zh, ru) {
  const z = zh.split(/\n+/).map((s) => s.trim()).filter(Boolean)
  const r = ru.split(/\n+/).map((s) => s.trim()).filter(Boolean)
  if (z.length === r.length && z.length > 0) {
    return z.map((line, i) => ({ zh: line, ru: r[i] }))
  }
  return null // расхождение -> рендерим колонки отдельно
}

export default function ReaderPage() {
  const { id } = useParams()
  const bookId = Number(id)
  const navigate = useNavigate()
  const [params, setParams] = useSearchParams()
  const lang = params.get('lang') === 'ru' ? 'ru' : 'zh'
  const diff = params.get('diff') === '1'
  const curNum = Math.max(1, parseInt(params.get('ch') || '1', 10) || 1)

  const [book, setBook] = useState(null)
  const [chapters, setChapters] = useState([])
  const [content, setContent] = useState(null)
  const [editOpen, setEditOpen] = useState(false)
  const [editText, setEditText] = useState('')
  const [revOpen, setRevOpen] = useState(false)
  const [revText, setRevText] = useState('')
  const [uiMsg, setUiMsg] = useState('')
  const [busy, setBusy] = useState(false)
  const tocRef = useRef(null)
  const curNumRef = useRef(curNum); curNumRef.current = curNum
  const pollRef = useRef(null)
  // при уходе со страницы гасим все активные поллинги
  useEffect(() => () => { if (pollRef.current) clearInterval(pollRef.current) }, [])

  useEffect(() => { api.get(`/api/books/${bookId}`).then(setBook).catch(() => {}) }, [bookId])
  useEffect(() => {
    api.get(`/api/books/${bookId}/chapters`).then((d) => {
      setChapters(d.mode === 'chaptered' ? d.chapters : [])
    }).catch(() => {})
  }, [bookId])

  const loadChapter = useCallback(async (num) => {
    try { setContent(await api.get(`/api/books/${bookId}/chapters/${num}`)) }
    catch { setContent(null) }
  }, [bookId])

  // Сохранение отредактированного перевода
  async function saveTranslation() {
    const ch = chapters.find((x) => x.num === curNum)
    if (!ch || !editText.trim()) return
    setBusy(true); setUiMsg('')
    try {
      await api.put(`/api/chapters/${ch.id}/translation`, { text: editText })
      setUiMsg('Перевод сохранён. Аудио старой версии сброшено — можно озвучить заново.')
      setEditOpen(false)
      await loadChapter(curNum)
    } catch (e) { setUiMsg('Ошибка: ' + (e.message || e)) }
    finally { setBusy(false) }
  }

  // Перегенерация перевода с учётом замечания (revise)
  async function submitRevise() {
    const ch = chapters.find((x) => x.num === curNum)
    if (!ch || !revText.trim()) return
    setBusy(true); setUiMsg('')
    try {
      await api.postJson(`/api/chapters/${ch.id}/revise`, { feedback: revText })
      setRevOpen(false); setRevText('')
      setUiMsg('Поставлено в очередь: пере-перевод с учётом вашего замечания.')
      const ok = await pollStatus(curNum,
        (d) => d.status === 'translated', (d) => d.status === 'error',
        (d) => 'пере-перевод: ' + (d.error || 'не удался'))
      if (ok) setUiMsg('Перевод обновлён по вашему замечанию.')
      await loadChapter(curNum)
    } catch (e) { setUiMsg('Ошибка: ' + (e.message || e)) }
    finally { setBusy(false) }
  }

  const openEdit = () => { setEditText(content?.ru || ''); setEditOpen(true); setRevOpen(false) }
  const openRev = () => { setRevText(''); setRevOpen(true); setEditOpen(false) }

  // Поллинг статуса главы до нужного условия (возвращает true при успехе).
  // num фиксируется: при переключении главы слежение останавливается (задача на сервере продолжает идти).
  function pollStatus(num, until, fail, label) {
    return new Promise((resolve) => {
      if (pollRef.current) clearInterval(pollRef.current)
      const t0 = Date.now()
      const timer = setInterval(async () => {
        if (curNumRef.current !== num) { clearInterval(timer); resolve(false); return }
        try {
          const d = await api.get(`/api/books/${bookId}/chapters/${num}`)
          if (curNumRef.current !== num) { clearInterval(timer); resolve(false); return }
          setContent(d)
          if (until(d)) { clearInterval(timer); resolve(true); return }
          if (fail(d)) { clearInterval(timer); setUiMsg(`Ошибка: ${label(d)}`); resolve(false); return }
          if (Date.now() - t0 > 60 * 60 * 1000) { clearInterval(timer); setUiMsg('Превышено время ожидания.'); resolve(false) }
        } catch { clearInterval(timer); resolve(false) }
      }, 4000)
      pollRef.current = timer
    })
  }

  // Перевод конкретной главы (если ещё не переведена)
  async function translateChapter() {
    const ch = chapters.find((x) => x.num === curNum)
    if (!ch || content?.status === 'translated') return
    setBusy(true); setUiMsg('')
    try {
      await api.postJson(`/api/books/${bookId}/pipeline`, { stage: 'translate', chapters: [curNum] })
      setUiMsg('Перевод главы поставлен в очередь…')
      const ok = await pollStatus(curNum,
        (d) => d.status === 'translated', (d) => d.status === 'error',
        (d) => d.error || 'перевод не удался')
      if (ok) setUiMsg('Глава переведена. Теперь её можно озвучить.')
    } catch (e) { setUiMsg('Ошибка: ' + (e.message || e)) }
    finally { setBusy(false) }
  }

  // Озвучка переведённой главы
  async function voiceChapter() {
    const ch = chapters.find((x) => x.num === curNum)
    if (!ch || content?.status !== 'translated') return
    setBusy(true); setUiMsg('')
    try {
      await api.postJson(`/api/chapters/${ch.id}/regen-tts`, {})
      setUiMsg('Озвучка главы поставлена в очередь…')
      const ok = await pollStatus(curNum,
        (d) => d.tts_status === 'done', (d) => d.tts_status === 'error',
        (d) => d.tts_error || 'озвучка не удалась')
      if (ok) setUiMsg('Глава озвучена — можно слушать.')
    } catch (e) { setUiMsg('Ошибка: ' + (e.message || e)) }
    finally { setBusy(false) }
  }
  useEffect(() => { loadChapter(curNum) }, [curNum, loadChapter])

  const go = useCallback((num, keepLang, keepDiff) => {
    const p = new URLSearchParams(params)
    p.set('ch', String(num))
    if (keepLang !== undefined) { keepLang ? p.set('lang', keepLang) : p.delete('lang') }
    if (keepDiff !== undefined) { keepDiff ? p.set('diff', '1') : p.delete('diff') }
    setParams(p, { replace: true })
  }, [params, setParams])

  const idx = chapters.findIndex((c) => c.num === curNum)
  const pairs = content ? pairParagraphs(content.zh, content.ru) : null

  useEffect(() => {
    if (tocRef.current && chapters.length) {
      const el = tocRef.current.querySelector(`[data-num="${curNum}"]`)
      el && el.scrollIntoView({ block: 'center', behavior: 'smooth' })
    }
  }, [curNum, chapters])

  if (!book) return <main className="page"><p>Загрузка…</p></main>
  const isChaptered = book.mode === 'chaptered'
  if (!isChaptered) {
    return (
      <main className="page">
        <div className="page-head"><Link to={`/book/${bookId}`} className="back">← К книге</Link><h2>{book.title}</h2></div>
        <div className="card"><p className="muted">У книги нет разбивки на главы. Вернитесь к книге и нажмите «Нарезать на главы».</p></div>
      </main>
    )
  }

  const curTitle = chapters[idx]?.title || `Глава ${curNum}`
  const hasAudio = content?.audio?.length > 0
  const isTranslated = content?.status === 'translated'

  return (
    <main className="page reader-page">
      <div className="reader-topbar">
        <div className="reader-head">
          <Link to={`/book/${bookId}`} className="back">← {book.title}</Link>
          <div className="seg">
            <button className={(lang === 'zh' && !diff) ? 'active' : ''} onClick={() => go(curNum, 'zh', false)}>Оригинал</button>
            <button className={(lang === 'ru' && !diff) ? 'active' : ''} onClick={() => go(curNum, 'ru', false)}>Перевод</button>
            <button className={(lang === 'zh' && diff) ? 'active diff' : 'diff'} title="Оригинал слева, перевод справа по абзацам"
              onClick={() => go(curNum, 'zh', !diff)}>⇄ Диф</button>
          </div>
        </div>
        <div className="reader-nav">
          <button disabled={idx <= 0} onClick={() => go(chapters[idx - 1].num, lang, diff)}>← Пред.</button>
          <button disabled={idx < 0 || idx >= chapters.length - 1} onClick={() => go(chapters[idx + 1].num, lang, diff)}>След. →</button>
        </div>
      </div>

      <div className="reader-layout">
        <aside className="card toc" ref={tocRef}>
          <div className="toc-head">Главы <span className="muted small">({chapters.length})</span></div>
          <div className="toc-list">
            {chapters.map((c) => {
              const st = c.status === 'translated' ? 'ok' : (c.status === 'error' ? 'err' : '')
              const ts = c.tts_status === 'done' ? '🎧' : (c.tts_status === 'error' ? '⚠' : '')
              return (
                <button key={c.num} data-num={c.num} className={'toc-item' + (c.num === curNum ? ' active' : '')}
                  onClick={() => go(c.num, lang, diff)}>
                  <span className="toc-num">{c.num}</span>
                  <span className="toc-title">{c.title}</span>
                  <span className={'toc-st ' + st}>{ts}</span>
                </button>
              )
            })}
          </div>
        </aside>

        <section className="card reader-main">
          <div className="chapter-head">
            <h3>{curTitle}</h3>
            <div className="seg">
              {content?.status === 'queued' && !isTranslated && (
                <span className="muted small">⏳ перевод в очереди…</span>
              )}
              {!isTranslated && content?.status !== 'queued' && (
                <button className="primary white" onClick={translateChapter} disabled={busy}>⏩ Перевести главу</button>
              )}
              {!isTranslated && (
                <button className="ghost" onClick={voiceChapter} disabled={busy || content?.status !== 'translated'}
                  title={content?.status !== 'translated' ? 'Сначала переведите главу' : ''}>🎙 Озвучить главу</button>
              )}
              {isTranslated && content?.tts_status === 'queued' && (
                <span className="muted small">⏳ озвучивается…</span>
              )}
              {isTranslated && content?.tts_status !== 'queued' && content?.tts_status !== 'error' && (
                <button className="primary" onClick={voiceChapter} disabled={busy}>
                  {hasAudio ? '🎙 Переозвучить главу' : '🎙 Озвучить главу'}
                </button>
              )}
              {isTranslated && hasAudio && (
                <button className="ghost" onClick={() => navigate(`/book/${bookId}/player?ch=${curNum}`)}>🎧 Слушать главу</button>
              )}
              {isTranslated && content?.tts_status === 'error' && (
                <button className="ghost" onClick={voiceChapter} disabled={busy}>↻ Повторить озвучку</button>
              )}
              {isTranslated && (
                <button className="ghost" onClick={() => downloadFile(`/api/books/${bookId}/download/chapter/${curNum}.txt?lang=ru`, `${String(curNum).padStart(3, '0')}_ru.txt`)}>⬇ txt</button>
              )}
              {hasAudio && (
                <button className="ghost" onClick={() => downloadFile(`/api/books/${bookId}/download/chapter/${curNum}.mp3`, `${String(curNum).padStart(3, '0')}.mp3`)}>⬇ mp3</button>
              )}
            </div>
          </div>

          {content?.status === 'queued' && (
            <p className="hint">Пере-перевод в очереди… Текущий перевод виден до замены.</p>
          )}
          {uiMsg && <p className="hint status-msg">{uiMsg}</p>}

          {isTranslated && (
            <div className="trans-tools">
              <button className="ghost" onClick={openEdit} disabled={busy}>✎ Изменить перевод</button>
              <button className="ghost" onClick={openRev} disabled={busy}>↻ Перегенерировать с замечанием</button>
              {editOpen && (
                <div className="edit-form">
                  <textarea rows={10} value={editText} onChange={(e) => setEditText(e.target.value)}
                    placeholder="Отредактируйте перевод этой главы…" />
                  <div className="row-form">
                    <button className="primary" onClick={saveTranslation} disabled={busy || !editText.trim()}>Сохранить перевод</button>
                    <button onClick={() => setEditOpen(false)} disabled={busy}>Отмена</button>
                  </div>
                </div>
              )}
              {revOpen && (
                <div className="edit-form">
                  <textarea rows={4} value={revText} onChange={(e) => setRevText(e.target.value)}
                    placeholder="Что поправить? Например: «Имена героев переводить как в оригинале», «Слишком вольно, ближе к тексту», «Сократи повторы»…" />
                  <div className="row-form">
                    <button className="primary" onClick={submitRevise} disabled={busy || !revText.trim()}>Перевести заново с учётом замечания</button>
                    <button onClick={() => setRevOpen(false)} disabled={busy}>Отмена</button>
                  </div>
                </div>
              )}
            </div>
          )}

          {lang === 'zh' && diff && content && (
            pairs ? (
              <>
                <div className="diff-grid">
                  <div className="col-head">Оригинал</div><div className="col-head">Перевод</div>
                  {pairs.flatMap((p, i) => [<div key={'z' + i} className="diff-cell diff-zh">{p.zh}</div>,
                                             <div key={'r' + i} className="diff-cell diff-ru">{p.ru || ''}</div>])}
                </div>
                {content.ru === '' && <p className="hint">Перевод этой главы ещё не готов — колонка появится после перевода.</p>}
              </>
            ) : (
              <div className="diff-grid">
                <div className="diff-col">
                  <div className="col-head">Оригинал</div>
                  {content.zh.split(/\n+/).filter((s) => s.trim()).map((s, i) => <p key={i}>{s}</p>)}
                </div>
                <div className="diff-col">
                  <div className="col-head">Перевод {!isTranslated && <span className="muted small">(нет)</span>}</div>
                  {isTranslated ? content.ru.split(/\n+/).filter((s) => s.trim()).map((s, i) => <p key={i}>{s}</p>)
                    : <p className="muted">Перевод этой главы ещё не готов — диф появится после перевода.</p>}
                </div>
              </div>
            )
          )}

          {lang === 'ru' && content && (
            isTranslated ? (
              <div className="reader-text">{content.ru}</div>
            ) : (
              <div className="reader-empty">
                <p className="muted">Перевод главы ещё не готов. Вернитесь к книге и запустите перевод.</p>
              </div>
            )
          )}

          {lang === 'zh' && !diff && content && (
            <div className="reader-text zh-text">{content.zh}</div>
          )}

          <div className="reader-footer">
            <button disabled={idx <= 0} onClick={() => go(chapters[idx - 1].num, lang, diff)}>← Глава {curNum - 1}</button>
            <span className="muted small">{curNum} / {chapters.length}</span>
            <button disabled={idx < 0 || idx >= chapters.length - 1} onClick={() => go(chapters[idx + 1].num, lang, diff)}>Глава {curNum + 1} →</button>
          </div>
        </section>
      </div>
    </main>
  )
}
