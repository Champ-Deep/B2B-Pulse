import { createContext, useContext } from 'react'
import type { User } from './types'

interface AuthContextType {
  user: User | null
  isLoading: boolean
  loginWithLinkedIn: (inviteCode?: string) => Promise<void>
  logout: () => void
}

export const AuthContext = createContext<AuthContextType>({
  user: null,
  isLoading: true,
  loginWithLinkedIn: async () => {},
  logout: () => {},
})

export const useAuth = () => useContext(AuthContext)
