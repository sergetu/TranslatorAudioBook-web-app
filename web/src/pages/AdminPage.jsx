import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import {
  loadSettings, saveSettings, saveDeepseekKey, loadUsers, createUserThunk,
  resetUserPassword, toggleUser,
} from '../store'
import { api } from '../api'

export default function AdminPage() {
  const dispatch = useDispatch()
  const { settings, deepseekSet, users, error } = useSelector((s) => s.admin)
  const [form, setForm] = useState({})
  const [prompt, setPrompt] = useState(null)   // локальная правка промпта; null = показать из настроек
  const [dk, setDk] = useState('')
  const [msg, setMsg] = useState('')
  const [newUser, setNewUser] = useState({ email: '', password: '', name: '', role: 'user' })
  const [resetPw, setResetPw] = useState({}) // userId -> новый пароль

  useEffect(() => { dispatch(loadSettings()); dispatch(loadUsers()) }, [dispatch])

  async function save() {
    setMsg('')
    await dispatch(saveSettings(form)).unwrap()
    setMsg('Сохранено')
  }
  async function savePrompt() {
    setMsg('')
    if (prompt === null) return
    await dispatch(saveSettings({ deepseek_system_prompt: prompt })).unwrap()
    setMsg('Системный промпт сохранён')
    dispatch(loadSettings())
  }
  async function saveKey() {
    setMsg('')
    if (!dk.trim()) return
    await dispatch(saveDeepseekKey(dk)).unwrap()
    setDk(''); setMsg('Ключ DeepSeek сохранён (в секретном файле)')
    dispatch(loadSettings())
  }
  async function createUser(e) {
    e.preventDefault(); setMsg('')
    try {
      await dispatch(createUserThunk(newUser)).unwrap()
      setNewUser({ email: '', password: '', name: '', role: 'user' })
      setMsg('Пользователь создан')
    } catch (err) { setMsg(err.message) }
  }
  async function doReset(uid) {
    const pw = resetPw[uid]; if (!pw || pw.length < 6) return
    await dispatch(resetUserPassword({ userId: uid, password: pw })).unwrap()
    setResetPw((m) => ({ ...m, [uid]: '' })); setMsg('Пароль сброшен')
  }
  async function doToggle(uid) {
    await dispatch(toggleUser(uid)).unwrap()
    dispatch(loadUsers())
  }
  async function doImport(e) {
    e.preventDefault(); setMsg('')
    const fd = new FormData(e.target)
    try {
      await api.post('/api/admin/import-stream', fd)
      setMsg('Книга импортирована как stream')
      e.target.reset()
    } catch (err) { setMsg(err.message) }
  }

  const keys = settings ? Object.keys(settings).filter((k) => k !== 'deepseek_system_prompt') : []
  const labelMap = {
    allow_registration: 'Открытая регистрация', tts_voice: 'Голос TTS',
    tts_rate: 'Скорость TTS', tts_volume: 'Громкость TTS',
    kobold_base_url: 'koboldcpp URL', kobold_model: 'Модель (koboldcpp)',
    kobold_context_tokens: 'Контекст (токены)', kobold_chat_format: 'Формат kobold (raw/chat)',
    deepseek_model: 'DeepSeek модель', deepseek_base_url: 'DeepSeek URL',
    llm_max_concurrency: 'Параллельность LLM', tts_max_concurrency: 'Параллельность TTS',
    max_upload_mb: 'Макс. размер файла (МБ)', deepseek_max_concurrency: 'DeepSeek параллельность',
    llm_max_retries: 'Ретраи LLM', llm_retry_base_seconds: 'Пауза ретрая (с)',
  }

  return (
    <main className="page">
      <h2>Администрирование</h2>
      {msg && <div className="ok">{msg}</div>}
      {error && <div className="error">{error}</div>}

      <div className="admin-grid">
        <section className="card">
          <h3>Настройки</h3>
          {keys.map((k) => (
            <label key={k} className="field">
              <span>{labelMap[k] || k}</span>
              <input value={form[k] !== undefined ? form[k] : (settings[k] ?? '')}
                onChange={(e) => setForm((f) => ({ ...f, [k]: e.target.value }))} />
            </label>
          ))}
          <button className="primary" onClick={save}>Сохранить настройки</button>
        </section>

        <section className="card">
          <h3>DeepSeek API</h3>
          <div className="field">
            <span>Системный промпт (перевод)</span>
            <textarea rows={8}
              value={prompt !== null ? prompt : (settings?.deepseek_system_prompt ?? '')}
              onChange={(e) => setPrompt(e.target.value)}
              placeholder="Инструкция для модели перевода…" />
            <p className="hint">Используется обоими движками (local chat и DeepSeek). Пусто — вернётся промпт по умолчанию.</p>
            <button className="primary" onClick={savePrompt} disabled={!prompt || prompt === (settings?.deepseek_system_prompt ?? '')}>
              Сохранить промпт
            </button>
          </div>
          <hr className="sep" />
          <p className="hint">Ключ хранится в секретном файле на сервере и не показывается.
            {deepseekSet ? ' Ключ задан.' : ' Ключ не задан.'}</p>
          <input type="password" placeholder="Введите новый ключ" value={dk}
            onChange={(e) => setDk(e.target.value)} autoComplete="off" />
          <button className="primary" onClick={saveKey} disabled={!dk.trim()}>Сохранить ключ</button>
        </section>

        <section className="card">
          <h3>Пользователи</h3>
          <table className="plain">
            <thead><tr><th>Email</th><th>Имя</th><th>Роль</th><th>Активен</th><th>Действия</th></tr></thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id}>
                  <td>{u.email}</td><td>{u.name}</td><td>{u.role}</td>
                  <td>{u.is_active ? 'да' : 'нет'}</td>
                  <td>
                    <input type="password" placeholder="новый пароль" style={{ width: 110 }}
                      value={resetPw[u.id] || ''} onChange={(e) => setResetPw((m) => ({ ...m, [u.id]: e.target.value }))} />
                    <button disabled={(resetPw[u.id] || '').length < 6}
                      onClick={() => doReset(u.id)}>Сброс</button>
                    <button onClick={() => doToggle(u.id)}>{u.is_active ? 'Выкл' : 'Вкл'}</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <form className="row-form" onSubmit={createUser}>
            <input required placeholder="email" type="email" value={newUser.email}
              onChange={(e) => setNewUser((u) => ({ ...u, email: e.target.value }))} />
            <input required placeholder="пароль" type="password" minLength={6} value={newUser.password}
              onChange={(e) => setNewUser((u) => ({ ...u, password: e.target.value }))} />
            <input placeholder="имя" value={newUser.name}
              onChange={(e) => setNewUser((u) => ({ ...u, name: e.target.value }))} />
            <select value={newUser.role} onChange={(e) => setNewUser((u) => ({ ...u, role: e.target.value }))}>
              <option value="user">user</option><option value="admin">admin</option>
            </select>
            <button className="primary">Создать</button>
          </form>
        </section>

        <section className="card">
          <h3>Импорт готовой книги (stream)</h3>
          <form className="col-form" onSubmit={doImport}>
            <label className="field"><span>Название</span><input name="title" required placeholder="Название книги" /></label>
            <label className="field"><span>Email владельца</span><input name="owner_email" required type="email" placeholder="user@example.com" /></label>
            <label className="field"><span>Путь к исходнику (.txt)</span><input name="source" placeholder="D:\\…\\source.txt" /></label>
            <label className="field"><span>Путь к готовому переводу</span><input name="translated" placeholder="D:\\…\\translated.txt" /></label>
            <label className="field"><span>Каталог mp3 (не копируется)</span><input name="audio_dir" placeholder="D:\\…\\audio_edge_svetlana" /></label>
            <button className="primary" type="submit">Импортировать</button>
          </form>
        </section>
      </div>
    </main>
  )
}
