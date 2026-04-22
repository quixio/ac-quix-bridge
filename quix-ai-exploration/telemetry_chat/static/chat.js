/**
 * Chat UI glue. Submits prompts to /api/plot, renders assistant replies
 * (plot | clarify | error), hooks clarify chips and suggestion chips back
 * into the prompt flow.
 */

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
  r.textContent = role === "user" ? "you" : role === "assistant" ? "ai" : "error";
  const body = document.createElement("div");
  body.className = "body";
  body.textContent = text;
  div.appendChild(r);
  div.appendChild(body);
  els.messages.appendChild(div);
  scrollBottom(els.messages);
  return div;
}

/** @returns {HTMLElement} */
function addThinking() {
  const div = document.createElement("div");
  div.className = "msg assistant thinking";
  div.textContent = "thinking";
  els.messages.appendChild(div);
  scrollBottom(els.messages);
  return div;
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
 * @typedef {Object} ClarifyResponse
 * @property {"clarify"} type
 * @property {string} session_id
 * @property {string} question
 * @property {string[]} options
 */
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
 * @typedef {Object} PlotResponse
 * @property {"plot"} type
 * @property {string} session_id
 * @property {string} title
 * @property {string=} track
 * @property {Chart[]} charts
 */

async function submit() {
  const text = els.prompt.value.trim();
  if (!text) return;
  els.prompt.value = "";
  els.send.disabled = true;

  addMessage("user", text);
  const thinking = addThinking();

  try {
    const res = await fetch("/api/plot", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, session_id: sessionId }),
    });
    thinking.remove();

    if (!res.ok) {
      const detail = await res.text();
      addMessage("error", `Backend error (${res.status}): ${detail.slice(0, 400)}`);
      return;
    }

    const data = /** @type {ClarifyResponse | PlotResponse} */ (await res.json());
    sessionId = data.session_id;

    if (data.type === "clarify") {
      const msg = addMessage("assistant", data.question);
      addClarifyChips(data.options || [], msg);
      return;
    }

    if (data.type === "plot") {
      const signals = data.charts.map((c) => c.signal).join(", ");
      const counts = data.charts.map((c) => c.traces.length);
      const min = Math.min(...counts);
      const max = Math.max(...counts);
      const countStr = min === max ? `${max}` : `${min}–${max}`;
      const summary =
        data.charts.length === 1
          ? `Plotted ${max} trace(s) of ${signals}.`
          : `Plotted ${data.charts.length} charts (${signals}), ${countStr} trace(s) per chart.`;
      addMessage("assistant", summary);
      els.emptyState.classList.add("hidden");
      els.plotTitle.textContent = data.title || "";
      els.plotTitle.classList.toggle("visible", Boolean(data.title));
      renderCharts(els.plot, data.charts);
    }
  } catch (err) {
    thinking.remove();
    addMessage("error", `Network error: ${/** @type {Error} */ (err).message}`);
  } finally {
    els.send.disabled = false;
    els.prompt.focus();
  }
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

els.send.addEventListener("click", submit);
els.newChat.addEventListener("click", newChat);
els.prompt.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    submit();
  }
});
for (const btn of document.querySelectorAll("#empty-state .suggestions button")) {
  btn.addEventListener("click", () => {
    const prompt = /** @type {HTMLButtonElement} */ (btn).dataset.prompt;
    if (!prompt) return;
    els.prompt.value = prompt;
    submit();
  });
}

els.prompt.focus();
