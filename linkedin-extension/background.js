// ChampMail Connector — MV3 service worker
// Reads the user's li_at cookie from .linkedin.com and ships it to the backend
// so our worker can act on the user's behalf.
//
// Storage keys (chrome.storage.local):
//   pairingToken     first-sync token handed to us by the web app (short-lived)
//   extensionToken   long-lived rolling token returned by the backend on first sync
//   apiBase          backend base URL (e.g. http://localhost:8001)
//   lastSyncAt       ISO timestamp of the most recent successful sync
//   lastStatus       'ok' | 'no_cookie' | 'auth_expired' | 'server_error'
//   userName         LinkedIn display name returned by the backend

const ALARM_NAME = 'champmail-sync';
const SYNC_EVERY_MINUTES = 240; // 4h
const MIN_RESYNC_INTERVAL_MS = 60 * 1000; // debounce cookie-change bursts

let _lastSyncTriggerAt = 0;

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: SYNC_EVERY_MINUTES });
});

chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM_NAME, { periodInMinutes: SYNC_EVERY_MINUTES });
});

chrome.alarms.onAlarm.addListener((alarm) => {
  if (alarm.name === ALARM_NAME) syncCookies('alarm');
});

// Re-sync whenever li_at changes in the user's browser.
chrome.cookies.onChanged.addListener((change) => {
  const c = change.cookie;
  if (!c || c.name !== 'li_at') return;
  if (!c.domain || !c.domain.includes('linkedin.com')) return;
  syncCookies('cookie-change');
});

// Pairing handshake — the web app calls chrome.runtime.sendMessage(EXT_ID, …)
chrome.runtime.onMessageExternal.addListener((message, sender, sendResponse) => {
  (async () => {
    if (!message || typeof message !== 'object') {
      sendResponse({ ok: false, error: 'bad_message' });
      return;
    }
    if (message.type === 'PING') {
      sendResponse({ ok: true, installed: true, version: chrome.runtime.getManifest().version });
      return;
    }
    if (message.type === 'PAIR') {
      if (!message.token || !message.apiBase) {
        sendResponse({ ok: false, error: 'missing_fields' });
        return;
      }
      await chrome.storage.local.set({
        pairingToken: message.token,
        apiBase: message.apiBase.replace(/\/$/, ''),
        extensionToken: null, // reset — first sync will mint a new one
      });
      const result = await syncCookies('pair');
      sendResponse({ ok: result.ok, status: result.status, userName: result.userName });
      return;
    }
    if (message.type === 'DISCONNECT') {
      await disconnect();
      sendResponse({ ok: true });
      return;
    }
    sendResponse({ ok: false, error: 'unknown_type' });
  })();
  return true; // keep channel open for async response
});

// Popup <-> worker bridge
chrome.runtime.onMessage.addListener((message, _sender, sendResponse) => {
  (async () => {
    if (message?.type === 'STATUS') {
      const state = await chrome.storage.local.get([
        'lastSyncAt', 'lastStatus', 'userName', 'pairingToken', 'extensionToken', 'apiBase',
      ]);
      sendResponse({
        connected: !!(state.pairingToken || state.extensionToken),
        lastSyncAt: state.lastSyncAt || null,
        lastStatus: state.lastStatus || null,
        userName: state.userName || null,
        apiBase: state.apiBase || null,
      });
      return;
    }
    if (message?.type === 'SYNC_NOW') {
      const r = await syncCookies('popup');
      sendResponse(r);
      return;
    }
    if (message?.type === 'DISCONNECT') {
      await disconnect();
      sendResponse({ ok: true });
      return;
    }
    sendResponse({ ok: false, error: 'unknown_type' });
  })();
  return true;
});

async function getLinkedInCookie(name) {
  return new Promise((resolve) => {
    chrome.cookies.get({ url: 'https://www.linkedin.com', name }, (c) => resolve(c || null));
  });
}

async function syncCookies(trigger) {
  // Debounce rapid cookie-change events.
  const now = Date.now();
  if (trigger === 'cookie-change' && now - _lastSyncTriggerAt < MIN_RESYNC_INTERVAL_MS) {
    return { ok: false, status: 'debounced' };
  }
  _lastSyncTriggerAt = now;

  const state = await chrome.storage.local.get(['pairingToken', 'extensionToken', 'apiBase']);
  const token = state.extensionToken || state.pairingToken;
  const apiBase = state.apiBase;
  if (!token || !apiBase) {
    await setStatus('not_paired');
    return { ok: false, status: 'not_paired' };
  }

  const liAt = await getLinkedInCookie('li_at');
  if (!liAt || !liAt.value) {
    await setStatus('no_cookie');
    setBadge('!', '#f59e0b');
    return { ok: false, status: 'no_cookie' };
  }
  const jsession = await getLinkedInCookie('JSESSIONID');

  try {
    const resp = await fetch(`${apiBase}/api/integrations/extension/session-cookies`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        'X-Pairing-Token': token,
      },
      body: JSON.stringify({
        li_at: liAt.value,
        jsessionid: jsession ? jsession.value.replace(/^"|"$/g, '') : null,
      }),
    });

    if (resp.status === 401) {
      await chrome.storage.local.set({ pairingToken: null, extensionToken: null });
      await setStatus('auth_expired');
      setBadge('!', '#ef4444');
      return { ok: false, status: 'auth_expired' };
    }
    if (!resp.ok) {
      await setStatus('server_error');
      setBadge('!', '#ef4444');
      return { ok: false, status: 'server_error', code: resp.status };
    }

    const data = await resp.json();
    const toStore = {
      lastSyncAt: new Date().toISOString(),
      lastStatus: 'ok',
      userName: data.user_name || null,
    };
    if (data.extension_token) {
      toStore.extensionToken = data.extension_token;
      toStore.pairingToken = null; // pairing token consumed by the backend
    }
    await chrome.storage.local.set(toStore);
    setBadge('✓', '#10b981');
    return { ok: true, status: 'ok', userName: data.user_name };
  } catch (err) {
    await setStatus('network_error');
    setBadge('!', '#ef4444');
    return { ok: false, status: 'network_error', error: String(err) };
  }
}

async function disconnect() {
  const state = await chrome.storage.local.get(['extensionToken', 'apiBase']);
  if (state.extensionToken && state.apiBase) {
    try {
      await fetch(`${state.apiBase}/api/integrations/extension/disconnect`, {
        method: 'POST',
        headers: { 'X-Pairing-Token': state.extensionToken },
      });
    } catch (_) { /* best effort */ }
  }
  await chrome.storage.local.clear();
  setBadge('', '#64748b');
}

async function setStatus(status) {
  await chrome.storage.local.set({ lastStatus: status });
}

function setBadge(text, color) {
  try {
    chrome.action.setBadgeText({ text });
    if (text) chrome.action.setBadgeBackgroundColor({ color });
  } catch (_) { /* ignore */ }
}
