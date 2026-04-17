const dot = document.getElementById('dot');
const label = document.getElementById('label');
const meta = document.getElementById('meta');
const hint = document.getElementById('hint');
const syncBtn = document.getElementById('sync');
const disconnectBtn = document.getElementById('disconnect');

function fmtAgo(iso) {
  if (!iso) return 'never';
  const delta = Math.max(0, Date.now() - new Date(iso).getTime());
  const s = Math.floor(delta / 1000);
  if (s < 60) return `${s}s ago`;
  const m = Math.floor(s / 60);
  if (m < 60) return `${m}m ago`;
  const h = Math.floor(m / 60);
  if (h < 24) return `${h}h ago`;
  return `${Math.floor(h / 24)}d ago`;
}

function render(state) {
  const { connected, lastSyncAt, lastStatus, userName, apiBase } = state;

  if (!connected) {
    dot.className = 'dot';
    label.textContent = 'Not connected';
    meta.innerHTML = 'Open the ChampMail web app and click <b>Connect LinkedIn</b> to pair this extension.';
    syncBtn.disabled = true;
    disconnectBtn.disabled = true;
    hint.textContent = '';
    return;
  }

  syncBtn.disabled = false;
  disconnectBtn.disabled = false;

  if (lastStatus === 'ok') {
    dot.className = 'dot ok';
    label.textContent = 'Connected';
  } else if (lastStatus === 'no_cookie') {
    dot.className = 'dot warn';
    label.textContent = 'Not signed in to LinkedIn';
  } else if (lastStatus === 'auth_expired') {
    dot.className = 'dot err';
    label.textContent = 'Pairing expired — reconnect from the app';
  } else if (lastStatus === 'server_error' || lastStatus === 'network_error') {
    dot.className = 'dot err';
    label.textContent = 'Backend unreachable';
  } else {
    dot.className = 'dot';
    label.textContent = 'Waiting for first sync…';
  }

  const rows = [];
  if (userName) rows.push(`<b>${userName}</b>`);
  rows.push(`Last sync: ${fmtAgo(lastSyncAt)}`);
  if (apiBase) rows.push(`API: ${apiBase}`);
  meta.innerHTML = rows.join('<br>');

  if (lastStatus === 'no_cookie') {
    hint.textContent = 'Log in to linkedin.com and the extension will auto-sync.';
  } else if (lastStatus === 'auth_expired') {
    hint.textContent = 'Reopen the ChampMail app and click Reconnect.';
  } else {
    hint.textContent = '';
  }
}

async function refresh() {
  chrome.runtime.sendMessage({ type: 'STATUS' }, render);
}

syncBtn.addEventListener('click', () => {
  syncBtn.disabled = true;
  label.textContent = 'Syncing…';
  chrome.runtime.sendMessage({ type: 'SYNC_NOW' }, () => {
    refresh();
  });
});

disconnectBtn.addEventListener('click', () => {
  if (!confirm('Disconnect ChampMail? Automation will stop until you reconnect.')) return;
  chrome.runtime.sendMessage({ type: 'DISCONNECT' }, () => {
    refresh();
  });
});

refresh();
