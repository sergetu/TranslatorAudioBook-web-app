import { useEffect, useState } from 'react'
import { useDispatch, useSelector } from 'react-redux'
import { useLocation, useNavigate } from 'react-router-dom'
import { login, register } from '../store'

export default function LoginPage() {
  const [mode, setMode] = useState('login')
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [name, setName] = useState('')
  const [error, setError] = useState('')
  const dispatch = useDispatch()
  const location = useLocation()
  const navigate = useNavigate()
  const user = useSelector((s) => s.auth.user)
  const busy = useSelector((s) => s.auth.status === 'pending') || false

  useEffect(() => {
    if (user) navigate('/', { replace: true })
  }, [user, navigate])

  async function submit(e) {
    e.preventDefault()
    setError('')
    const from = location.state?.from || '/'
    try {
      if (mode === 'login') await dispatch(login({ email, password })).unwrap()
      else await dispatch(register({ email, password, name })).unwrap()
      navigate(from, { replace: true })
    } catch (err) {
      setError(err.message || 'Ошибка')
    }
  }

  return (
    <div className="auth-wrap">
      <form className="card auth-card" onSubmit={submit}>
        <h1>📖 TranslatorAudioBook</h1>
        <p className="muted">Перевод и озвучка книг по главам</p>
        <div className="tabs">
          <button type="button" className={mode === 'login' ? 'active' : ''} onClick={() => setMode('login')}>Вход</button>
          <button type="button" className={mode === 'register' ? 'active' : ''} onClick={() => setMode('register')}>Регистрация</button>
        </div>
        {mode === 'register' && (
          <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Имя (необязательно)" />
        )}
        <input type="email" required value={email} onChange={(e) => setEmail(e.target.value)} placeholder="Email" />
        <input type="password" required minLength={6} value={password} onChange={(e) => setPassword(e.target.value)} placeholder="Пароль" />
        {error && <div className="error">{error}</div>}
        <button className="primary" disabled={busy}>
          {mode === 'login' ? 'Войти' : 'Создать аккаунт'}
        </button>
        {mode === 'register' && <p className="hint">Первый зарегистрированный пользователь становится администратором.</p>}
        {mode === 'login' && <p className="hint">Забыли пароль? Обратитесь к администратору — он сбросит его.</p>}
      </form>
    </div>
  )
}
