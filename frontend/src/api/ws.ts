import { useEffect, useRef, useState } from 'react'

export interface WebSocketMessage {
  type: string
  payload: any
}

interface UseWebSocketEventsOptions {
  onMessage?: (message: WebSocketMessage) => void
}

export function useWebSocketEvents(options?: UseWebSocketEventsOptions) {
  const [isConnected, setIsConnected] = useState(false)
  const wsRef = useRef<WebSocket | null>(null)
  const reconnectTimeoutRef = useRef<number>()

  // Store the latest onMessage callback so we don't need to depend on `options.onMessage`
  // directly in the useEffect, avoiding stale closures.
  const savedOnMessage = useRef(options?.onMessage)

  useEffect(() => {
    savedOnMessage.current = options?.onMessage
  }, [options?.onMessage])

  useEffect(() => {
    // Get token from local storage
    const accessToken = localStorage.getItem('access_token')

    // Only connect if we have a token
    if (!accessToken) return

    const connect = () => {
      // Create WebSocket URL
      // If we're on https://, use wss://, otherwise ws://
      const protocol = window.location.protocol === 'https:' ? 'wss:' : 'ws:'

      // We expect VITE_API_URL to be something like "http://localhost:8000/api"
      // or "/api" in production
      const apiUrl = import.meta.env.VITE_API_URL || '/api'

      let wsUrl = ''
      if (apiUrl.startsWith('http')) {
         wsUrl = apiUrl.replace(/^http/, protocol === 'wss:' ? 'wss' : 'ws') + '/ws/events'
      } else {
         const host = window.location.host
         wsUrl = `${protocol}//${host}${apiUrl}/ws/events`
      }

      const urlWithToken = `${wsUrl}?token=${encodeURIComponent(accessToken)}`

      console.log('Connecting to WebSocket:', wsUrl)

      const ws = new WebSocket(urlWithToken)

      ws.onopen = () => {
        console.log('WebSocket connected')
        setIsConnected(true)
      }

      ws.onmessage = (event) => {
        try {
          const data = JSON.parse(event.data) as WebSocketMessage
          if (savedOnMessage.current) {
            savedOnMessage.current(data)
          }
        } catch (err) {
          console.error('Failed to parse WebSocket message:', err)
        }
      }

      ws.onclose = (event) => {
        console.log('WebSocket disconnected', event.code, event.reason)
        setIsConnected(false)

        // Don't reconnect if it was a deliberate close or an auth failure (1008)
        if (event.code !== 1000 && event.code !== 1008) {
          // Attempt to reconnect after a delay
          reconnectTimeoutRef.current = window.setTimeout(() => {
            console.log('Attempting to reconnect WebSocket...')
            connect()
          }, 3000)
        }
      }

      ws.onerror = (error) => {
        console.error('WebSocket error:', error)
      }

      wsRef.current = ws
    }

    connect()

    return () => {
      if (reconnectTimeoutRef.current) {
        clearTimeout(reconnectTimeoutRef.current)
      }
      if (wsRef.current) {
        wsRef.current.close(1000, 'Component unmounting')
      }
    }
  }, []) // Empty dependency array means it runs once on mount

  return { isConnected }
}
