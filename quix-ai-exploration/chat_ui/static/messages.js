// Pure message-rendering helpers. No direct DOM lookups — callers pass the container.
// Kept separate from chat.js so it's trivially testable with vitest + happy-dom.

import { renderMarkdown } from "./markdown.js";

const TEXT_ROLES = new Set(["user", "assistant"]);

function safeParseJSON(s) {
  if (typeof s !== "string") return s;
  try {
    return JSON.parse(s);
  } catch {
    return null;
  }
}

function prettyJSON(val) {
  if (typeof val === "string") {
    const parsed = safeParseJSON(val);
    if (parsed !== null) return JSON.stringify(parsed, null, 2);
    return val;
  }
  return JSON.stringify(val, null, 2);
}

export function buildToolResultMap(messages) {
  const map = new Map();
  for (const m of messages) {
    if ((m.role || "").toLowerCase() !== "tool") continue;
    for (const b of m.contentBlocks || []) {
      if (b.type === "tool_result" && b.toolCallId) map.set(b.toolCallId, b);
    }
  }
  return map;
}

function buildToolPill(call, result) {
  const args = safeParseJSON(call.arguments) || {};
  const subject = call.subjectField && typeof args === "object" ? args[call.subjectField] : null;
  const isError = result?.isError === true;
  const status = !result ? "…" : isError ? "✗" : "✓";

  const details = document.createElement("details");
  details.className = `tool${isError ? " tool-error" : ""}`;

  const summary = document.createElement("summary");
  const statusSpan = document.createElement("span");
  statusSpan.className = "tool-status";
  statusSpan.textContent = status;
  const nameCode = document.createElement("code");
  nameCode.className = "tool-name";
  nameCode.textContent = call.displayName || call.toolName || "tool";
  summary.append(statusSpan, nameCode);
  if (subject != null && subject !== "") {
    const subj = document.createElement("span");
    subj.className = "tool-subject";
    subj.textContent = `· ${subject}`;
    summary.append(subj);
  }
  details.append(summary);

  const inputDetails = document.createElement("details");
  inputDetails.className = "tool-input";
  const inputSummary = document.createElement("summary");
  inputSummary.textContent = "Show input";
  inputDetails.append(inputSummary);
  const inputPre = document.createElement("pre");
  const inputCode = document.createElement("code");
  inputCode.textContent = prettyJSON(call.arguments);
  inputPre.append(inputCode);
  inputDetails.append(inputPre);
  details.append(inputDetails);

  if (result) {
    if (result.userSummary) {
      const header = document.createElement("div");
      header.className = "tool-result-header";
      header.textContent = result.userSummary;
      details.append(header);
    }
    const resultDiv = document.createElement("div");
    resultDiv.className = "tool-result";
    const raw = typeof result.result === "string" ? result.result : prettyJSON(result.result);
    resultDiv.innerHTML = renderMarkdown(raw);
    details.append(resultDiv);
  }

  return details;
}

function buildBlockEl(block, toolResults) {
  if (!block || !block.type) return null;
  if (block.type === "text") {
    const wrap = document.createElement("div");
    wrap.className = "block-text";
    wrap.innerHTML = renderMarkdown(block.text || "");
    return wrap;
  }
  if (block.type === "tool_use") {
    return buildToolPill(block, toolResults?.get(block.toolCallId));
  }
  return null;
}

export function buildMessageEl(m, { toolResults } = {}) {
  const role = (m.role || "").toLowerCase();

  if (role === "tool") return null;

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

  const blocks = m.contentBlocks;
  if (Array.isArray(blocks) && blocks.length) {
    for (const b of blocks) {
      const blockEl = buildBlockEl(b, toolResults);
      if (blockEl) body.append(blockEl);
    }
    if (!body.childNodes.length) return null;
  } else {
    body.dataset.raw = m.content || "";
    body.innerHTML = renderMarkdown(m.content || "");
  }

  el.append(roleEl, body);
  return el;
}

export function renderBatch(container, messages, { prepend = false } = {}) {
  const toolResults = buildToolResultMap(messages);
  const frag = document.createDocumentFragment();
  for (const m of messages) {
    const el = buildMessageEl(m, { toolResults });
    if (el) frag.append(el);
  }
  if (!frag.childNodes.length) return;
  if (prepend) container.insertBefore(frag, container.firstChild);
  else container.append(frag);
}

export function sortAscending(messages) {
  return [...messages].sort((a, b) => (a.sequenceNumber ?? 0) - (b.sequenceNumber ?? 0));
}
