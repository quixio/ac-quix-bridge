// Sidebar: list/fetch prior workspace sessions.

const listEl = document.getElementById("session-list");

const DATE_FMT = new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric" });

function plural(n, word) {
  return `${n} ${word}${n === 1 ? "" : "s"} ago`;
}

function relativeTime(iso) {
  if (!iso) return "";
  const then = new Date(iso);
  const diffSec = (Date.now() - then.getTime()) / 1000;
  if (Number.isNaN(diffSec)) return "";

  if (diffSec < 60) return "just now";
  if (diffSec < 3600) return plural(Math.round(diffSec / 60), "min");
  if (diffSec < 86400) return plural(Math.round(diffSec / 3600), "hour");

  const days = Math.round(diffSec / 86400);
  if (days === 1) return "yesterday";
  if (days < 7) return plural(days, "day");
  return DATE_FMT.format(then);
}

function renderEmpty(text) {
  listEl.innerHTML = `<li class="empty">${text}</li>`;
}

export function setActive(sessionId) {
  for (const el of listEl.querySelectorAll(".session-item")) {
    el.classList.toggle("active", el.dataset.id === sessionId);
  }
}

export async function loadSessions(onPick, activeId) {
  renderEmpty("loading…");
  let items;
  try {
    const res = await fetch("/api/sessions");
    if (!res.ok) {
      renderEmpty(`[error ${res.status}]`);
      return;
    }
    items = await res.json();
  } catch (err) {
    renderEmpty(`[fetch error]`);
    console.error(err);
    return;
  }

  if (!items.length) {
    renderEmpty("no sessions yet");
    return;
  }

  items.sort(
    (a, b) => new Date(b.lastActivityAt || b.createdAt) - new Date(a.lastActivityAt || a.createdAt),
  );

  listEl.innerHTML = "";
  for (const s of items) {
    const li = document.createElement("li");
    li.className = "session-item";
    li.dataset.id = s.id;
    if (s.id === activeId) li.classList.add("active");

    const title = document.createElement("span");
    title.className = "title";
    title.textContent = s.title || "(untitled)";

    const sub = document.createElement("span");
    sub.className = "sub";
    const when = document.createElement("span");
    when.textContent = relativeTime(s.lastActivityAt || s.createdAt);
    const count = document.createElement("span");
    count.textContent = `${s.messageCount ?? 0} msg`;
    sub.append(when, count);

    li.append(title, sub);
    li.addEventListener("click", () => onPick(s.id));
    listEl.append(li);
  }
}
