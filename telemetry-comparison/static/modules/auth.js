/**
 * Bearer-token auth handshake + global fetch override.
 *
 * Embedded mode (iframe inside Test Manager / Quix Portal):
 *   - postMessage `{type: "REQUEST_AUTH_TOKEN"}` to parent.
 *   - Parent replies `{type: "AUTH_TOKEN", token: "..."}`.
 *   - Token is held in memory only; NOT persisted (parent owns lifetime).
 *
 * Standalone mode (direct URL access):
 *   - Check `localStorage["telexp_token"]`. If valid (verified via /api/channels),
 *     use it.
 *   - Otherwise show the PAT dialog; user pastes a Quix Personal Access Token.
 *   - Token persisted to localStorage on success.
 *
 * After init, the original `window.fetch` is replaced with a wrapper that:
 *   - Injects `Authorization: Bearer <token>` on every same-origin /api/* call.
 *   - On 401, refreshes (re-asks parent or re-prompts) and retries once.
 *   - Leaves cross-origin / non-/api requests untouched.
 */

const STORAGE_KEY = 'telexp_token';
const PARENT_TIMEOUT_MS = 5000;
const REFRESH_INTERVAL_MS = 30 * 60 * 1000;

// Only accept AUTH_TOKEN messages from parents on a Quix-owned domain. Same
// origin is always allowed (covers local dev + tests). `.quix.io` is the
// classic Quix Cloud host; `.byox.demo` covers BYOX-deployed workspaces
// (e.g. *.edge.byox.demo) where the Test Manager parent is served.
const _TRUSTED_ORIGIN_PATTERN = /\.(quix\.io|byox\.demo)$/;

let _token = null;
let _initialPromise = null;
let _refreshPromise = null;
const _isEmbedded = window.self !== window.top;

// Captured before we replace window.fetch so internal verify/retry calls
// don't recurse through the wrapper.
const _origFetch = window.fetch.bind(window);

function _isTrustedOrigin(origin) {
  if (origin === window.location.origin) return true;
  try {
    const host = new URL(origin).hostname;
    // `localhost` / 127.0.0.1 cover the local dev loop where TM and TE
    // run on the same host but different ports.
    if (host === 'localhost' || host === '127.0.0.1') return true;
    return _TRUSTED_ORIGIN_PATTERN.test(host);
  } catch {
    return false;
  }
}

function _postToParent(type) {
  if (!_isEmbedded) return;
  window.parent.postMessage({ type }, '*');
}

function _requestFromParent() {
  return new Promise((resolve, reject) => {
    const timer = setTimeout(() => {
      window.removeEventListener('message', handler);
      reject(new Error('parent did not respond with AUTH_TOKEN'));
    }, PARENT_TIMEOUT_MS);
    const handler = (e) => {
      if (!_isTrustedOrigin(e.origin)) return;
      if (!e.data || typeof e.data !== 'object') return;
      if (e.data.type !== 'AUTH_TOKEN' || typeof e.data.token !== 'string') return;
      clearTimeout(timer);
      window.removeEventListener('message', handler);
      resolve(e.data.token);
    };
    window.addEventListener('message', handler);
    _postToParent('REQUEST_AUTH_TOKEN');
  });
}

function _setupPersistentListener() {
  // Embedded parents may push unsolicited token refreshes (e.g. 30 min cycle).
  window.addEventListener('message', (e) => {
    if (!_isTrustedOrigin(e.origin)) return;
    if (!e.data || typeof e.data !== 'object') return;
    if (e.data.type === 'AUTH_TOKEN' && typeof e.data.token === 'string') {
      _token = e.data.token;
    }
  });
}

async function _verifyToken(token) {
  try {
    const r = await _origFetch('/api/channels', {
      headers: { Authorization: `Bearer ${token}` },
    });
    return r.ok;
  } catch {
    return false;
  }
}

function _showPATDialog() {
  const dialog = document.getElementById('auth-dialog');
  const input = document.getElementById('auth-token-input');
  const submit = document.getElementById('auth-token-submit');
  const error = document.getElementById('auth-token-error');
  if (!dialog || !input || !submit) {
    return Promise.reject(new Error('PAT dialog markup missing'));
  }
  dialog.style.display = 'flex';
  input.value = '';
  input.focus();
  if (error) error.textContent = '';

  // AbortController bundles listener cleanup so reopens don't accumulate handlers.
  const ctrl = new AbortController();
  return new Promise((resolve) => {
    const finish = (tok) => {
      ctrl.abort();
      dialog.style.display = 'none';
      resolve(tok);
    };
    const onSubmit = async () => {
      const tok = input.value.trim();
      if (!tok) {
        if (error) error.textContent = 'Token required';
        return;
      }
      if (error) error.textContent = 'Validating…';
      const ok = await _verifyToken(tok);
      if (!ok) {
        if (error) error.textContent = 'Invalid token. Try again.';
        return;
      }
      finish(tok);
    };
    submit.addEventListener('click', onSubmit, { signal: ctrl.signal });
    input.addEventListener(
      'keydown',
      (e) => {
        if (e.key === 'Enter') {
          e.preventDefault();
          onSubmit();
        }
      },
      { signal: ctrl.signal },
    );
  });
}

export function getToken() {
  return _token;
}

export function isEmbedded() {
  return _isEmbedded;
}

export async function initAuth() {
  if (_initialPromise) return _initialPromise;
  // Only embedded parents push unsolicited refreshes; registering the
  // listener in standalone mode would just widen the attack surface.
  if (_isEmbedded) _setupPersistentListener();

  _initialPromise = (async () => {
    if (_isEmbedded) {
      // Embedded handshake must succeed — a hostile parent could otherwise
      // stall the handshake and force the PAT dialog to appear inside its
      // iframe, phishing the user. No PAT fallback when embedded.
      _token = await _requestFromParent();
      return _token;
    }
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && (await _verifyToken(stored))) {
      _token = stored;
      return _token;
    }
    if (stored) localStorage.removeItem(STORAGE_KEY);
    const tok = await _showPATDialog();
    localStorage.setItem(STORAGE_KEY, tok);
    _token = tok;
    return _token;
  })();

  if (_isEmbedded) {
    setInterval(() => refresh(), REFRESH_INTERVAL_MS);
    document.addEventListener('visibilitychange', () => {
      if (!document.hidden && _token) refresh();
    });
  }

  return _initialPromise;
}

export async function refresh() {
  if (_refreshPromise) return _refreshPromise;
  _refreshPromise = (async () => {
    if (_isEmbedded) {
      try {
        const tok = await _requestFromParent();
        _token = tok;
        return tok;
      } catch {
        return null;
      }
    }
    // Standalone: re-prompt only if the current token is no longer valid.
    if (_token && (await _verifyToken(_token))) return _token;
    localStorage.removeItem(STORAGE_KEY);
    _token = null;
    const tok = await _showPATDialog();
    localStorage.setItem(STORAGE_KEY, tok);
    _token = tok;
    return tok;
  })();
  try {
    return await _refreshPromise;
  } finally {
    _refreshPromise = null;
  }
}

/**
 * fetch() drop-in that injects Bearer for /api/* and retries once on 401.
 * Same-origin paths only; absolute / cross-origin URLs pass through.
 */
export async function authFetch(input, init = {}) {
  let url = null;
  if (typeof input === 'string') url = input;
  else if (input instanceof Request) url = input.url;
  if (!url || !url.startsWith('/api/')) {
    return _origFetch(input, init);
  }
  if (!_token) await initAuth();

  const withBearer = (token) => ({
    ...init,
    headers: { ...(init.headers || {}), Authorization: `Bearer ${token}` },
  });

  let res = await _origFetch(input, withBearer(_token));
  if (res.status !== 401) return res;

  const fresh = await refresh();
  if (!fresh) return res;
  return _origFetch(input, withBearer(fresh));
}

export function installFetchOverride() {
  window.fetch = authFetch;
}
