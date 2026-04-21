import { streamSSE } from "/static/sse.js";

const log = document.getElementById("log");
const input = document.getElementById("input");
const sendBtn = document.getElementById("send");
const sessionEl = document.getElementById("session");

let sessionId = null;

const NEAR_BOTTOM_PX = 80;

function isNearBottom() {
  return log.scrollHeight - log.scrollTop - log.clientHeight < NEAR_BOTTOM_PX;
}

function scrollIfFollowing(wasFollowing) {
  if (wasFollowing) log.scrollTop = log.scrollHeight;
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
  body.textContent = text;
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

function handleEvent(evt, assistantBody) {
  if (evt.data === "[DONE]") return;

  if (evt.event === "session") {
    try {
      sessionId = JSON.parse(evt.data).session_id;
      sessionEl.textContent = `session ${sessionId.slice(0, 8)}…`;
    } catch {}
    return;
  }
  if (evt.event === "error") {
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
      assistantBody.textContent += payload.text || "";
      scrollIfFollowing(following);
      break;
    }
    case "session_title":
      addSystem(`session title: ${payload.title}`);
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

  let started = false;
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
    started = true;
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
    void started;
  }
}

sendBtn.addEventListener("click", send);
input.addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) {
    e.preventDefault();
    send();
  }
});
