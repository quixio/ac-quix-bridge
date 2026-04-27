/**
 * Chat UI glue. Submits prompts to /api/plot, renders assistant replies
 * (plot | clarify | error), hooks clarify chips and suggestion chips back
 * into the prompt flow.
 */

import { renderMarkdown } from "./markdown.js";
import { clearCharts, renderCharts } from "./plot.js";

/** @type {string | null} */
let sessionId = null;

const els = {
  prompt: /** @type {HTMLTextAreaElement} */ (document.getElementById("prompt")),
  send: /** @type {HTMLButtonElement} */ (document.getElementById("send")),
  newChat: /** @type {HTMLButtonElement} */ (document.getElementById("new-chat")),
  messages: /** @type {HTMLElement} */ (document.getElementById("messages")),
  plot: /** @type {HTMLElement} */ (document.getElementById("plot")),
  plotTitle: /** @type {HTMLElement} */ (document.getElementById("plot-title")),
  emptyState: /** @type {HTMLElement} */ (document.getElementById("empty-state")),
  app: /** @type {HTMLElement} */ (document.querySelector(".app")),
  divider: /** @type {HTMLElement} */ (document.getElementById("divider")),
  dockToggle: /** @type {HTMLButtonElement} */ (document.getElementById("dock-toggle")),
};

/** @param {HTMLElement} el */
function scrollBottom(el) {
  el.scrollTop = el.scrollHeight;
}

/**
 * @param {"user" | "assistant" | "error"} role
 * @param {string} text
 * @returns {HTMLElement}
 */
function addMessage(role, text) {
  const div = document.createElement("div");
  div.className = `msg ${role}`;
  const r = document.createElement("div");
  r.className = "role";
  r.textContent = role === "user" ? "you" : role === "assistant" ? "Quix AI" : "error";
  const body = document.createElement("div");
  body.className = "body";
  if (role === "assistant") {
    body.dataset.raw = text;
    body.innerHTML = renderMarkdown(text);
  } else {
    body.textContent = text;
  }
  div.appendChild(r);
  div.appendChild(body);
  els.messages.appendChild(div);
  scrollBottom(els.messages);
  return div;
}

/**
 * Show a spinner + label as a transient assistant row in the chat pane.
 * Reuses the same `#progress` node across status events so the label updates
 * in place. Cleared by `hideProgress` once a non-status event arrives, or by
 * the next turn's `showProgress` resetting it. Plot pane is left untouched
 * so prior charts remain visible during follow-up Mode 2/3 conversations.
 * @param {string} label
 * @param {number=} done
 * @param {number=} total
 */
function showProgress(label, done, total) {
  let prog = /** @type {HTMLElement | null} */ (document.getElementById("progress"));
  if (!prog) {
    prog = document.createElement("div");
    prog.id = "progress";
    prog.className = "msg assistant";
    prog.innerHTML =
      '<div class="role">Quix AI</div>' +
      '<div class="body"><span class="spinner"></span><span class="label"></span></div>';
    els.messages.appendChild(prog);
  }
  const labelEl = /** @type {HTMLElement} */ (prog.querySelector(".label"));
  const parts = [label];
  if (typeof done === "number" && typeof total === "number") {
    parts.push(`${done}/${total} traces`);
  }
  labelEl.textContent = parts.join(" — ");
  scrollBottom(els.messages);
}

function hideProgress() {
  document.getElementById("progress")?.remove();
}

/**
 * @param {string[]} options
 * @param {HTMLElement} messageEl
 */
function addClarifyChips(options, messageEl) {
  const wrap = document.createElement("div");
  wrap.className = "clarify-options";
  for (const opt of options) {
    const b = document.createElement("button");
    b.type = "button";
    b.textContent = opt;
    b.addEventListener("click", () => {
      els.prompt.value = opt;
      submit();
    });
    wrap.appendChild(b);
  }
  messageEl.appendChild(wrap);
}

/**
 * @typedef {Object} PlotTrace
 * @property {string} session_id
 * @property {number} lap
 * @property {string=} driver
 * @property {string=} carModel
 * @property {string=} track
 * @property {string=} experiment
 * @property {number[]} x
 * @property {(number|null)[]} y
 * @property {number} count
 */
/**
 * @typedef {Object} Chart
 * @property {string} signal
 * @property {PlotTrace[]} traces
 */
/**
 * @typedef {Object} PlotEvent
 * @property {"plot"} event
 * @property {string} session_id
 * @property {string} title
 * @property {string|null=} track
 * @property {Chart[]} charts
 */
/**
 * @typedef {Object} AnswerDeltaEvent
 * @property {"answer_delta"} event
 * @property {string} session_id
 * @property {string} text
 */
/**
 * @typedef {Object} ClarifyEvent
 * @property {"clarify"} event
 * @property {string} session_id
 * @property {string} question
 * @property {string[]} options
 */
/**
 * @typedef {Object} StatusEvent
 * @property {"status"} event
 * @property {string=} session_id
 * @property {string} message
 * @property {number=} done
 * @property {number=} total
 */
/**
 * @typedef {Object} ErrorEvent
 * @property {"error"} event
 * @property {string=} session_id
 * @property {string} detail
 * @property {number=} status
 */

/** Resize the textarea to fit its content, up to the CSS max-height. */
function autoResize() {
  els.prompt.style.height = "auto";
  els.prompt.style.height = `${els.prompt.scrollHeight}px`;
}

/** Send is grey + disabled until there's trimmed input AND no in-flight request. */
let sending = false;
function refreshSendState() {
  els.send.disabled = sending || !els.prompt.value.trim();
}

async function submit() {
  const text = els.prompt.value.trim();
  if (!text || sending) return;
  sending = true;
  els.prompt.value = "";
  autoResize();
  refreshSendState();

  activeAnswer = null;
  addMessage("user", text);
  showProgress("Looking up sessions");

  try {
    const res = await fetch("/api/plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });

    if (!res.ok || !res.body) {
      hideProgress();
      const detail = await res.text();
      addMessage("error", `Backend error (${res.status}): ${detail.slice(0, 400)}`);
      return;
    }

    await readEventStream(res.body);
  } catch (err) {
    hideProgress();
    addMessage("error", `Network error: ${/** @type {Error} */ (err).message}`);
  } finally {
    sending = false;
    refreshSendState();
    els.prompt.focus();
  }
}

/**
 * Read newline-delimited JSON events from the /api/plot response body.
 * Each complete line is dispatched to `handleEvent` as it arrives.
 * @param {ReadableStream<Uint8Array>} body
 */
async function readEventStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split("\n");
    // The last fragment may be incomplete — keep it for the next read.
    buffer = lines.pop() ?? "";
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed) parseAndHandle(trimmed);
    }
  }
  const tail = buffer.trim();
  if (tail) parseAndHandle(tail);
}

/** @param {string} line */
function parseAndHandle(line) {
  try {
    handleEvent(JSON.parse(line));
  } catch (e) {
    console.error("malformed event:", e, line.slice(0, 200));
  }
}

/**
 * Per-turn assistant bubble accumulating `answer_delta` chunks. Reset between
 * turns when status reopens, or when clarify/plot/error close out the turn.
 * @type {HTMLElement | null}
 */
let activeAnswer = null;

/** @type {Set<HTMLElement>} */
const pendingRender = new Set();
let renderScheduled = false;

/** @param {HTMLElement} body */
function scheduleRender(body) {
  pendingRender.add(body);
  if (renderScheduled) return;
  renderScheduled = true;
  requestAnimationFrame(() => {
    renderScheduled = false;
    for (const el of pendingRender) {
      el.innerHTML = renderMarkdown(el.dataset.raw || "");
    }
    pendingRender.clear();
    scrollBottom(els.messages);
  });
}

/**
 * @param {StatusEvent | AnswerDeltaEvent | ClarifyEvent | PlotEvent | ErrorEvent} evt
 */
function handleEvent(evt) {
  if (evt.session_id) sessionId = evt.session_id;
  switch (evt.event) {
    case "status":
      activeAnswer = null;
      showProgress(evt.message, evt.done, evt.total);
      break;
    case "answer_delta": {
      hideProgress();
      if (!activeAnswer) {
        activeAnswer = addMessage("assistant", "");
      }
      const body = /** @type {HTMLElement} */ (activeAnswer.querySelector(".body"));
      body.dataset.raw = (body.dataset.raw || "") + evt.text;
      scheduleRender(body);
      break;
    }
    case "clarify": {
      hideProgress();
      activeAnswer = null;
      const msg = addMessage("assistant", evt.question);
      addClarifyChips(evt.options || [], msg);
      break;
    }
    case "plot":
      hideProgress();
      activeAnswer = null;
      renderPlotEvent(evt);
      break;
    case "error":
      hideProgress();
      activeAnswer = null;
      addMessage("error", `${evt.detail}${evt.status ? ` (${evt.status})` : ""}`.slice(0, 500));
      break;
  }
}

/** @param {PlotEvent} data */
async function renderPlotEvent(data) {
  els.emptyState.classList.add("hidden");
  els.plotTitle.textContent = data.title || "";
  els.plotTitle.classList.toggle("visible", Boolean(data.title));
  await renderCharts(els.plot, data.charts);
}

function newChat() {
  sessionId = null;
  els.messages.innerHTML = "";
  els.emptyState.classList.remove("hidden");
  els.plotTitle.classList.remove("visible");
  els.plotTitle.textContent = "";
  clearCharts(els.plot);
  els.prompt.focus();
}

// Layout: draggable divider + dock toggle.
const CHAT_MIN_PX = 140;
const CHAT_MAX_RATIO = 0.75;

/**
 * Resize all Plotly charts inside the plot pane. Called after a divider drag
 * ends so charts re-layout to the new container size. responsive:true uses a
 * ResizeObserver internally, but an explicit call avoids a perceptible delay.
 */
function resizePlots() {
  for (const chart of Array.from(els.plot.children)) {
    // @ts-ignore – Plotly from CDN script tag.
    Plotly.Plots.resize(chart);
  }
}

els.divider.addEventListener("mousedown", (e) => {
  e.preventDefault();
  const vertical = els.app.classList.contains("dock-right");
  const startPos = vertical ? e.clientX : e.clientY;
  const startSize = parseFloat(getComputedStyle(els.app).getPropertyValue("--chat-size"));
  const viewport = vertical ? window.innerWidth : window.innerHeight;
  const maxSize = viewport * CHAT_MAX_RATIO;
  const onMove = /** @param {MouseEvent} ev */ (ev) => {
    const delta = (vertical ? ev.clientX : ev.clientY) - startPos;
    // Dragging toward the chat shrinks it, away from it grows it.
    const next = Math.max(CHAT_MIN_PX, Math.min(maxSize, startSize - delta));
    els.app.style.setProperty("--chat-size", `${next}px`);
  };
  const onUp = () => {
    document.removeEventListener("mousemove", onMove);
    document.removeEventListener("mouseup", onUp);
    document.body.style.userSelect = "";
    els.divider.classList.remove("dragging");
    resizePlots();
  };
  document.body.style.userSelect = "none";
  els.divider.classList.add("dragging");
  document.addEventListener("mousemove", onMove);
  document.addEventListener("mouseup", onUp);
});

els.dockToggle.addEventListener("click", () => {
  const toRight = !els.app.classList.contains("dock-right");
  els.app.classList.toggle("dock-right", toRight);
  // Swap the divider's a11y orientation to match its cursor.
  els.divider.setAttribute("aria-orientation", toRight ? "vertical" : "horizontal");
  els.dockToggle.setAttribute("aria-label", toRight ? "Dock chat to bottom" : "Dock chat to right");
  // Each mode has a different sensible default size.
  els.app.style.setProperty("--chat-size", toRight ? "420px" : "240px");
  // Defer until layout settles so Plotly picks up the new container size.
  requestAnimationFrame(resizePlots);
});

els.send.addEventListener("click", submit);
els.newChat.addEventListener("click", newChat);
els.prompt.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submit();
  }
});
els.prompt.addEventListener("input", () => {
  autoResize();
  refreshSendState();
});
for (const btn of document.querySelectorAll("#empty-state .suggestions button")) {
  btn.addEventListener("click", () => {
    const prompt = /** @type {HTMLButtonElement} */ (btn).dataset.prompt;
    if (!prompt) return;
    els.prompt.value = prompt;
    autoResize();
    refreshSendState();
    els.prompt.focus();
  });
}

els.prompt.focus();
