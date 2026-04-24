/**
 * Video lap loading + UI (picker, speed, status). Sync is NOT here —
 * updateMarker, RVFC/rAF loops, and lookup helpers all live in sync.js.
 *
 * This module owns:
 *   - populating the lap picker after Plot
 *   - fetching /api/video/{sid}/{lap} metadata + sidecar
 *   - deciding blob-buffer vs stream based on Content-Length
 *   - revoking the previous blob URL on lap switch
 *   - stale-token protection across interleaved loads
 *
 * It calls into sync.js at two well-defined points:
 *   - buildSyncLookups(meta.sync) after parsing metadata
 *   - highlightVideoLapTrace(label) at the start of a lap load
 */

import { videoState } from './state.js';
import { buildSyncLookups, highlightVideoLapTrace } from './sync.js';

function setVideoStatus(msg, level) {
  const el = document.getElementById('video-status');
  if (!el) return;
  el.textContent = msg || '';
  el.className = 'video-status' + (level ? ' ' + level : '');
}

/**
 * Show the video loading overlay. Call at the top of every new load attempt.
 * No token check needed here — a new load always owns the overlay immediately.
 */
function showVideoLoading(label) {
  const overlay = document.getElementById('video-loading-overlay');
  const lbl = document.getElementById('video-loading-label');
  if (!overlay) return;
  if (lbl) lbl.textContent = label || 'Loading video…';
  // 'hidden' is the sole toggle; the layout classes (flex, flex-col, etc.) stay
  // in the element's static class list and must never be removed.
  overlay.classList.remove('hidden');
}

/**
 * Hide the video loading overlay. Requires the caller's token to match the
 * current load token so that a stale load finishing late cannot clear the
 * overlay that a newer load already owns.
 */
function hideVideoLoading(token) {
  if (token !== undefined && token !== videoState.currentLoadToken) return;
  const overlay = document.getElementById('video-loading-overlay');
  if (!overlay) return;
  overlay.classList.add('hidden');
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

  // Release previous blob URL
  if (videoState.blobUrl) {
    URL.revokeObjectURL(videoState.blobUrl);
    videoState.blobUrl = null;
  }

  // Fetch the full MP4 into memory (up to 100 MB) so seeking is instant.
  // Larger files fall back to streaming.
  const MAX_BLOB_BYTES = 100 * 1048576;
  setVideoStatus('Buffering video...');
  showVideoLoading('Buffering video…');
  try {
    const headResp = await fetch(meta.mp4_url, { method: 'HEAD' });
    if (token !== videoState.currentLoadToken) return;
    const contentLen = parseInt(headResp.headers.get('Content-Length') || '0', 10);

    let sizeMB = '?';
    if (contentLen > 0 && contentLen <= MAX_BLOB_BYTES) {
      const resp = await fetch(meta.mp4_url);
      if (token !== videoState.currentLoadToken) return;
      if (!resp.ok) {
        setVideoStatus('Failed to buffer video (HTTP ' + resp.status + ')', 'error');
        hideVideoLoading(token);
        return;
      }
      const blob = await resp.blob();
      if (token !== videoState.currentLoadToken) return;
      videoState.blobUrl = URL.createObjectURL(blob);
      video.src = videoState.blobUrl;
      sizeMB = (blob.size / 1048576).toFixed(1);
    } else {
      // Too large or unknown size — stream directly
      video.src = meta.mp4_url;
      if (contentLen > 0) sizeMB = (contentLen / 1048576).toFixed(1);
    }
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
    video.play().catch(() => {});

    if (videoState.frames) {
      const dur = meta.sync.duration_ms ? (meta.sync.duration_ms / 1000).toFixed(1) + 's' : '?';
      setVideoStatus(`${sizeMB} MB • ${dur} • sync ${videoState.frames.length} pts`);
    } else {
      setVideoStatus(
        `${sizeMB} MB` + (meta.message ? ' • ' + meta.message : ''),
        meta.has_sync ? '' : 'warn',
      );
    }
  } catch (e) {
    if (token !== videoState.currentLoadToken) return;
    setVideoStatus('Video buffer failed: ' + (e.message || e), 'error');
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
