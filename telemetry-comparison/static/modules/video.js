/**
 * Video lap loading + UI (picker, speed, status). Sync is NOT here —
 * updateMarker, RVFC/rAF loops, and lookup helpers all live in sync.js.
 *
 * This module owns:
 *   - populating the lap picker after Plot
 *   - fetching /api/video/{sid}/{lap} metadata + sidecar
 *   - assigning meta.mp4_url directly to <video src> for progressive playback
 *     (browser handles streaming via HTTP Range against /api/video/{sid}/{lap}/mp4)
 *   - stale-token protection across interleaved loads
 *
 * It calls into sync.js at two well-defined points:
 *   - buildSyncLookups(meta.sync) after parsing metadata
 *   - highlightVideoLapTrace(label) at the start of a lap load
 */

import { videoState } from './state.js';
import { buildSyncLookups, highlightVideoLapTrace } from './sync.js';
import { debugLog } from './debug-overlay.js';

/**
 * Round 6 — full-file background prefetch.
 *
 * After §6.1 the <video> element streams via HTTP Range against the proxy,
 * which is fast for first paint but unpredictable for backward seeks: the
 * browser is free to evict bytes behind the playhead, so a backward scrub
 * round-trips through the proxy and GCS again. The fix: once the live Range
 * path has reached `canplaythrough` (i.e. initial buffering is comfortably
 * past), kick off a low-priority `fetch()` of the full MP4. The browser
 * stores the 200 response in its HTTP cache (the proxy sets
 * `Cache-Control: private, max-age=300` on full responses and `no-store` on
 * 206 partials — that split, owned by `video_proxy.py`, is the load-bearing
 * piece that makes this work). Subsequent Range requests issued by the
 * <video> element on a backward seek are served from the HTTP cache, not
 * from the proxy, so the seek resolves locally.
 *
 * The prefetch is fire-and-forget: we never read `response.body`, never
 * `await` the promise, and only attach `.catch()` to swallow aborts.
 *
 * Skill-contract status: purely additive. Does not write `currentTime`,
 * does not seek, does not touch `videoState.frames`/`framesByNd`, does not
 * interact with the marker↔video sync logic. The only new side-effect on
 * the lap-load lifecycle is starting (and aborting on lap switch) the
 * background fetch.
 */
function _shouldSkipPrefetch() {
  try {
    if (localStorage.getItem('te-disable-prefetch') === '1') return 'manual';
  } catch (_) {
    /* localStorage may throw in privacy modes — treat as not set */
  }
  // navigator.connection is Chromium-only; missing on Firefox/Safari.
  // When it's missing we don't have signal to skip on, so we proceed.
  const conn = typeof navigator !== 'undefined' ? navigator.connection : null;
  if (conn) {
    if (conn.saveData === true) return 'saveData';
    if (conn.effectiveType === '2g' || conn.effectiveType === 'slow-2g') {
      return conn.effectiveType;
    }
  }
  return null;
}

function _startBackgroundPrefetch(url) {
  const skipReason = _shouldSkipPrefetch();
  if (skipReason) {
    debugLog(`[prefetch] skipped reason=${skipReason}`);
    return;
  }
  // Abort any in-flight prefetch from a previous lap so rapid lap switches
  // don't stack concurrent 30 MB downloads.
  if (videoState._prefetchAbort) {
    try {
      videoState._prefetchAbort.abort();
    } catch (_) {
      /* nothing to do */
    }
  }
  const ac = new AbortController();
  videoState._prefetchAbort = ac;
  // Truncate URL for log readability; full URL is visible in DevTools Network.
  const shortUrl = url.length > 60 ? url.slice(0, 60) + '…' : url;
  debugLog(`[prefetch] start url=${shortUrl}`);
  // priority: 'low' is a Chrome/Edge 121+ hint; older browsers ignore unknown
  // option keys silently. Safari has no native support yet but does not throw.
  fetch(url, {
    credentials: 'same-origin',
    priority: 'low',
    signal: ac.signal,
  })
    .then(async (response) => {
      // Round 7.1: flip the live-seek gate only AFTER the body has fully
      // streamed. The original Round 7 flipped on the response headers,
      // but at that point the 30 MB body is still in flight and — because
      // we never read it — some browsers cancel the stream entirely, so
      // nothing reaches the HTTP cache. Subsequent Range requests from
      // <video> miss the cache, hit the proxy, and the seek-storm returns.
      //
      // Drain the body via a getReader() loop and discard each chunk. This
      // forces the browser to actually receive every byte (populating the
      // disk-backed HTTP cache) without holding more than one chunk in JS
      // heap at a time. Total JS memory footprint: one chunk (~64 KB).
      if (!response || !response.ok || !response.body) return;
      const reader = response.body.getReader();
      while (true) {
        const { done } = await reader.read();
        if (done) break;
      }
      videoState._prefetchDone = true;
      debugLog('[prefetch] complete — live seek enabled');
    })
    .catch(() => {
      /* fire-and-forget: aborts and network errors are both fine here */
    });
}

function setVideoStatus(msg, level) {
  const el = document.getElementById('video-status');
  if (!el) return;
  el.textContent = msg || '';
  el.className = 'video-status' + (level ? ' ' + level : '');
}

// Minimum time the loading overlay stays visible (ms).
// Set high enough that Ludvík can confirm the overlay is actually firing;
// lower to ~150 once confirmed working.
const LOADING_MIN_DISPLAY_MS = 500;

/**
 * Show the video loading overlay. Call at the top of every new load attempt.
 * No token check needed here — a new load always owns the overlay immediately.
 * Records Date.now() on videoState so hideVideoLoading can enforce a minimum
 * display time (prevents the overlay flashing invisible on cached/fast loads).
 */
function showVideoLoading(label) {
  const overlay = document.getElementById('video-loading-overlay');
  const lbl = document.getElementById('video-loading-label');
  if (!overlay) return;
  if (lbl) lbl.textContent = label || 'Loading video…';
  // 'hidden' is the sole toggle; the layout classes (flex, flex-col, etc.) stay
  // in the element's static class list and must never be removed.
  overlay.classList.remove('hidden');
  videoState.loadingShownAt = Date.now();
}

/**
 * Hide the video loading overlay. Requires the caller's token to match the
 * current load token so that a stale load finishing late cannot clear the
 * overlay that a newer load already owns.
 *
 * Enforces LOADING_MIN_DISPLAY_MS: if the overlay was shown less than that
 * many ms ago, defers the hide via setTimeout. The deferred callback re-checks
 * the token so a newer load that started in the meantime keeps ownership.
 */
function hideVideoLoading(token) {
  if (token !== undefined && token !== videoState.currentLoadToken) return;
  const overlay = document.getElementById('video-loading-overlay');
  if (!overlay) return;
  const elapsed = Date.now() - (videoState.loadingShownAt || 0);
  const remaining = LOADING_MIN_DISPLAY_MS - elapsed;
  if (remaining > 0) {
    setTimeout(() => {
      // Re-check: a newer load may have taken over while we waited.
      if (token !== undefined && token !== videoState.currentLoadToken) return;
      overlay.classList.add('hidden');
    }, remaining);
  } else {
    overlay.classList.add('hidden');
  }
}

function showVideoElement() {
  const v = document.getElementById('video-player');
  const e = document.getElementById('video-empty');
  const c = document.getElementById('video-controls');
  if (v) v.style.display = 'block';
  if (e) e.style.display = 'none';
  if (c) c.style.display = 'flex';
}

function hideVideoElement(emptyMsg) {
  const v = document.getElementById('video-player');
  const e = document.getElementById('video-empty');
  const c = document.getElementById('video-controls');
  if (v) {
    try {
      v.pause();
    } catch (_) {}
    v.removeAttribute('src');
    v.load();
    v.style.display = 'none';
  }
  if (e) {
    e.textContent = emptyMsg || 'No video for this lap';
    e.style.display = 'flex';
  }
  // Keep video controls visible so user can switch laps even when current has no video
  videoState.frames = null;
  videoState.framesByNd = null;
  videoState.isPlaying = false;
  void c; // referenced for layout parity with original
}

export function onVideoSpeedChange(rate) {
  if (!videoState.element) return;
  const r = parseFloat(rate);
  if (Number.isFinite(r) && r > 0) videoState.element.playbackRate = r;
}

function _currentVideoSpeed() {
  const el = document.getElementById('video-speed');
  const r = el ? parseFloat(el.value) : 1;
  return Number.isFinite(r) && r > 0 ? r : 1;
}

export function populateVideoLapPicker(selections) {
  const select = document.getElementById('video-lap-select');
  if (!select) return;

  videoState.laps = (selections || []).slice();
  select.innerHTML = '';

  if (!videoState.laps.length) {
    select.appendChild(new Option('— pick laps and Plot —', ''));
    select.disabled = true;
    hideVideoElement('Pick laps and click Plot');
    setVideoStatus('');
    return;
  }

  videoState.laps.forEach((sel, i) => {
    const sid = sel.key.session_id || '?';
    // Short session display: just the timestamp portion
    const shortSid = sid.length > 19 ? sid.slice(11, 19) : sid;
    const text = `${sel.label} • ${shortSid}`;
    select.appendChild(new Option(text, String(i)));
  });
  select.disabled = false;
  const vc = document.getElementById('video-controls');
  if (vc) vc.style.display = 'flex';

  // Auto-load the first lap if nothing was loaded yet (preserve user's choice
  // across re-plots if the same lap is still in the list).
  let preserveIdx = -1;
  if (videoState.currentLapIdx >= 0) {
    const prev = videoState.laps[videoState.currentLapIdx];
    if (prev) preserveIdx = videoState.currentLapIdx;
  }
  const targetIdx = preserveIdx >= 0 ? preserveIdx : 0;
  select.value = String(targetIdx);
  loadVideoForLapIdx(targetIdx);
}

export function onVideoLapChange(idxStr) {
  const idx = parseInt(idxStr, 10);
  if (Number.isFinite(idx)) loadVideoForLapIdx(idx);
}

export async function loadVideoForLapIdx(idx) {
  const sel = videoState.laps[idx];
  if (!sel) return;
  videoState.currentLapIdx = idx;
  highlightVideoLapTrace(sel.label);

  const sid = sel.key.session_id;
  const lap = sel.lap;
  if (!sid && sid !== 0) {
    hideVideoElement('Selection has no session_id');
    setVideoStatus('');
    return;
  }

  const token = ++videoState.currentLoadToken;
  setVideoStatus('Loading video...');
  showVideoLoading('Loading video…');

  // Round 6: abort any prior lap's background prefetch immediately on a new
  // lap load (before we even know if the new lap has video). Avoids stacking
  // concurrent full-file downloads across rapid lap switches.
  if (videoState._prefetchAbort) {
    try {
      videoState._prefetchAbort.abort();
    } catch (_) {
      /* nothing to do */
    }
    videoState._prefetchAbort = null;
  }
  // Round 7: each lap starts fresh — the prior lap's cached MP4 doesn't help
  // the new lap, so flip the live-seek gate back off until the new prefetch
  // (if any) resolves.
  videoState._prefetchDone = false;

  let meta;
  try {
    const url = `/api/video/${encodeURIComponent(sid)}/${lap}`;
    const res = await fetch(url);
    if (!res.ok) {
      hideVideoElement('Video unavailable (HTTP ' + res.status + ')');
      setVideoStatus('HTTP ' + res.status, 'error');
      hideVideoLoading(token);
      return;
    }
    meta = await res.json();
  } catch (e) {
    hideVideoElement('Video request failed');
    setVideoStatus(e.message || String(e), 'error');
    hideVideoLoading(token);
    return;
  }

  // A newer load may have started while we awaited — drop stale results
  if (token !== videoState.currentLoadToken) return;

  if (!meta || !meta.has_video) {
    hideVideoElement((meta && meta.message) || 'No video for this lap');
    setVideoStatus('');
    hideVideoLoading(token);
    return;
  }

  showVideoElement();
  const video = videoState.element;

  // Build sync lookups first (doesn't depend on video download)
  if (meta.has_sync && meta.sync && Array.isArray(meta.sync.frames)) {
    buildSyncLookups(meta.sync);
  } else {
    videoState.frames = null;
    videoState.framesByNd = null;
  }

  if (!video) return;
  try {
    video.pause();
  } catch (_) {}
  videoState.isPlaying = false;

  // Browser handles streaming via HTTP Range against /api/video/{sid}/{lap}/mp4.
  // The proxy supports Range; combined with -movflags +faststart on the recorder
  // (ac_video_streaming/video_recorder.py) the moov atom is at the front, so the
  // browser can decode the first frame after fetching only the head of the file.
  setVideoStatus('Loading video…');
  try {
    video.preload = 'auto';
    video.src = meta.mp4_url;
    video.load();
    video.currentTime = 0;
    video.playbackRate = _currentVideoSpeed();
    // Compute timebase correction once MP4 metadata loads
    video.addEventListener(
      'loadedmetadata',
      function _onMeta() {
        video.removeEventListener('loadedmetadata', _onMeta);
        const mp4Dur = video.duration * 1000;
        if (videoState.sidecarDurationMs > 0 && mp4Dur > 0) {
          videoState.timeScale = videoState.sidecarDurationMs / mp4Dur;
        }
      },
      { once: true },
    );
    // Hide the loading overlay once the browser has buffered enough to play.
    // Capture the token in closure so a stale listener cannot clear an overlay
    // owned by a newer concurrent load.
    const capturedToken = token;
    video.addEventListener(
      'canplay',
      function _onCanPlay() {
        video.removeEventListener('canplay', _onCanPlay);
        hideVideoLoading(capturedToken);
      },
      { once: true },
    );
    // Round 6: kick off a low-priority full-file prefetch only after the live
    // Range path is comfortably playing. canplaythrough (not loadedmetadata or
    // canplay) so we never starve initial buffering on slow networks.
    const prefetchUrl = meta.mp4_url;
    video.addEventListener(
      'canplaythrough',
      function _onCanPlayThrough() {
        video.removeEventListener('canplaythrough', _onCanPlayThrough);
        // Drop stale fires: if a newer lap was loaded between canplay and
        // canplaythrough, don't kick off a prefetch for the superseded lap.
        if (capturedToken !== videoState.currentLoadToken) return;
        _startBackgroundPrefetch(prefetchUrl);
      },
      { once: true },
    );
    video.play().catch(() => {});

    if (videoState.frames) {
      const dur = meta.sync.duration_ms ? (meta.sync.duration_ms / 1000).toFixed(1) + 's' : '?';
      setVideoStatus(`${dur} • sync ${videoState.frames.length} pts`);
    } else {
      setVideoStatus(
        meta.message ? meta.message : 'Streaming',
        meta.has_sync ? '' : 'warn',
      );
    }
  } catch (e) {
    if (token !== videoState.currentLoadToken) return;
    setVideoStatus('Video load failed: ' + (e.message || e), 'error');
    hideVideoLoading(token);
  }
}

// ---------------------------------------------------------------------------
// Inline-HTML handler surface. The two <select>s in index.html fire these
// directly via onchange=.
// ---------------------------------------------------------------------------

window.onVideoLapChange = onVideoLapChange;
window.onVideoSpeedChange = onVideoSpeedChange;

// charts.js dispatches this after a successful plot; video.js owns the
// lap-picker lifecycle so the import graph stays acyclic.
document.addEventListener('plot-complete', (ev) => {
  populateVideoLapPicker(ev.detail?.selections || []);
});
