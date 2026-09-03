import { configureStore, createSlice, createAsyncThunk } from '@reduxjs/toolkit'
import { api, setToken, getToken } from './api'

// ---------------- auth ----------------
export const login = createAsyncThunk('auth/login', async ({ email, password }) => {
  const form = new FormData()
  form.append('email', email); form.append('password', password)
  const data = await api.post('/api/auth/login', form)
  setToken(data.token)
  return data.user
})
export const register = createAsyncThunk('auth/register', async ({ email, password, name }) => {
  const form = new FormData()
  form.append('email', email); form.append('password', password); form.append('name', name || '')
  const data = await api.post('/api/auth/register', form)
  setToken(data.token)
  return data.user
})
export const fetchMe = createAsyncThunk('auth/me', async () => {
  if (!getToken()) throw new Error('no token')
  return api.get('/api/auth/me')
})

const authSlice = createSlice({
  name: 'auth',
  initialState: { user: null, status: 'idle', error: null },
  reducers: {
    logout(state) { state.user = null; state.status = 'idle'; setToken(null) },
  },
  extraReducers: (b) => {
    b.addCase(login.fulfilled, (s, a) => { s.user = a.payload; s.status = 'ok' })
    b.addCase(register.fulfilled, (s, a) => { s.user = a.payload; s.status = 'ok' })
    b.addCase(fetchMe.fulfilled, (s, a) => { s.user = a.payload; s.status = 'ok' })
    b.addCase(fetchMe.rejected, (s) => { s.user = null; s.status = 'idle' })
  },
})
export const { logout } = authSlice.actions

// ---------------- books ----------------
export const loadBooks = createAsyncThunk('books/list', () => api.get('/api/books'))
export const createBook = createAsyncThunk('books/create', (payload) => {
  const form = new FormData()
  form.append('title', payload.title); form.append('author', payload.author || '')
  form.append('translator', payload.translator || 'local')
  return api.post('/api/books', form)
})
export const uploadSource = createAsyncThunk('books/source', ({ bookId, file }) =>
  api.upload(`/api/books/${bookId}/source`, file))
export const uploadCover = createAsyncThunk('books/cover', ({ bookId, file }) =>
  api.upload(`/api/books/${bookId}/cover`, file))
export const scanBook = createAsyncThunk('books/scan', (bookId) =>
  api.post(`/api/books/${bookId}/scan`, new FormData()))
export const runPipeline = createAsyncThunk('books/pipeline', ({ bookId, stage, chapters }) =>
  api.postJson(`/api/books/${bookId}/pipeline`, { stage, chapters: chapters || null }))
export const qualityCheck = createAsyncThunk('books/quality', (bookId) =>
  api.post(`/api/books/${bookId}/quality-check`, new FormData()))
export const deleteBookThunk = createAsyncThunk('books/delete', (bookId) =>
  api.del(`/api/books/${bookId}`))
export const patchBook = createAsyncThunk('books/patch', ({ bookId, payload }) =>
  api.put(`/api/books/${bookId}`, payload))
export const retryChapter = createAsyncThunk('chapters/retry', (chapterId) =>
  api.post(`/api/chapters/${chapterId}/retry`, new FormData()))
export const regenTtsChapter = createAsyncThunk('chapters/regen-tts', (chapterId) =>
  api.post(`/api/chapters/${chapterId}/regen-tts`, new FormData()))
export const loadJobs = createAsyncThunk('jobs/list', (bookId) =>
  api.get(bookId ? `/api/jobs?book_id=${bookId}` : '/api/jobs'))
export const cancelJob = createAsyncThunk('jobs/cancel', (jobId) =>
  api.post(`/api/jobs/${jobId}/cancel`, new FormData()))

const booksSlice = createSlice({
  name: 'books',
  initialState: { items: [], jobs: [], error: null, busy: false },
  reducers: {
    clearError(s) { s.error = null },
  },
  extraReducers: (b) => {
    b.addCase(loadBooks.fulfilled, (s, a) => { s.items = a.payload })
    b.addCase(createBook.fulfilled, (s, a) => { s.items = [a.payload, ...s.items] })
    b.addCase(uploadSource.fulfilled, (s, a) => {
      s.items = s.items.map((x) => (x.id === a.payload.id ? a.payload : x))
    })
    b.addCase(scanBook.fulfilled, (s, a) => {
      s.error = a.payload.ok ? null : 'scan failed'
    })
    b.addCase(deleteBookThunk.fulfilled, (s, a) => {
      s.items = s.items.filter((x) => x.id !== a.meta.arg)
    })
    b.addCase(loadJobs.fulfilled, (s, a) => { s.jobs = a.payload })
    b.addCase(runPipeline.rejected, (s, a) => { s.error = a.error?.message || 'pipeline error' })
    b.addCase(qualityCheck.rejected, (s, a) => { s.error = a.error?.message || 'quality error' })
    b.addCase(loadBooks.rejected, (s, a) => { s.error = a.error?.message || 'load error' })
    b.addCase(patchBook.fulfilled, (s, a) => {
      s.items = s.items.map((x) => (x.id === a.payload.id ? a.payload : x))
    })
  },
})
export const { clearError } = booksSlice.actions

// ---------------- settings / admin ----------------
export const loadSettings = createAsyncThunk('admin/settings', () => api.get('/api/admin/settings'))
export const saveSettings = createAsyncThunk('admin/settings-save', (payload) =>
  api.put('/api/admin/settings', payload))
export const saveDeepseekKey = createAsyncThunk('admin/deepseek-key', (key) => {
  const form = new FormData(); form.append('key', key)
  return api.post('/api/admin/settings/deepseek-key', form)
})
export const loadUsers = createAsyncThunk('admin/users', () => api.get('/api/admin/users'))
export const createUserThunk = createAsyncThunk('admin/users-create', (u) => {
  const form = new FormData()
  form.append('email', u.email); form.append('password', u.password)
  form.append('name', u.name || ''); form.append('role', u.role || 'user')
  return api.post('/api/admin/users', form)
})
export const resetUserPassword = createAsyncThunk('admin/users-reset', ({ userId, password }) => {
  const form = new FormData(); form.append('new_password', password)
  return api.post(`/api/admin/users/${userId}/reset-password`, form)
})
export const toggleUser = createAsyncThunk('admin/users-toggle', (userId) =>
  api.post(`/api/admin/users/${userId}/toggle`, new FormData()))

const adminSlice = createSlice({
  name: 'admin',
  initialState: { settings: null, deepseekSet: false, users: [], error: null },
  reducers: { clearAdminError(s) { s.error = null } },
  extraReducers: (b) => {
    b.addCase(loadSettings.fulfilled, (s, a) => {
      s.settings = a.payload.settings; s.deepseekSet = a.payload.deepseek_key_set
    })
    b.addCase(saveDeepseekKey.fulfilled, (s, a) => { s.deepseekSet = a.payload.deepseek_key_set })
    b.addCase(loadUsers.fulfilled, (s, a) => { s.users = a.payload })
    b.addCase(createUserThunk.fulfilled, (s, a) => { s.users = [...s.users, a.payload] })
    b.addCase(saveSettings.rejected, (s, a) => { s.error = a.error?.message || 'settings error' })
    b.addCase(loadSettings.rejected, (s, a) => { s.error = a.error?.message || 'settings error' })
  },
})
export const { clearAdminError } = adminSlice.actions

export const store = configureStore({
  reducer: {
    auth: authSlice.reducer,
    books: booksSlice.reducer,
    admin: adminSlice.reducer,
  },
})
