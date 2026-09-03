import { useEffect, useState } from 'react'
import { useSelector, useDispatch } from 'react-redux'
import { Routes, Route, Navigate, useLocation } from 'react-router-dom'
import { fetchMe, logout } from './store'
import LoginPage from './pages/LoginPage.jsx'
import LibraryPage from './pages/LibraryPage.jsx'
import BookPage from './pages/BookPage.jsx'
import ReaderPage from './pages/ReaderPage.jsx'
import PlayerPage from './pages/PlayerPage.jsx'
import AdminPage from './pages/AdminPage.jsx'
import { getToken } from './api'

function Shell({ children }) {
  const user = useSelector((s) => s.auth.user)
  const dispatch = useDispatch()
  const location = useLocation()
  useEffect(() => {
    const onUnauth = () => dispatch(logout())
    window.addEventListener('tab-unauthorized', onUnauth)
    return () => window.removeEventListener('tab-unauthorized', onUnauth)
  }, [dispatch])
  if (!user) return <Navigate to="/login" state={{ from: location.pathname + location.search }} replace />
  return (
    <div className="shell">
      <header className="topbar">
        <div className="brand" onClick={() => { window.location.href = '/' }}>📖 TranslatorAudioBook</div>
        <nav>
          <a href="/" onClick={(e) => { e.preventDefault(); window.location.href = '/' }}>Библиотека</a>
          {user.role === 'admin' && (
            <a href="/admin" onClick={(e) => { e.preventDefault(); window.location.href = '/admin' }}>Админ</a>
          )}
        </nav>
        <div className="user">
          <span>{user.email}</span>
          <button className="ghost" onClick={() => dispatch(logout())}>Выйти</button>
        </div>
      </header>
      {children}
    </div>
  )
}

export default function App() {
  const dispatch = useDispatch()
  const user = useSelector((s) => s.auth.user)
  const [booting, setBooting] = useState(!!getToken() && !user)

  useEffect(() => {
    if (!getToken()) { setBooting(false); return }
    if (!user) {
      dispatch(fetchMe()).finally(() => setBooting(false))
    } else {
      setBooting(false)
    }
  }, [user, dispatch])

  if (booting && !user) {
    return <div className="page"><p className="muted">Загрузка…</p></div>
  }

  return (
    <Routes>
      <Route path="/login" element={<LoginPage />} />
      <Route path="/" element={<Shell><LibraryPage /></Shell>} />
      <Route path="/book/:id" element={<Shell><BookPage /></Shell>} />
      <Route path="/book/:id/read" element={<Shell><ReaderPage /></Shell>} />
      <Route path="/book/:id/player" element={<Shell><PlayerPage /></Shell>} />
      <Route path="/admin" element={<Shell><AdminPage /></Shell>} />
      <Route path="*" element={<Navigate to="/" replace />} />
    </Routes>
  )
}
