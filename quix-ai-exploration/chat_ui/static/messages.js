// Pure message-rendering helpers. No direct DOM lookups — callers pass the container.
// Kept separate from chat.js so it's trivially testable with vitest + happy-dom.

const TEXT_ROLES = new Set(["user", "assistant"]);

export function buildMessageEl(m) {
  const role = (m.role || "").toLowerCase();

  if (role === "system") {
    if (m.synthetic) return null;
    const el = document.createElement("div");
    el.className = "msg system";
    el.textContent = m.content || "";
    return el;
  }

  if (!TEXT_ROLES.has(role)) return null;

  const el = document.createElement("div");
  el.className = `msg ${role}`;
  const roleEl = document.createElement("div");
  roleEl.className = "role";
  roleEl.textContent = role;
  const body = document.createElement("div");
  body.className = "body";
  body.textContent = m.content || "";
  el.append(roleEl, body);
  return el;
}

export function renderBatch(container, messages, { prepend = false } = {}) {
  const frag = document.createDocumentFragment();
  for (const m of messages) {
    const el = buildMessageEl(m);
    if (el) frag.append(el);
  }
  if (!frag.childNodes.length) return;
  if (prepend) container.insertBefore(frag, container.firstChild);
  else container.append(frag);
}

export function sortAscending(messages) {
  return [...messages].sort((a, b) => (a.sequenceNumber ?? 0) - (b.sequenceNumber ?? 0));
}
