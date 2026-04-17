// Thin bridge between the web app and the ChampMail Connector extension.
//
// The extension declares `externally_connectable` for our frontend origin so
// we can reach its service worker via chrome.runtime.sendMessage. This
// module centralizes that plumbing so components can treat the extension as
// a normal promise-returning API.

const EXTENSION_ID = import.meta.env.VITE_EXTENSION_ID as string | undefined

type ChromeSendMessage = (
  extensionId: string,
  message: unknown,
  responseCallback: (response: unknown) => void,
) => void

function getChrome(): { runtime: { sendMessage: ChromeSendMessage; lastError?: { message?: string } } } | null {
  const c = (window as unknown as { chrome?: unknown }).chrome
  if (!c || typeof c !== 'object') return null
  const runtime = (c as { runtime?: unknown }).runtime
  if (!runtime || typeof runtime !== 'object') return null
  if (typeof (runtime as { sendMessage?: unknown }).sendMessage !== 'function') return null
  return c as never
}

function send<T = unknown>(message: unknown): Promise<T | null> {
  const c = getChrome()
  if (!c || !EXTENSION_ID) return Promise.resolve(null)
  return new Promise((resolve) => {
    try {
      c.runtime.sendMessage(EXTENSION_ID, message, (response: unknown) => {
        // When the extension isn't installed, chrome sets lastError and calls
        // the callback with undefined. We swallow it and resolve null so the
        // caller can fall back to another path.
        if (c.runtime.lastError) {
          resolve(null)
          return
        }
        resolve((response ?? null) as T | null)
      })
    } catch {
      resolve(null)
    }
  })
}

export function hasExtensionRuntime(): boolean {
  return !!getChrome() && !!EXTENSION_ID
}

export async function pingExtension(): Promise<boolean> {
  if (!hasExtensionRuntime()) return false
  const resp = await send<{ ok?: boolean; installed?: boolean }>({ type: 'PING' })
  return !!(resp && resp.ok && resp.installed)
}

export interface PairResult {
  ok: boolean
  status?: string
  userName?: string | null
}

export async function pairExtension(pairingToken: string, apiBase: string): Promise<PairResult> {
  if (!hasExtensionRuntime()) return { ok: false, status: 'not_installed' }
  const resp = await send<PairResult>({ type: 'PAIR', token: pairingToken, apiBase })
  return resp ?? { ok: false, status: 'no_response' }
}

export async function disconnectExtension(): Promise<boolean> {
  if (!hasExtensionRuntime()) return false
  const resp = await send<{ ok?: boolean }>({ type: 'DISCONNECT' })
  return !!(resp && resp.ok)
}

export const extensionInstallHint =
  'Install the ChampMail Connector extension, reload this page, then try again.'
