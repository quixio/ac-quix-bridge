"use strict";

// All API calls use RELATIVE URLs (no leading slash) so a path-rewriting
// ingress prefix (e.g. /track-viewer/) does not break them.
const API_BASE = "api/tracks";

// Radius (m) at/above which a segment is treated as "straight" (full green).
// Capped so a single long straight does not wash out the colour scale.
const RADIUS_CAP_M = 400;

const els = {
  banner: document.getElementById("status-banner"),
  list: document.getElementById("layout-list"),
  title: document.getElementById("map-title"),
  meta: document.getElementById("map-meta"),
  note: document.getElementById("map-note"),
  message: document.getElementById("map-message"),
  canvas: document.getElementById("map-canvas"),
};

let currentGeometry = null;

function showBanner(text, kind) {
  els.banner.className = `alert alert-${kind} py-2 small`;
  els.banner.textContent = text;
}

function hideBanner() {
  els.banner.className = "alert alert-info py-2 small d-none";
}

function showMapMessage(text, kind) {
  els.message.className = `alert alert-${kind || "secondary"}`;
  els.message.textContent = text;
}

function hideMapMessage() {
  els.message.className = "alert alert-secondary d-none";
}

function fmtKm(lengthM) {
  if (typeof lengthM !== "number") return "?";
  return (lengthM / 1000).toFixed(2) + " km";
}

async function loadTracks() {
  let resp;
  try {
    resp = await fetch(API_BASE, { headers: { Accept: "application/json" } });
  } catch (err) {
    showBanner("Cannot reach MongoDB: network error (" + err + ").", "danger");
    return;
  }
  if (resp.status === 503) {
    let detail = "service unavailable";
    try {
      detail = (await resp.json()).error || detail;
    } catch (_) {}
    showBanner("Cannot reach MongoDB: " + detail, "danger");
    return;
  }
  if (!resp.ok) {
    showBanner("Failed to load tracks (HTTP " + resp.status + ").", "danger");
    return;
  }

  const tracks = await resp.json();
  if (!Array.isArray(tracks) || tracks.length === 0) {
    showBanner(
      "No tracks found in test_manager.track_layouts. The import may not have run.",
      "warning"
    );
    return;
  }
  hideBanner();
  renderSidebar(tracks);
}

function renderSidebar(tracks) {
  els.list.innerHTML = "";
  for (const t of tracks) {
    const row = document.createElement("button");
    row.type = "button";
    row.className =
      "list-group-item list-group-item-action layout-row d-flex flex-column align-items-start";
    row.dataset.id = t._id;

    const titleLine = document.createElement("div");
    titleLine.className = "fw-semibold";
    const showConfig =
      t.trackConfiguration && t.trackConfiguration !== t.layout;
    titleLine.textContent = `${t.track} / ${t.layout}`;
    if (showConfig) {
      const cfg = document.createElement("span");
      cfg.className = "text-secondary fw-normal small ms-1";
      cfg.textContent = `(${t.trackConfiguration})`;
      titleLine.appendChild(cfg);
    }

    const badges = document.createElement("div");
    badges.className = "mt-1";
    badges.innerHTML =
      `<span class="badge text-bg-secondary me-1">${fmtKm(t.length_m)}</span>` +
      `<span class="badge text-bg-light me-1">${t.n_points} pts</span>` +
      `<span class="badge text-bg-light">${t.n_corners} corners</span>`;

    row.appendChild(titleLine);
    row.appendChild(badges);
    row.addEventListener("click", () => selectLayout(t._id, row));
    els.list.appendChild(row);
  }
}

async function selectLayout(id, rowEl) {
  document
    .querySelectorAll(".layout-row.active")
    .forEach((e) => e.classList.remove("active"));
  if (rowEl) rowEl.classList.add("active");

  hideMapMessage();
  els.title.textContent = "Loading…";
  els.meta.textContent = "";
  els.note.textContent = "";

  // Relative path; encode each id segment but keep the slash between
  // track and layout (the {id:path} param accepts the embedded slash).
  const encoded = id
    .split("/")
    .map(encodeURIComponent)
    .join("/");
  let resp;
  try {
    resp = await fetch(`${API_BASE}/${encoded}/geometry`);
  } catch (err) {
    showMapMessage("Cannot reach MongoDB: network error (" + err + ").", "danger");
    return;
  }
  if (!resp.ok) {
    let detail = "HTTP " + resp.status;
    try {
      detail = (await resp.json()).error || detail;
    } catch (_) {}
    showMapMessage("Could not load geometry: " + detail, "danger");
    els.title.textContent = "Error";
    return;
  }

  const geo = await resp.json();
  currentGeometry = geo;
  els.title.textContent = `${geo.track} / ${geo.layout}`;
  const cfg =
    geo.trackConfiguration && geo.trackConfiguration !== geo.layout
      ? ` · config: ${geo.trackConfiguration}`
      : "";
  els.meta.textContent =
    `${fmtKm(geo.length_m)} · ${geo.n_points} pts · ${geo.n_corners} corners${cfg}`;
  els.note.textContent = geo.downsampled
    ? `downsampled to ${geo.n_points_returned} pts for display`
    : "";

  if (!geo.points || geo.points.length === 0) {
    showMapMessage("This layout has no point geometry to render.", "warning");
    clearCanvas();
    return;
  }
  hideMapMessage();
  drawTrack(geo.points);
}

// Map a radius (m) to a red→yellow→green colour, capped at RADIUS_CAP_M and
// scaled over the layout's own observed radius range (winsorized at the cap).
function radiusColor(radius, minR, maxR) {
  let r = typeof radius === "number" && isFinite(radius) ? radius : maxR;
  r = Math.min(r, RADIUS_CAP_M);
  const lo = Math.min(minR, RADIUS_CAP_M);
  const hi = Math.min(maxR, RADIUS_CAP_M);
  let t = hi > lo ? (r - lo) / (hi - lo) : 1;
  t = Math.max(0, Math.min(1, t));
  // 0 = tight (red), 0.5 = yellow, 1 = straight (green).
  let red, green;
  if (t < 0.5) {
    red = 220;
    green = Math.round(40 + (200 - 40) * (t / 0.5));
  } else {
    red = Math.round(220 - (220 - 40) * ((t - 0.5) / 0.5));
    green = 200;
  }
  return `rgb(${red},${green},60)`;
}

function clearCanvas() {
  const ctx = els.canvas.getContext("2d");
  ctx.clearRect(0, 0, els.canvas.width, els.canvas.height);
}

function drawTrack(points) {
  const canvas = els.canvas;
  // Size the backing store to the displayed CSS size (devicePixelRatio aware).
  const dpr = window.devicePixelRatio || 1;
  const cssW = canvas.clientWidth;
  const cssH = canvas.clientHeight;
  canvas.width = Math.round(cssW * dpr);
  canvas.height = Math.round(cssH * dpr);
  const ctx = canvas.getContext("2d");
  ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
  ctx.clearRect(0, 0, cssW, cssH);

  // Bounds over raw x (horizontal) and z (vertical).
  let minX = Infinity,
    maxX = -Infinity,
    minZ = Infinity,
    maxZ = -Infinity,
    minR = Infinity,
    maxR = -Infinity;
  for (const [x, z, r] of points) {
    if (x < minX) minX = x;
    if (x > maxX) maxX = x;
    if (z < minZ) minZ = z;
    if (z > maxZ) maxZ = z;
    if (typeof r === "number" && isFinite(r)) {
      if (r < minR) minR = r;
      if (r > maxR) maxR = r;
    }
  }
  if (!isFinite(minR)) {
    minR = 0;
    maxR = RADIUS_CAP_M;
  }

  const pad = 24;
  const spanX = maxX - minX || 1;
  const spanZ = maxZ - minZ || 1;
  // Equal aspect ratio: one scale for both axes.
  const scale = Math.min(
    (cssW - 2 * pad) / spanX,
    (cssH - 2 * pad) / spanZ
  );
  const offX = (cssW - spanX * scale) / 2;
  const offZ = (cssH - spanZ * scale) / 2;
  // Flip z so increasing z draws upward (screen y grows downward).
  const toX = (x) => offX + (x - minX) * scale;
  const toY = (z) => cssH - (offZ + (z - minZ) * scale);

  // Per-segment polyline coloured by the segment's start radius.
  ctx.lineWidth = 2.5;
  ctx.lineCap = "round";
  ctx.lineJoin = "round";
  for (let i = 1; i < points.length; i++) {
    const [x0, z0, r0] = points[i - 1];
    const [x1, z1] = points[i];
    ctx.strokeStyle = radiusColor(r0, minR, maxR);
    ctx.beginPath();
    ctx.moveTo(toX(x0), toY(z0));
    ctx.lineTo(toX(x1), toY(z1));
    ctx.stroke();
  }

  // Start marker at points[0].
  const [sx, sz] = points[0];
  ctx.fillStyle = "#ffffff";
  ctx.strokeStyle = "#000000";
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  ctx.arc(toX(sx), toY(sz), 6, 0, 2 * Math.PI);
  ctx.fill();
  ctx.stroke();
  ctx.fillStyle = "#ffffff";
  ctx.font = "12px sans-serif";
  ctx.fillText("start", toX(sx) + 9, toY(sz) - 6);
}

// Redraw on resize so the equal-aspect map keeps fitting.
let resizeTimer = null;
window.addEventListener("resize", () => {
  if (!currentGeometry || !currentGeometry.points) return;
  clearTimeout(resizeTimer);
  resizeTimer = setTimeout(() => drawTrack(currentGeometry.points), 120);
});

loadTracks();
