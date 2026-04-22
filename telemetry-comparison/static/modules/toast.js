/**
 * Toast stack — top-center cards for informational / warning / error /
 * success messages. Renders into #toasts in index.html.
 *
 * Plain (non-module) script so `showToast` lives on `window` and can be
 * called from anywhere in app.js without imports.
 */

const TOAST_ICONS = { error: '!', warn: '!', info: 'i', success: '✓' };

/**
 * Show a toast/flash message anchored to the top-center of the viewport.
 * All kinds share the same card layout; color + icon convey severity.
 *
 * @param arg  either a string (becomes the title) or an object
 *             { title, detail } where detail is rendered below the title.
 * @param kind "info" | "warn" | "error" | "success".
 * @param durationMs auto-dismiss delay in ms. 0 = sticky until user closes.
 *                   Default: errors are sticky; everything else fades after 8 s.
 */
function showToast(arg, kind = 'info', durationMs) {
  if (durationMs === undefined) durationMs = kind === 'error' ? 0 : 8000;
  const { title, detail } = typeof arg === 'string' ? { title: arg, detail: '' } : arg;
  const container = document.getElementById('toasts');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast toast--${kind}`;

  const icon = document.createElement('span');
  icon.className = 'toast__icon';
  icon.textContent = TOAST_ICONS[kind] || TOAST_ICONS.info;
  toast.appendChild(icon);

  const body = document.createElement('div');
  body.className = 'toast__body';
  const titleEl = document.createElement('div');
  titleEl.className = 'toast__title';
  titleEl.textContent = title;
  body.appendChild(titleEl);
  if (detail) {
    const detailEl = document.createElement('div');
    detailEl.className = 'toast__detail';
    detailEl.textContent = detail;
    body.appendChild(detailEl);
  }
  toast.appendChild(body);

  const closeBtn = document.createElement('button');
  closeBtn.className = 'toast__close';
  closeBtn.textContent = '×';
  toast.appendChild(closeBtn);

  const dismiss = () => {
    if (toast.classList.contains('toast--leaving')) return;
    toast.classList.add('toast--leaving');
    toast.addEventListener('animationend', () => toast.remove(), { once: true });
  };
  closeBtn.addEventListener('click', dismiss);
  container.appendChild(toast);
  if (durationMs > 0) setTimeout(dismiss, durationMs);
  return toast;
}
