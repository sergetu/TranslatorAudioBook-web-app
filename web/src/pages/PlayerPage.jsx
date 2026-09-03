import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams, useSearchParams } from 'react-router-dom'
import { api, getToken } from '../api'

export default function PlayerPage() {
  const { id } = useParams()
  const bookId = Number(id)
  const [params, setParams] = useSearchParams()
  const reqCh = parseInt(params.get('ch') || '0', 10) || 0

  const [book, setBook] = useState(null)
  const [mode, setMode] = useState('chapters')     // chapters | legacy
  const [chapters, setChapters] = useState([])      // с аудио (tts done)
  const [curNum, setCurNum] = useState(null)
  const [parts, setParts] = useState([])            // url'ы частей главы
  const [part, setPart] = useState(0)
  const [tracks, setTracks] = useState([])          // legacy-треки
  const [trk, setTrk] = useState(0)
  const audioRef = useRef(null)
  const token = getToken()

  useEffect(() => {
    api.get(`/api/books/${bookId}`).then(async (b) => {
      setBook(b)
      if (b.has_legacy_audio) {
        try { const t = await api.get(`/api/books/${bookId}/legacy/tracks`); setTracks(t.tracks || []) } catch { /* */ }
      }
      if (b.mode === 'chaptered') {
        try {
          const d = await api.get(`/api/books/${bookId}/chapters`)
          const withAudio = (d.chapters || []).filter((c) => c.tts_status === 'done' || c.audio_parts > 0)
          setChapters(withAudio)
        } catch { /* */ }
      }
    }).catch(() => {})
  }, [bookId])

  const voiced = useMemo(() => {
    const map = {}
    chapters.forEach((c) => { map[c.num] = c })
    return map
  }, [chapters])

  // выбрать главу с аудио (запрошенную или первую подходящую)
  const pickChapter = useCallback(async (num) => {
    if (voiced[num]) { setCurNum(num); setPart(0); return }
    const list = chapters.map((c) => c.num)
    if (!list.length) { setCurNum(null); return }
    let target = list[0]
    if (num) {
      const ge = list.find((n) => n >= num)
      if (ge) target = ge
    }
    setCurNum(target); setPart(0)
  }, [chapters, voiced])

  useEffect(() => { pickChapter(reqCh || 0) }, [reqCh, chapters.length]) // eslint-disable-line

  // загрузка частей текущей главы
  useEffect(() => {
    if (!curNum) { setParts([]); return }
    api.get(`/api/books/${bookId}/chapters/${curNum}`).then((d) => {
      setParts(d.audio || [])
    }).catch(() => setParts([]))
  }, [bookId, curNum])

  // режим по умолчанию: если есть озвученные главы — «по главам» (особенно когда пришли ?ch=N);
  // legacy — только если главы с аудио отсутствуют, либо выбран явно
  const hasVoiced = chapters.length > 0
  useEffect(() => {
    if (hasVoiced && reqCh) { setMode('chapters'); return }
    if (book && !book.has_legacy_audio) { setMode('chapters'); return }
    if (book && book.has_legacy_audio && !hasVoiced) { setMode('legacy'); return }
  }, [hasVoiced, reqCh, book])
  const effectiveMode = mode === 'legacy' && !tracks.length ? 'chapters' : mode

  if (!book) return <main className="page"><p>Загрузка…</p></main>

  const audioSrc = effectiveMode === 'legacy'
    ? (tracks[trk] ? tracks[trk].url + '?token=' + token : '')
    : (parts[part] ? parts[part] + '?token=' + token : '')

  function onEnded() {
    if (effectiveMode === 'legacy') {
      if (trk + 1 < tracks.length) {
        setTrk(trk + 1)
        api.put(`/api/books/${bookId}/progress`, { chapter_num: trk + 2, position_sec: 0 }).catch(() => {})
      }
      return
    }
    // chapters: следующая часть или следующая озвученная глава
    if (part + 1 < parts.length) { setPart(part + 1); return }
    const list = chapters.map((c) => c.num)
    const i = list.indexOf(curNum)
    if (i >= 0 && i + 1 < list.length) { setCurNum(list[i + 1]); setPart(0) }
  }

  function savePos() {
    if (effectiveMode === 'chapters') {
      api.put(`/api/books/${bookId}/progress`, { chapter_num: curNum, position_sec: audioRef.current?.currentTime || 0 }).catch(() => {})
    } else {
      api.put(`/api/books/${bookId}/progress`, { chapter_num: trk + 1, position_sec: audioRef.current?.currentTime || 0 }).catch(() => {})
    }
  }

  const curTitle = effectiveMode === 'chapters'
    ? (voiced[curNum]?.title || `Глава ${curNum}`)
    : (tracks[trk]?.name || '')

  return (
    <main className="page player-page">
      <div className="page-head">
        <Link to={`/book/${bookId}`} className="back">← {book.title}</Link>
        <h2>🎧 Аудио</h2>
        <div className="spacer" />
        {book.has_legacy_audio && chapters.length > 0 && (
          <div className="seg">
            <button className={effectiveMode === 'chapters' ? 'active' : ''} onClick={() => setMode('chapters')}>По главам</button>
            <button className={effectiveMode === 'legacy' ? 'active' : ''} onClick={() => setMode('legacy')}>Легаси ({tracks.length})</button>
          </div>
        )}
      </div>

      {effectiveMode === 'chapters' && chapters.length === 0 && !tracks.length && (
        <div className="card"><p className="muted">Озвученных глав пока нет. Запустите озвучку со страницы книги.</p></div>
      )}

      {effectiveMode === 'chapters' && chapters.length > 0 && (
        <div className="player-layout">
          <aside className="card toc">
            <div className="toc-head">Озвученные главы ({chapters.length})</div>
            <div className="toc-list">
              {chapters.map((c) => (
                <button key={c.num} className={'toc-item' + (c.num === curNum ? ' active' : '')}
                  onClick={() => { setCurNum(c.num); setPart(0) }}>
                  <span className="toc-num">{c.num}</span>
                  <span className="toc-title">{c.title}</span>
                  <span className="toc-st ok">🎧</span>
                </button>
              ))}
            </div>
          </aside>
          <section className="card player">
            {parts.length > 0 ? (
              <>
                <div className="now"><strong>{curTitle}</strong>
                  <span>часть {part + 1} / {parts.length} · {curNum}/{book.chapters_total}</span></div>
                <audio ref={audioRef} key={audioSrc} controls autoPlay src={audioSrc}
                  onEnded={onEnded}
                  onTimeUpdate={() => { const t = (audioRef.current?.currentTime || 0); if (Math.floor(t) % 15 === 0) savePos() }}
                  onLoadedMetadata={() => {
                    if (part === 0) api.get(`/api/books/${bookId}/progress`).then((p) => {
                      if (p && p.chapter_num === curNum && p.position_sec) audioRef.current.currentTime = p.position_sec
                    }).catch(() => {})
                  }} />
                <div className="controls">
                  <button onClick={() => { const list = chapters.map((x) => x.num); const i = list.indexOf(curNum); if (i > 0) { setCurNum(list[i - 1]); setPart(0) } }}>⏮ глава</button>
                  <button disabled={part === 0} onClick={() => setPart(part - 1)}>← часть</button>
                  <button onClick={() => audioRef.current?.play()}>▶</button>
                  <button onClick={() => audioRef.current?.pause()}>⏸</button>
                  <button disabled={part + 1 >= parts.length} onClick={() => setPart(part + 1)}>часть →</button>
                </div>
                <div className="hint">Глава закончится — автоматически запустится следующая озвученная. Позиция сохраняется на аккаунт.</div>
              </>
            ) : <p className="muted">Загружаю аудио главы {curNum}…</p>}
          </section>
        </div>
      )}

      {effectiveMode === 'legacy' && (
        <div className="player-layout">
          <aside className="card toc">
            <div className="toc-head">Треки ({tracks.length})</div>
            <div className="toc-list">
              {tracks.map((t, i) => (
                <button key={t.name} className={'toc-item' + (i === trk ? ' active' : '')}
                  onClick={() => setTrk(i)}>
                  <span className="toc-num">{String(i + 1).padStart(3, '0')}</span>
                  <span className="toc-title">{t.name}</span>
                  <span className="muted small">{(t.size / 1e6).toFixed(1)} МБ</span>
                </button>
              ))}
            </div>
          </aside>
          <section className="card player">
            <div className="now"><strong>{curTitle}</strong><span>{trk + 1} / {tracks.length}</span></div>
            <audio ref={audioRef} key={audioSrc} controls autoPlay src={audioSrc}
              onEnded={onEnded}
              onTimeUpdate={() => { const t = (audioRef.current?.currentTime || 0); if (Math.floor(t) % 15 === 0) savePos() }} />
            <div className="controls">
              <button disabled={trk === 0} onClick={() => setTrk(trk - 1)}>⏮</button>
              <button onClick={() => audioRef.current?.play()}>▶</button>
              <button onClick={() => audioRef.current?.pause()}>⏸</button>
              <button disabled={trk + 1 >= tracks.length} onClick={() => setTrk(trk + 1)}>⏭</button>
            </div>
          </section>
        </div>
      )}
    </main>
  )
}
