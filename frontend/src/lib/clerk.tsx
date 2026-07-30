// Clerk auth provider.
//
// Wraps the app so Clerk owns sign-in, session and refresh, and registers a
// token getter with the API client so every request carries a fresh token.
//
// The provider degrades rather than crashing when no publishable key is set:
// local development and CI can run the UI without a Clerk account, and the
// backend's CLERK_DEV_UNSAFE flag is the matching half of that. Anything other
// than a real key in production is a misconfiguration, and the banner says so
// loudly rather than letting it pass unnoticed.

import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import {
  ClerkProvider,
  SignedIn,
  SignedOut,
  SignIn,
  useAuth as useClerkAuth,
  useUser,
} from '@clerk/clerk-react'
import api, { setAuthTokenGetter } from '../api/client'
import type { User } from './types'

const PUBLISHABLE_KEY = import.meta.env.VITE_CLERK_PUBLISHABLE_KEY as string | undefined

interface AuthState {
  user: User | null
  isLoading: boolean
  isConfigured: boolean
  logout: () => void
}

const AuthContext = createContext<AuthState>({
  user: null,
  isLoading: true,
  isConfigured: false,
  logout: () => {},
})

export const useAuth = () => useContext(AuthContext)

/** Bridges Clerk's session into the API client and loads our own user record. */
function AuthBridge({ children }: { children: ReactNode }) {
  const { getToken, signOut, isLoaded: authLoaded } = useClerkAuth()
  const { user: clerkUser, isLoaded: userLoaded } = useUser()
  const [user, setUser] = useState<User | null>(null)
  const [isLoading, setIsLoading] = useState(true)

  // Register the token getter before anything can issue a request.
  useEffect(() => {
    setAuthTokenGetter(() => getToken())
    return () => setAuthTokenGetter(null)
  }, [getToken])

  useEffect(() => {
    if (!authLoaded || !userLoaded) return

    if (!clerkUser) {
      setUser(null)
      setIsLoading(false)
      return
    }

    // /auth/me is also the provisioning call: it creates the local User and
    // workspace on first sight, so it has to run before anything else.
    let cancelled = false
    api
      .get<User>('/auth/me')
      .then(({ data }) => {
        if (!cancelled) setUser(data)
      })
      .catch(() => {
        if (!cancelled) setUser(null)
      })
      .finally(() => {
        if (!cancelled) setIsLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [authLoaded, userLoaded, clerkUser])

  return (
    <AuthContext.Provider
      value={{
        user,
        isLoading,
        isConfigured: true,
        logout: () => signOut(),
      }}
    >
      {children}
    </AuthContext.Provider>
  )
}

export function AuthProvider({ children }: { children: ReactNode }) {
  if (!PUBLISHABLE_KEY) {
    return (
      <AuthContext.Provider
        value={{
          user: null,
          isLoading: false,
          isConfigured: false,
          logout: () => {},
        }}
      >
        <div
          style={{
            background: '#7f1d1d',
            color: '#fff',
            padding: '10px 16px',
            fontSize: 14,
          }}
        >
          VITE_CLERK_PUBLISHABLE_KEY is not set — authentication is disabled.
          This is only valid for local development.
        </div>
        {children}
      </AuthContext.Provider>
    )
  }

  return (
    <ClerkProvider publishableKey={PUBLISHABLE_KEY} afterSignOutUrl="/login">
      <AuthBridge>{children}</AuthBridge>
    </ClerkProvider>
  )
}

/** Gate that shows Clerk's sign-in to anyone who isn't authenticated. */
export function RequireAuth({ children }: { children: ReactNode }) {
  if (!PUBLISHABLE_KEY) return <>{children}</>

  return (
    <>
      <SignedIn>{children}</SignedIn>
      <SignedOut>
        <div
          style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            minHeight: '100vh',
          }}
        >
          <SignIn routing="hash" />
        </div>
      </SignedOut>
    </>
  )
}
