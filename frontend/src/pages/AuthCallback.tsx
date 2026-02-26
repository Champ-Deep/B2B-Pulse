import { useEffect, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { Spinner } from '../components/Loading'

export default function AuthCallback() {
  const navigate = useNavigate()
  const [error, setError] = useState('')

  useEffect(() => {
    const hash = window.location.hash.substring(1)
    const params = new URLSearchParams(hash)
    const accessToken = params.get('access_token')
    const refreshToken = params.get('refresh_token')
    const isNew = params.get('is_new') === '1'

    if (accessToken && refreshToken) {
      localStorage.setItem('access_token', accessToken)
      localStorage.setItem('refresh_token', refreshToken)
      // Clear fragment from URL and redirect
      window.location.replace(isNew ? '/onboarding' : '/')
    } else {
      // Check query params for error from backend redirect
      const query = new URLSearchParams(window.location.search)
      const errorMsg = query.get('error')
      setError(errorMsg || 'Authentication failed. Please try again.')
      setTimeout(() => navigate('/login'), 3000)
    }
  }, [navigate])

  if (error) {
    return (
      <div className="min-h-screen flex items-center justify-center bg-gray-50">
        <div className="text-center">
          <div className="bg-red-50 text-red-600 px-6 py-4 rounded-lg text-sm mb-4">{error}</div>
          <p className="text-gray-500 text-sm">Redirecting to login...</p>
        </div>
      </div>
    )
  }

  return (
    <div className="min-h-screen flex items-center justify-center bg-gray-50">
      <div className="text-center">
        <Spinner />
        <p className="mt-4 text-gray-600">Completing sign in...</p>
      </div>
    </div>
  )
}
