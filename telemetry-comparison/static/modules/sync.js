/**
 * Marker ↔ Video bidirectional sync.
 *
 * This entire file owns the feedback-loop guard (`source === 'drag' | 'video'`)
 * and MUST remain the single place the `source` tag is inspected. `charts.js`
 * and `video.js` both import from here; neither imports from the other.
 *
 * Contract (frozen — see .claude/skills/video-seeking/SKILL.md):
 *   updateMarker(nd, forceTrack, source)
 *     source === 'drag'  → user dragged marker → seek paused video
 *     source === 'video' → video drove the marker → don't echo back
 *     source === undefined → programmatic (plot, init) → leave video alone
 *
 *   buildSyncLookups(sync) — populate videoState.frames + framesByNd.
 *   lookupTmsForNormPos(nd), lookupNormPosForTms(t_ms) — binary-search helpers.
 *   highlightVideoLapTrace(label), clearTraceHighlight() — visual pass,
 *     driven by videoState.currentLapIdx; lives here because it shares state
 *     with the loop (plotTraces / plotDivs / highlightedLabel).
 *   wireVideoElement() — one-shot init; attaches play/pause/seeked listeners
 *     and bootstraps the RVFC or rAF loop.
 */

import {
  appState,
  videoState,
  setMarkerPosition,
  getTrackZoom,
  getTrackData,
  MAX_TRACE_ANNOTATIONS,
} from './state.js';
import { interpolateAt, _interp } from './data.js';
import { debugLog } from './debug-overlay.js';

// ---------------------------------------------------------------------------
// Touch pause flag for the rAF marker loop.
//
// Tablet diagnostic (option 3 from
// dev-planning/telemetry-explorer-touch-responsiveness/discussion.md): while
// the floating video plays, the rAF loop polls video.currentTime at ~60 Hz
// and rebuilds Plotly shapes/annotations on every chart. That eats main-
// thread budget that pointer dispatch needs. We suspend the loop body while
// any pointer is down so taps on the Float/Dock button get instant
// dispatch; the loop resumes the moment the pointer lifts.
//
// IMPORTANT: we do NOT cancelAnimationFrame here — the rAF id keeps cycling
// and the body just no-ops while paused. That way `pointerup` doesn't have
// to re-prime the loop; the next scheduled frame just notices `_touchPauseDepth
// === 0` and runs normally. Resume latency = one frame (~16 ms).
//
// `_touchPauseDepth` is a counter, not a boolean, because multitouch can
// fire several `pointerdown`s before any `pointerup`. Counting ensures the
// loop only resumes after every finger has lifted (depth back to 0).
// `pointercancel` and `touchcancel` decrement just like `pointerup`. A
// safety watchdog (re-zero on `visibilitychange`) prevents a stuck flag
// from permanently freezing the marker if the OS swallows a release event.
// ---------------------------------------------------------------------------
let _touchPauseDepth = 0;

/**
 * Readout strip under the top bar: displays the current marker as a
 * percent-of-lap plus an absolute track-distance when we have track data.
 * Lives in sync.js (not charts.js) because updateMarker calls it on every
 * marker move, and placing it in charts.js would create a sync ↔ charts
 * import cycle (spec §5 bans that direction).
 */
function updateReadout() {
  const el = document.getElementById('readout-pos-text');
  if (!el) return;

  const trackPt =
    typeof window.trackPointAtNorm === 'function'
      ? window.trackPointAtNorm(appState.markerPosition)
      : null;
  el.textContent = trackPt
    ? `${(appState.markerPosition * 100).toFixed(1)}% @ ${trackPt.distance_m.toFixed(0)}m`
    : `${(appState.markerPosition * 100).toFixed(1)}%`;
}

export { updateReadout };

// ---------------------------------------------------------------------------
// The central hub. Both directions (drag → video, video → marker) call here.
// Keep this function's body aligned with the pre-refactor version; a diff
// against the original app.js is the primary review aid.
// ---------------------------------------------------------------------------

export function updateMarker(nd, forceTrack, source) {
  setMarkerPosition(nd);
  // Video sync — only marker→video; video→marker callers pass source='video'.
  syncVideoFromMarker(nd, source);

  // Update track dot
  const trackData = getTrackData();
  if (forceTrack || trackData) {
    const p = typeof window.trackPointAtNorm === 'function' ? window.trackPointAtNorm(nd) : null;
    if (p && window._markerTraceIdx !== undefined) {
      const div = document.getElementById('track-map');
      if (div && div.data) {
        Plotly.restyle(div, { x: [[p.x]], y: [[-p.z]] }, [window._markerTraceIdx]);
        // Re-center zoom window on the dot ONLY when zoomed in
        if (getTrackZoom() > 1.02 && typeof window.applyZoom === 'function') window.applyZoom();
      }
    }
  }

  // Update marker line + per-trace value annotations (up to 6 per plot) on every plot
  for (let i = 0; i < appState.plotDivs.length; i++) {
    const div = appState.plotDivs[i];
    if (!div.layout) continue;

    const traces = appState.plotTraces[i] || [];
    const cornerAnn = div._cornerAnnotations || [];
    const valueAnn = [];
    const shown = Math.min(MAX_TRACE_ANNOTATIONS, traces.length);

    // Stack annotations vertically in a fixed column next to the marker line
    // so they never overlap regardless of where the trace values fall.
    // Selected (highlighted) annotation renders last so it draws on top.
    const ROW_H = 18; // px between stacked labels
    let hlAnn = null;
    for (let k = 0; k < shown; k++) {
      const t = traces[k];
      const v = interpolateAt(t.x, t.y, nd);
      if (v === null || !isFinite(v)) continue;
      const valStr = Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(2);
      const isHL = appState.highlightedLabel && t.label === appState.highlightedLabel;
      const ann = {
        xref: 'x',
        yref: 'paper',
        x: nd,
        y: 1,
        text: isHL ? `<b>${valStr}</b>` : valStr,
        showarrow: false,
        xanchor: 'left',
        yanchor: 'top',
        xshift: 6,
        yshift: -(k * ROW_H) - 2,
        font: { color: t.color, size: 11, family: 'monospace' },
        bgcolor: 'rgba(15,17,23,0.9)',
        bordercolor: t.color,
        borderwidth: isHL ? 2 : 1,
        borderpad: 2,
        opacity: 1,
      };
      if (isHL) {
        hlAnn = ann;
      } else {
        valueAnn.push(ann);
      }
    }
    if (hlAnn) valueAnn.push(hlAnn);

    if (traces.length > MAX_TRACE_ANNOTATIONS) {
      valueAnn.push({
        xref: 'x',
        yref: 'paper',
        x: nd,
        y: 1,
        text: `+${traces.length - MAX_TRACE_ANNOTATIONS}`,
        showarrow: false,
        xanchor: 'left',
        yanchor: 'top',
        xshift: 6,
        yshift: -(shown * ROW_H) - 2,
        font: { color: '#8892a4', size: 9, style: 'italic' },
        bgcolor: 'rgba(15,17,23,0.7)',
        borderpad: 1,
      });
    }

    Plotly.relayout(div, {
      'shapes[0].x0': nd,
      'shapes[0].x1': nd,
      annotations: cornerAnn.concat(valueAnn),
    });
  }

  updateReadout();
}

// ---------------------------------------------------------------------------
// Highlight the currently-loaded video lap's trace across all charts.
// Called on lap load and on highlight clear.
// ---------------------------------------------------------------------------

export function highlightVideoLapTrace(label) {
  appState.highlightedLabel = label;
  if (!appState.plotDivs.length || !appState.plotTraces.length) return;
  const matchIdx = appState.plotTraces[0]?.findIndex((t) => t.label === label) ?? -1;
  const selColor = appState.plotTraces[0]?.[matchIdx]?.color;

  appState.plotDivs.forEach((div, pi) => {
    const traces = appState.plotTraces[pi];
    if (!traces || !div.data) return;

    // 1) Remove old halos FIRST so trace indices are clean
    const haloIdxs = [];
    div.data.forEach((d, i) => {
      if (d.name === '_halo') haloIdxs.push(i);
    });
    for (let i = haloIdxs.length - 1; i >= 0; i--) Plotly.deleteTraces(div, haloIdxs[i]);

    // 2) Width: selected bolder, ALL others back to normal
    traces.forEach((t, i) => {
      Plotly.restyle(
        div,
        { 'line.width': t.label === label ? 3 : 1.5, opacity: t.label === label ? 1 : 0.7 },
        [i],
      );
    });
  });

  // 3) DOM: move selected trace SVG to front + highlight legend + annotations
  // Only target chart plots (plotDivs), not the track map
  setTimeout(() => {
    appState.plotDivs.forEach((plot) => {
      // Bring selected trace to front
      const layer = plot.querySelector('.scatterlayer');
      if (layer) {
        layer.querySelectorAll('.trace').forEach((tr) => {
          const path = tr.querySelector('path.js-line');
          if (path) {
            const w = parseFloat(path.getAttribute('stroke-width') || '1');
            if (w >= 2.5) layer.appendChild(tr);
          }
        });
      }

      // Legend highlight
      const legendEntries = plot.querySelectorAll('.legend .traces');
      legendEntries.forEach((entry, i) => {
        const isMatch = i === matchIdx;
        entry.querySelectorAll('text').forEach((t) => {
          t.style.fontWeight = isMatch ? 'bold' : '';
          t.style.opacity = isMatch ? '1' : '0.5';
          t.style.filter = isMatch ? `drop-shadow(0 0 6px ${selColor || '#fff'})` : '';
        });
        entry.querySelectorAll('path, line, rect').forEach((l) => {
          l.style.opacity = isMatch ? '1' : '0.5';
          l.style.filter = isMatch ? `drop-shadow(0 0 6px ${selColor || '#fff'})` : '';
        });
      });

      // Annotation highlight handled via Plotly layout in updateMarker()
      // Force a marker update to re-render annotations with highlight
      updateMarker(appState.markerPosition, false);
    });
  }, 150);
}

export function _highlightAnnotations(plot, matchIdx, selColor) {
  const annotations = plot.querySelectorAll('.annotation');
  annotations.forEach((ann, i) => {
    const bg = ann.querySelector('rect');
    const txt = ann.querySelector('text');
    if (!bg || !txt) return;
    const isMatch = i === matchIdx;
    if (isMatch) {
      txt.style.fontWeight = 'bold';
      txt.style.fontSize = '13px';
      txt.style.opacity = '1';
      bg.style.opacity = '1';
      bg.style.filter = selColor ? `drop-shadow(0 0 4px ${selColor})` : '';
    } else {
      txt.style.fontWeight = '';
      txt.style.fontSize = '';
      txt.style.opacity = '0.5';
      bg.style.opacity = '0.5';
      bg.style.filter = '';
    }
  });
}

export function clearTraceHighlight() {
  appState.highlightedLabel = null;
  if (!appState.plotDivs.length || !appState.plotTraces.length) return;
  appState.plotDivs.forEach((div, pi) => {
    const traces = appState.plotTraces[pi];
    if (!traces || !div.data) return;
    traces.forEach((t, i) => {
      Plotly.restyle(div, { 'line.width': 1.5, opacity: 1 }, [i]);
    });
    // Remove halo traces
    const haloIdxs = [];
    div.data.forEach((d, i) => {
      if (d.name === '_halo') haloIdxs.push(i);
    });
    for (let i = haloIdxs.length - 1; i >= 0; i--) Plotly.deleteTraces(div, haloIdxs[i]);
  });
  // Reset legend styles — only chart plots, not track map
  appState.plotDivs.forEach((plot) => {
    plot.querySelectorAll('.legend .traces').forEach((entry) => {
      entry.querySelectorAll('text, path, line, rect').forEach((el) => {
        el.style.fontWeight = '';
        el.style.opacity = '';
        el.style.filter = '';
      });
    });
  });
  // Refresh annotations without highlight
  updateMarker(appState.markerPosition, false);
}

// ---------------------------------------------------------------------------
// Sidecar lookup tables — built once per lap load.
// ---------------------------------------------------------------------------

export function buildSyncLookups(sync) {
  const valid = (sync.frames || []).filter(
    (f) => f && Number.isFinite(f.t_ms) && f.normPos != null && Number.isFinite(f.normPos),
  );
  if (!valid.length) {
    videoState.frames = null;
    videoState.framesByNd = null;
    return;
  }
  // Two views: by t_ms for video-playback → marker, by normPos for
  // marker-drag → video. Maintaining both is critical because normPos is
  // NOT monotonic on out-laps (wraps from ~1.0 → 0.0 across the S/F line),
  // and binary-searching a t_ms-sorted array by normPos snaps drags to
  // either the first or last frame.
  videoState.frames = valid.slice().sort((a, b) => a.t_ms - b.t_ms);
  videoState.framesByNd = valid.slice().sort((a, b) => a.normPos - b.normPos);
  // Sidecar duration for timebase correction (MP4 duration may differ)
  videoState.sidecarDurationMs = valid.length ? valid[valid.length - 1].t_ms : 0;
  videoState.timeScale = 1; // updated once video metadata loads
}

export function lookupTmsForNormPos(nd) {
  return _interp(
    videoState.framesByNd,
    (f) => f.normPos,
    (f) => f.t_ms,
    nd,
  );
}

export function lookupNormPosForTms(t_ms) {
  return _interp(
    videoState.frames,
    (f) => f.t_ms,
    (f) => f.normPos,
    t_ms,
  );
}

// ---------------------------------------------------------------------------
// Marker → video seek (drag path). Only fires when source==='drag'.
// Video playback and programmatic updates leave the video alone.
// ---------------------------------------------------------------------------

export function syncVideoFromMarker(nd, source) {
  // Called from updateMarker.
  //   source==='video' : called by the video timeupdate handler — don't echo.
  //   source==='drag'  : user dragged the marker → pause video (if playing),
  //                      then seek to the matching frame.
  //   else             : programmatic re-render (plot/init/etc) → leave video alone.
  const v = videoState.element;
  if (!v || !videoState.frames) return;
  if (source !== 'drag') return;
  if (videoState.isPlaying) {
    try {
      v.pause();
    } catch (_) {}
  }
  const t_ms = lookupTmsForNormPos(nd);
  if (t_ms == null) return;
  const scale = videoState.timeScale || 1;
  const target = t_ms / scale / 1000;
  // Smaller-than-frame deltas would just churn the video element. At 30 fps
  // a frame is ~33ms; 15ms is ~half a frame and still feels responsive while
  // dragging.
  if (Math.abs(v.currentTime - target) <= 0.015) return;
  // Round 7.2 — revert Round 7's live-during-drag mode. Diagnostic data on
  // tablet: even with the prefetch fully cached (so Range requests hit disk,
  // seek-stall ~60ms each), 24 mid-drag seeks in 400ms fragmented the
  // MediaSource buffer into 5 disjoint ranges and left the decoder in a
  // tangled state — `play` after the drag fired `waiting`, then `stalled`,
  // and the video never recovered. Cache hits help **network** latency but
  // don't protect the **decoder pipeline** from being shredded by rapid
  // currentTime writes. So: regardless of `_prefetchDone`, we ALWAYS stash
  // during drag and let `flushPendingSeek()` (called from charts.js's
  // pointerup / pointercancel) issue the single seek on release.
  //
  // `_prefetchDone` is still set and is still useful — it makes that single
  // post-drag seek hit the HTTP cache instead of the proxy, so the seek
  // completes fast even on slow upstream networks. We just don't try to
  // preview during the drag itself.
  videoState._pendingSeekTime = target;
}

// ---------------------------------------------------------------------------
// Drain hook for the round-5 drag-end seek deferral. Called from charts.js
// attachMarkerDrag's pointerup / pointercancel handlers (option A from the
// round-5 spec — explicit hook beats a global capture-phase listener because
// the canonical "user released the drag" signal already lives in charts.js).
// No-op if nothing is stashed. The 15ms threshold mirrors the entry guard.
// ---------------------------------------------------------------------------
export function flushPendingSeek() {
  const v = videoState.element;
  const pending = videoState._pendingSeekTime;
  if (!v || pending == null) return;
  videoState._pendingSeekTime = null;
  if (Math.abs(v.currentTime - pending) > 0.015) {
    v.currentTime = pending;
  }
}

// ---------- requestVideoFrameCallback path (frame-accurate) ----------------
export function _onVideoFrame(_now, metadata) {
  if (!videoState.isPlaying || !videoState.frames || !videoState.element) return;
  const scale = videoState.timeScale || 1;
  const nd = lookupNormPosForTms(metadata.mediaTime * 1000 * scale);
  if (nd != null) {
    updateMarker(Math.max(0, Math.min(1, nd)), true, 'video');
  }
  // Re-register for the next displayed frame while still playing.
  if (videoState.isPlaying) {
    videoState.element.requestVideoFrameCallback(_onVideoFrame);
  }
}

// ---------- rAF path (display-rate smoothing for low-fps sidecars) ---------
// Sidecar sync frames land at the video fps (often 14–25 Hz) — RVFC would
// cap marker updates at that rate, producing visible jumps. Instead we poll
// video.currentTime every animation frame. The browser advances currentTime
// continuously during playback (interpolated from the wall clock), and
// lookupNormPosForTms already linearly interpolates between sparse sidecar
// samples, so the marker position is smooth at display refresh rate.
export function _videoRafLoop() {
  if (!videoState.isPlaying || !videoState.frames || !videoState.element) {
    videoState._rafId = null;
    return;
  }
  // Touch suspension: while at least one pointer is down anywhere on the page
  // we no-op the body. The rAF id keeps cycling so resume on pointerup is
  // instant — one frame later the next tick runs full updateMarker work.
  if (_touchPauseDepth > 0) {
    videoState._rafId = requestAnimationFrame(_videoRafLoop);
    return;
  }
  const rafScale = videoState.timeScale || 1;
  const nd = lookupNormPosForTms(videoState.element.currentTime * 1000 * rafScale);
  if (nd != null) {
    updateMarker(Math.max(0, Math.min(1, nd)), true, 'video');
  }
  videoState._rafId = requestAnimationFrame(_videoRafLoop);
}

export function _startVideoSync(_v) {
  if (videoState._rafId == null) {
    videoState._rafId = requestAnimationFrame(_videoRafLoop);
  }
}

export function _stopVideoSync() {
  if (videoState._rafId != null) {
    cancelAnimationFrame(videoState._rafId);
    videoState._rafId = null;
  }
}

// ---------------------------------------------------------------------------
// One-shot wiring of the <video> element. Called once from the bootstrap.
// Named without the leading underscore because it's a public surface.
// ---------------------------------------------------------------------------

export function wireVideoElement() {
  const v = document.getElementById('video-player');
  if (!v) return;
  videoState.element = v;

  v.addEventListener('play', () => {
    videoState.isPlaying = true;
    _startVideoSync(v);
  });
  v.addEventListener('pause', () => {
    videoState.isPlaying = false;
    _stopVideoSync();
  });
  v.addEventListener('ended', () => {
    videoState.isPlaying = false;
    _stopVideoSync();
  });

  // While paused, native HTML5 video controls (or the user's marker drag)
  // can step the frame; reflect any seek in the marker so the dot follows.
  v.addEventListener('seeked', () => {
    // Seek coalesce drain (round 4): while the previous seek was in flight,
    // syncVideoFromMarker may have stashed newer drag targets in
    // _pendingSeekTime. Apply the latest one now (only if it still differs
    // by more than the 15ms threshold from where we landed). This caps the
    // browser-visible seek count at ~2 per drag burst regardless of how
    // many marker events fired in between.
    const pending = videoState._pendingSeekTime;
    if (pending != null) {
      videoState._pendingSeekTime = null;
      if (Math.abs(v.currentTime - pending) > 0.015) {
        v.currentTime = pending;
      }
    }
    if (videoState.isPlaying || !videoState.frames) return;
    const seekScale = videoState.timeScale || 1;
    const nd = lookupNormPosForTms(v.currentTime * 1000 * seekScale);
    if (nd == null) return;
    updateMarker(Math.max(0, Math.min(1, nd)), true, 'video');
  });

  _wireTouchPauseListeners();
  _wireVideoEventInstrumentation(v);
}

// ---------------------------------------------------------------------------
// Touch-pause: capture-phase pointer/touch listeners on the document increment
// `_touchPauseDepth` so the rAF loop body short-circuits while any finger is
// down (option 3 in the touch-responsiveness discussion doc).
//
// Capture phase is critical: we must observe pointerdown before any
// stopPropagation()/preventDefault() further down the tree (e.g. the
// `#btn-video-float` carve-out from option 4) can swallow the event. We never
// preventDefault here ourselves — we only count.
// ---------------------------------------------------------------------------
function _wireTouchPauseListeners() {
  if (typeof document === 'undefined' || _touchPauseListenersWired) return;
  _touchPauseListenersWired = true;

  const onDown = () => {
    _touchPauseDepth++;
    if (_touchPauseDepth === 1) debugLog('[touch] paused rAF');
  };
  const onUp = () => {
    if (_touchPauseDepth > 0) _touchPauseDepth--;
    if (_touchPauseDepth === 0) debugLog('[touch] resumed rAF');
  };

  // pointer* events fire on all modern browsers (incl. iPadOS Safari ≥13).
  // touch* listeners are belt-and-braces in case a browser fires touchstart
  // without a corresponding pointerdown (older Android WebViews). Both
  // increment the same counter; double-counting is fine because both
  // counterparts also fire on release, keeping the depth balanced.
  document.addEventListener('pointerdown', onDown, { capture: true, passive: true });
  document.addEventListener('pointerup', onUp, { capture: true, passive: true });
  document.addEventListener('pointercancel', onUp, { capture: true, passive: true });
  document.addEventListener('touchstart', onDown, { capture: true, passive: true });
  document.addEventListener('touchend', onUp, { capture: true, passive: true });
  document.addEventListener('touchcancel', onUp, { capture: true, passive: true });

  // Watchdog: if the OS swallows a release (rare — happens on iOS when an
  // alert pops over the page mid-touch), tab visibility changing back forces
  // the depth to 0 so the marker can never permanently freeze.
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible' && _touchPauseDepth !== 0) {
      _touchPauseDepth = 0;
      debugLog('[touch] watchdog reset depth=0');
    }
  });
}
let _touchPauseListenersWired = false;

// ---------------------------------------------------------------------------
// `<video>` event instrumentation (option E in the discussion doc).
//
// Logs every relevant lifecycle / network / decode event to the on-page
// debug overlay. The pair we care most about is `seeking` → `seeked`: the
// elapsed delta is the metric that distinguishes "browser instantly served
// from buffer" from "browser is round-tripping a fresh Range request",
// which is the back-and-forth scrub stall pattern from §"forward-then-
// backward seek stalls" in the same discussion doc.
//
// All log lines are routed through `debugLog`, which is a no-op unless the
// overlay is active (`?debug=1` or `localStorage.te-debug = '1'`), so this
// has zero runtime cost in normal use.
// ---------------------------------------------------------------------------
function _formatBuffered(v) {
  try {
    const b = v.buffered;
    if (!b || b.length === 0) return '[]';
    const parts = [];
    for (let i = 0; i < b.length; i++) {
      parts.push(`(${b.start(i).toFixed(2)},${b.end(i).toFixed(2)})`);
    }
    return '[' + parts.join(',') + ']';
  } catch (_) {
    return '[?]';
  }
}

function _logVideoEvent(v, name) {
  try {
    const t = Number.isFinite(v.currentTime) ? v.currentTime.toFixed(3) : '?';
    debugLog(
      `[video] ${name} t=${t}s buffered=${_formatBuffered(v)} readyState=${v.readyState} networkState=${v.networkState}`,
    );
  } catch (_) {
    /* never throw from a logger */
  }
}

function _wireVideoEventInstrumentation(v) {
  const events = [
    'stalled',
    'waiting',
    'error',
    'loadstart',
    'loadedmetadata',
    'canplay',
    'canplaythrough',
    'ratechange',
    'pause',
    'play',
    'emptied',
    'ended',
  ];
  for (const ev of events) {
    v.addEventListener(ev, () => _logVideoEvent(v, ev));
  }

  // seeking ↔ seeked timing. performance.now() is monotonic so even across
  // tab-throttled rAF ticks the delta is the real wall-clock duration the
  // browser spent recovering buffer / decoding the keyframe.
  let _seekStartedAt = null;
  v.addEventListener('seeking', () => {
    _seekStartedAt = performance.now();
    _logVideoEvent(v, 'seeking');
  });
  v.addEventListener('seeked', () => {
    if (_seekStartedAt != null) {
      const dt = (performance.now() - _seekStartedAt).toFixed(1);
      debugLog(`[video] seek-stall ${dt}ms`);
      _seekStartedAt = null;
    }
    _logVideoEvent(v, 'seeked');
  });
}

// Expose updateMarker globally so the non-module track-map.js can call it
// at the end of renderTrackMap() to paint the initial position dot.
// Without this, track-map.js's `updateMarker(markerPosition, true)` fails
// with ReferenceError because ES-module exports are scoped to their module.
window.updateMarker = updateMarker;
