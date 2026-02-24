import type { AxiosError } from 'axios'

/**
 * Extract a human-readable error message from an API error response.
 */
export function getApiErrorMessage(err: unknown, fallback = 'Something went wrong'): string {
  const axiosErr = err as AxiosError<{ detail?: string | Array<{ msg: string }> }>
  const detail = axiosErr?.response?.data?.detail
  if (typeof detail === 'string') return detail
  if (Array.isArray(detail)) return detail.map((d) => d.msg).join(', ')
  return fallback
}
