import axios from 'axios'

const API_URL = import.meta.env.VITE_API_URL || '/api'

const api = axios.create({
  baseURL: API_URL,
  timeout: 30000,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Clerk owns the session token and its refresh. The provider registers a
// getter here rather than us reading localStorage: Clerk rotates tokens on a
// short cycle, so a cached copy goes stale, and asking for it per request is
// the only way to be sure what we attach is still valid.
//
// This also removes the refresh-on-401 dance the old locally-issued JWT
// needed — there is nothing left for us to refresh.
type TokenGetter = () => Promise<string | null>

let getToken: TokenGetter | null = null

export function setAuthTokenGetter(getter: TokenGetter | null): void {
  getToken = getter
}

api.interceptors.request.use(async (config) => {
  if (getToken) {
    try {
      const token = await getToken()
      if (token) {
        config.headers.Authorization = `Bearer ${token}`
      }
    } catch {
      // No token available — let the request go out unauthenticated and let
      // the response interceptor handle the 401.
    }
  }
  return config
})

api.interceptors.response.use(
  (response) => response,
  (error) => {
    // A 401 now means "not signed in", not "token needs refreshing" — Clerk
    // has already tried to refresh before handing us anything. Send the user
    // to sign in rather than retrying a request that cannot succeed.
    if (error.response?.status === 401) {
      const path = window.location.pathname
      if (path !== '/login' && path !== '/sign-in') {
        window.location.href = '/login'
      }
    }
    return Promise.reject(error)
  },
)

export default api
