import { streamSSE } from "/static/sse.js";
import { loadSessions, setActive } from "/static/sessions.js";
import { renderBatch, sortAscending } from "/static/messages.js";
import { renderMarkdown } from "/static/markdown.js";

const log = document.getElementById("log");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const sessionEl = document.getElementById("session");
const newChatBtn = document.getElementById("new-chat");
const refreshBtn = document.getElementById("refresh-sessions");

let sessionId = null;

const NEAR_BOTTOM_PX = 80;

function isNearBottom() {
  return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM_PX;
}

function scrollIfFollowing(wasFollowing) {
  if (wasFollowing) log.scrollTop = log.scrollHeight;
}

function clearLog() {
  log.innerHTML = "";
}

function setSessionId(id) {
  sessionId = id;
  sessionEl.textContent = id ? `session ${id.slice(0, 8)}…` : "no session yet";
  setActive(id);
}

function addMessage(role, text = "") {
  const following = isNearBottom();
  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role;
  const body = document.createElement("div");
  body.className = "body";
  body.dataset.raw = text;
  body.innerHTML = renderMarkdown(text);
  el.append(roleEl, body);
  log.append(el);
  scrollIfFollowing(following);
  return body;
}

function addSystem(text) {
  const following = isNearBottom();
  const el = document.createElement("div");
  el.className = "msg system";
  el.textContent = text;
  log.append(el);
  scrollIfFollowing(following);
}

const PAGE_SIZE = 20;
const SCROLL_TOP_THRESHOLD_PX = 60;
let oldestSeq = null;
let hasMoreOlder = false;
let loadingOlder = false;
let loadingIndicator = null;

function showLoadingIndicator() {
  if (loadingIndicator) return;
  loadingIndicator = document.createElement("div");
  loadingIndicator.className = "msg system older-loading";
  loadingIndicator.textContent = "Loading older messages…";
  log.prepend(loadingIndicator);
}

function hideLoadingIndicator() {
  if (loadingIndicator) loadingIndicator.remove();
  loadingIndicator = null;
}

async function loadOlder() {
  if (!sessionId || oldestSeq == null || !hasMoreOlder || loadingOlder) return;
  loadingOlder = true;
  showLoadingIndicator();
  try {
    const url = `/api/sessions/${encodeURIComponent(sessionId)}/messages?before=${oldestSeq}&limit=${PAGE_SIZE}`;
    const res = await fetch(url);
    if (!res.ok) {
      addSystem(`[error ${res.status}]`);
      hasMoreOlder = false;
      return;
    }
    const { messages = [], hasMore = false } = await res.json();
    const asc = sortAscending(messages);
    const prevScrollHeight = log.scrollHeight;
    const prevScrollTop = log.scrollTop;
    hideLoadingIndicator();
    renderBatch(log, asc, { prepend: true });
    if (asc.length) oldestSeq = asc[0].sequenceNumber;
    hasMoreOlder = hasMore;
    // keep the user's viewport anchored to the same message after prepend
    log.scrollTop = prevScrollTop + (log.scrollHeight - prevScrollHeight);
  } catch (err) {
    addSystem(`[fetch error] ${err}`);
    hasMoreOlder = false;
  } finally {
    hideLoadingIndicator();
    loadingOlder = false;
  }
}

function onScroll() {
  if (log.scrollTop < SCROLL_TOP_THRESHOLD_PX) loadOlder();
}

async function openSession(id) {
  try {
    setSessionId(id);
    clearLog();
    oldestSeq = null;
    hasMoreOlder = false;
    loadingOlder = false;
    loadingIndicator = null;

    const res = await fetch(`/api/sessions/${encodeURIComponent(id)}/messages?limit=${PAGE_SIZE}`);
    if (!res.ok) {
      addSystem(`[error loading session: ${res.status}]`);
      return;
    }
    const { messages = [], hasMore = false } = await res.json();
    const asc = sortAscending(messages);
    renderBatch(log, asc);
    if (asc.length) oldestSeq = asc[0].sequenceNumber;
    hasMoreOlder = hasMore;
    log.scrollTop = log.scrollHeight;
  } catch (err) {
    addSystem(`[fetch error] ${err}`);
  }
}

function startNewChat() {
  setSessionId(null);
  clearLog();
  input.focus();
}

function handleEvent(evt, assistantBody) {
  if (evt.data === "[DONE]") return;

  if (evt.event === "session") {
    try {
      const wasFresh = !sessionId;
      setSessionId(JSON.parse(evt.data).session_id);
      if (wasFresh) loadSessions(openSession, sessionId);
    } catch {}
    return;
  }
  if (evt.event === "error") {
    assistantBody.dataset.raw = `[error] ${evt.data}`;
    assistantBody.textContent = `[error] ${evt.data}`;
    return;
  }

  let payload;
  try {
    payload = JSON.parse(evt.data);
  } catch {
    return;
  }

  switch (payload.type) {
    case "text_delta": {
      const following = isNearBottom();
      assistantBody.dataset.raw = (assistantBody.dataset.raw || "") + (payload.text || "");
      assistantBody.innerHTML = renderMarkdown(assistantBody.dataset.raw);
      scrollIfFollowing(following);
      break;
    }
    case "session_title":
      addSystem(`session title: ${payload.title}`);
      loadSessions(openSession, sessionId);
      break;
    case "context_warning":
      addSystem(`context ${payload.usagePercent}% full`);
      break;
  }
}

async function send() {
  const message = input.value.trim();
  if (!message) return;
  sendBtn.disabled = true;
  addMessage("user", message);
  const assistantBody = addMessage("assistant", "");
  assistantBody.parentElement.classList.add("cursor");

  try {
    const res = await fetch("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message, session_id: sessionId }),
    });
    if (!res.ok || !res.body) {
      assistantBody.textContent = `[error ${res.status}]`;
      return;
    }
    input.value = "";
    for await (const evt of streamSSE(res)) {
      handleEvent(evt, assistantBody);
    }
  } catch (err) {
    assistantBody.textContent = `[fetch error] ${err}`;
  } finally {
    assistantBody.parentElement.classList.remove("cursor");
    sendBtn.disabled = false;
    input.focus();
  }
}

sendBtn.addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
newChatBtn.addEventListener("click", startNewChat);
refreshBtn.addEventListener("click", () => loadSessions(openSession, sessionId));
log.addEventListener("scroll", onScroll);

loadSessions(openSession, sessionId);
