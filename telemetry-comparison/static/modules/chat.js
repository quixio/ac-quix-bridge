/**
 * Chat UI glue. Submits prompts to /api/chat (JSONL stream), renders
 * assistant replies (status / answer_delta / answer_break / tool_start /
 * tool_args / tool_end / tool_result / clarify / plot / error) including
 * tool cards, renders the DEEP-mode environment-agent block (env_agent_start /
 * env_agent_activity / env_agent_end), and forwards plot plans to applyPlotPlan
 * so they drive the existing manual UI surfaces (dropdowns + lap chips +
 * signal chips + Plot button).
 *
 * Wire flow:
 *   user types -> submit() -> POST /api/chat
 *   response body = ndjson, read line by line
 *   each line dispatched to handleEvent()
 *   plot event -> applyPlotPlan(plan) -> existing /api/telemetry pipeline
 */

import { applyPlotPlan } from './ai-plot-glue.js';
import { renderMarkdown } from './markdown.js';
import { formatStatus } from './thinking-messages.js';

let _sessionId = null;
let _activeAnswer = null; // current accumulating assistant bubble
let _sending = false;
let _abortController = null; // aborts the in-flight /api/chat stream on new-session
let _statusKey = null; // raw status key currently shown; re-roll label only when it changes
let _statusLabel = ''; // friendly label picked for the current status key
const _toolCards = new Map(); // tool_call_id -> { argsBuf, argsEl, resultEl }
const _envAgents = new Map(); // agent_id -> { root, body, count, indicator, tools, n, lastText }

const _pendingRender = new Set();
let _renderScheduled = false;

function _scrollBottom(el) {
  el.scrollTop = el.scrollHeight;
}

function _scheduleRender(body) {
  _pendingRender.add(body);
  if (_renderScheduled) return;
  _renderScheduled = true;
  requestAnimationFrame(() => {
    _renderScheduled = false;
    for (const el of _pendingRender) {
      el.innerHTML = renderMarkdown(el.dataset.raw || '');
    }
    _pendingRender.clear();
    const list = document.getElementById('chat-messages');
    if (list) _scrollBottom(list);
  });
}

function _addMessage(role, text) {
  const list = document.getElementById('chat-messages');
  if (!list) return null;
  const div = document.createElement('div');
  div.className = `chat-msg chat-msg-${role}`;
  if (role === 'assistant') {
    div.dataset.raw = text;
    div.innerHTML = renderMarkdown(text);
  } else {
    div.textContent = text;
  }
  list.appendChild(div);
  _scrollBottom(list);
  return div;
}

function _showProgress(label) {
  const list = document.getElementById('chat-messages');
  if (!list) return;
  let prog = document.getElementById('chat-progress');
  if (!prog) {
    prog = document.createElement('div');
    prog.id = 'chat-progress';
    prog.className = 'chat-msg chat-msg-assistant-status';
    list.appendChild(prog);
  }
  prog.textContent = label;
  _scrollBottom(list);
}

function _hideProgress() {
  document.getElementById('chat-progress')?.remove();
}

function _addClarifyChips(options, messageEl) {
  if (!options?.length) return;
  const wrap = document.createElement('div');
  wrap.className = 'chat-clarify-options';
  for (const opt of options) {
    const b = document.createElement('button');
    b.type = 'button';
    b.className = 'chat-clarify-chip';
    b.textContent = opt;
    b.addEventListener('click', () => {
      const input = document.getElementById('chat-input');
      if (!input) return;
      input.value = opt;
      _submit();
    });
    wrap.appendChild(b);
  }
  messageEl.appendChild(wrap);
}

// The agent's plot tool. Quix may surface it bare or MCP-prefixed
// (`mcp__telemetry-comparison__plot_data`), so match the suffix.
function _isPlotTool(name) {
  return typeof name === 'string' && /(?:^|_)plot_data$/.test(name);
}

// Build a tool card element + its state record. The caller appends `cardEl`
// to a parent (top-level chat or an env-agent body) and tracks `record` in
// whichever registry correlates results back. Collapsed by default — header
// toggles the body; input hides behind its own "Show input" toggle. Mirrors
// the native Quix AI card so tool runs don't dump args + results by default.
function _buildToolCard(label, toolName) {
  const card = document.createElement('div');
  card.className = 'chat-tool-card chat-tool-running chat-tool-collapsed';

  const head = document.createElement('div');
  head.className = 'chat-tool-head';
  head.textContent = label || 'tool';
  head.addEventListener('click', () => card.classList.toggle('chat-tool-collapsed'));

  const body = document.createElement('div');
  body.className = 'chat-tool-body';

  const inputToggle = document.createElement('div');
  inputToggle.className = 'chat-tool-subtoggle';
  inputToggle.textContent = 'Show input';
  const args = document.createElement('pre');
  args.className = 'chat-tool-args';
  args.textContent = '{}'; // default for no-arg tools; overwritten once args arrive
  inputToggle.addEventListener('click', () => {
    const shown = args.classList.toggle('chat-tool-args-shown');
    inputToggle.textContent = shown ? 'Hide input' : 'Show input';
  });

  const resultLabel = document.createElement('div');
  resultLabel.className = 'chat-tool-result-label';
  resultLabel.textContent = 'RESULT';
  const result = document.createElement('div');
  result.className = 'chat-tool-result';

  body.append(inputToggle, args, resultLabel, result);
  card.append(head, body);
  return {
    cardEl: card,
    record: {
      argsBuf: '',
      argsEl: args,
      resultEl: result,
      resultLabelEl: resultLabel,
      cardEl: card,
      name: toolName || '',
    },
  };
}

function _addToolCard(toolCallId, label, toolName) {
  const list = document.getElementById('chat-messages');
  if (!list) return;
  const { cardEl, record } = _buildToolCard(label, toolName);
  list.appendChild(cardEl);
  _toolCards.set(toolCallId, record);
  _scrollBottom(list);
}

function _finalizeToolArgs(c) {
  if (!c) return;
  let parsed = null;
  if (!c.argsBuf.trim()) {
    c.argsEl.textContent = '{}'; // no-arg tool — keep the placeholder
    return;
  }
  try {
    parsed = JSON.parse(c.argsBuf);
    c.argsEl.textContent = JSON.stringify(parsed, null, 2);
  } catch {
    c.argsEl.textContent = c.argsBuf;
  }
  // A plot_data call IS the plot directive — drive the chart from its args.
  if (parsed && _isPlotTool(c.name)) {
    applyPlotPlan({ type: 'plot', ...parsed });
  }
}

function _fillResult(c, text, isError) {
  if (!c) return;
  c.cardEl.classList.remove('chat-tool-running');
  c.cardEl.classList.add(isError ? 'chat-tool-error' : 'chat-tool-done');
  c.resultEl.textContent = typeof text === 'string' ? text : JSON.stringify(text);
  if (c.resultLabelEl) c.resultLabelEl.style.display = c.resultEl.textContent ? 'block' : 'none';
  const list = document.getElementById('chat-messages');
  if (list) _scrollBottom(list);
}

// --- DEEP-mode environment-agent block -----------------------------------
// delegate_task spawns an environment agent; the backend suppresses that
// tool's card and streams environment_agent_* events instead. We render a
// nested, collapsible block: header (status + workspace + event count),
// optional "Agent prompt", a body of activities (nested tool cards correlated
// by toolUseId, command/file lines, prose, status), and a summary footer.

function _startEnvAgent(evt) {
  const list = document.getElementById('chat-messages');
  if (!list) return;
  const root = document.createElement('div');
  root.className = 'chat-env-agent chat-env-running';

  const head = document.createElement('div');
  head.className = 'chat-env-agent-head';
  const indicator = document.createElement('span');
  indicator.className = 'chat-env-agent-indicator';
  const name = document.createElement('span');
  name.className = 'chat-env-agent-name';
  name.textContent = evt.workspace_name || evt.workspace_id || 'environment agent';
  const count = document.createElement('span');
  count.className = 'chat-env-agent-count';
  const chevron = document.createElement('span');
  chevron.className = 'chat-env-agent-chevron';
  head.append(indicator, name, count, chevron);
  head.addEventListener('click', () => root.classList.toggle('chat-env-collapsed'));

  const body = document.createElement('div');
  body.className = 'chat-env-agent-body';
  if (evt.task) {
    const toggle = document.createElement('div');
    toggle.className = 'chat-env-agent-task-toggle';
    toggle.textContent = 'Agent prompt';
    const taskEl = document.createElement('div');
    taskEl.className = 'chat-env-agent-task';
    taskEl.textContent = evt.task;
    toggle.addEventListener('click', () => taskEl.classList.toggle('chat-env-agent-task-shown'));
    body.append(toggle, taskEl);
  }

  root.append(head, body);
  list.appendChild(root);
  _envAgents.set(evt.agent_id, {
    root,
    body,
    count,
    indicator,
    tools: new Map(),
    n: 0,
    lastText: null,
  });
  _scrollBottom(list);
}

function _envAgentActivity(evt) {
  const a = _envAgents.get(evt.agent_id);
  if (!a) return;
  const d = evt.data || {};
  if (evt.kind === 'working') return; // transient "still working" pulse — no row
  a.n += 1;
  a.count.textContent = `${a.n} event${a.n === 1 ? '' : 's'}`;

  switch (evt.kind) {
    case 'tool_start': {
      const { cardEl, record } = _buildToolCard(d.displayName || d.tool, d.tool);
      a.body.appendChild(cardEl);
      if (d.toolUseId) a.tools.set(d.toolUseId, record);
      record.argsBuf = typeof d.arguments === 'string' ? d.arguments : '';
      _finalizeToolArgs(record);
      a.lastText = null;
      break;
    }
    case 'tool_result':
      _fillResult(a.tools.get(d.toolUseId), d.summary, d.isError);
      a.lastText = null;
      break;
    case 'command': {
      const line = document.createElement('div');
      line.className = 'chat-env-line chat-env-cmd';
      line.textContent = `$ ${d.command || ''}` + (d.exitCode ? `  (exit ${d.exitCode})` : '');
      a.body.appendChild(line);
      a.lastText = null;
      break;
    }
    case 'file_edit': {
      const line = document.createElement('div');
      line.className = 'chat-env-line chat-env-file';
      line.textContent = `✎ ${d.path || ''}`;
      a.body.appendChild(line);
      a.lastText = null;
      break;
    }
    case 'status': {
      const line = document.createElement('div');
      line.className = 'chat-env-line chat-env-statusmsg';
      line.textContent = formatStatus(d.status || d.message || '');
      a.body.appendChild(line);
      a.lastText = null;
      break;
    }
    case 'error': {
      const block = document.createElement('div');
      block.className = 'chat-env-error';
      block.textContent = d.message || 'Agent error';
      a.body.appendChild(block);
      a.lastText = null;
      break;
    }
    case 'text': {
      const msg = d.text || d.message || '';
      if (!msg) break;
      if (a.lastText) {
        a.lastText.dataset.raw = `${a.lastText.dataset.raw || ''}\n\n${msg}`;
      } else {
        a.lastText = document.createElement('div');
        a.lastText.className = 'chat-env-text';
        a.lastText.dataset.raw = msg;
        a.body.appendChild(a.lastText);
      }
      a.lastText.innerHTML = renderMarkdown(a.lastText.dataset.raw);
      break;
    }
  }
  const list = document.getElementById('chat-messages');
  if (list) _scrollBottom(list);
}

function _endEnvAgent(evt) {
  const a = _envAgents.get(evt.agent_id);
  if (!a) return;
  a.root.classList.remove('chat-env-running');
  a.root.classList.add(evt.status === 'failed' ? 'chat-env-failed' : 'chat-env-done');
  if (evt.summary) {
    const s = document.createElement('div');
    s.className = 'chat-env-agent-summary';
    s.dataset.raw = evt.summary;
    s.innerHTML = renderMarkdown(evt.summary);
    a.body.appendChild(s);
  }
  const list = document.getElementById('chat-messages');
  if (list) _scrollBottom(list);
}

async function _readEventStream(body) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = '';
  while (true) {
    const { done, value } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    const lines = buffer.split('\n');
    buffer = lines.pop() ?? '';
    for (const line of lines) {
      const trimmed = line.trim();
      if (trimmed) _parseAndHandle(trimmed);
    }
  }
  const tail = buffer.trim();
  if (tail) _parseAndHandle(tail);
}

function _parseAndHandle(line) {
  try {
    _handleEvent(JSON.parse(line));
  } catch (e) {
    console.error('chat: malformed event', e, line.slice(0, 200));
  }
}

function _handleEvent(evt) {
  if (evt.session_id) _sessionId = evt.session_id;
  switch (evt.event) {
    case 'status':
      _activeAnswer = null;
      // Map the raw key ("generating", "rate_limited", …) to a friendly label,
      // re-rolling only when the key changes so repeated frames don't flicker.
      if (evt.message !== _statusKey) {
        _statusKey = evt.message;
        _statusLabel = formatStatus(evt.message);
      }
      _showProgress(_statusLabel);
      break;
    case 'answer_delta': {
      _hideProgress();
      if (!_activeAnswer) {
        _activeAnswer = _addMessage('assistant', '');
      }
      _activeAnswer.dataset.raw = (_activeAnswer.dataset.raw || '') + evt.text;
      _scheduleRender(_activeAnswer);
      break;
    }
    case 'answer_break':
      _activeAnswer = null;
      break;
    case 'tool_start':
      _hideProgress();
      _activeAnswer = null;
      _addToolCard(evt.tool_call_id, evt.display_name || evt.tool_name, evt.tool_name);
      break;
    case 'tool_args': {
      const c = _toolCards.get(evt.tool_call_id);
      if (c) c.argsBuf += evt.delta || '';
      break;
    }
    case 'tool_end':
      _finalizeToolArgs(_toolCards.get(evt.tool_call_id));
      break;
    case 'tool_result':
      _fillResult(_toolCards.get(evt.tool_call_id), evt.result, evt.is_error);
      break;
    case 'env_agent_start':
      _hideProgress();
      _activeAnswer = null;
      _startEnvAgent(evt);
      break;
    case 'env_agent_activity':
      _envAgentActivity(evt);
      break;
    case 'env_agent_end':
      _activeAnswer = null;
      _endEnvAgent(evt);
      break;
    case 'clarify': {
      _hideProgress();
      _activeAnswer = null;
      const msg = _addMessage('assistant', evt.question);
      if (msg) _addClarifyChips(evt.options || [], msg);
      break;
    }
    case 'plot':
      _hideProgress();
      _activeAnswer = null;
      applyPlotPlan(evt.plan);
      break;
    case 'error':
      _hideProgress();
      _activeAnswer = null;
      _addMessage('error', `${evt.detail}${evt.status ? ` (${evt.status})` : ''}`.slice(0, 500));
      break;
  }
}

function _refreshSendDisabled() {
  const sendBtn = document.getElementById('chat-send');
  const input = document.getElementById('chat-input');
  if (!sendBtn || !input) return;
  sendBtn.disabled = _sending || !input.value.trim();
}

async function _submit() {
  const input = document.getElementById('chat-input');
  if (!input || _sending) return;
  const text = input.value.trim();
  if (!text) return;
  _sending = true;
  input.value = '';
  _refreshSendDisabled();

  _activeAnswer = null;
  _toolCards.clear();
  _envAgents.clear();
  _addMessage('user', text);
  _statusKey = 'generating';
  _statusLabel = formatStatus('generating');
  _showProgress(_statusLabel);

  _abortController = new AbortController();
  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: _sessionId }),
      signal: _abortController.signal,
    });
    if (!res.ok || !res.body) {
      _hideProgress();
      const detail = await res.text();
      _addMessage('error', `Backend error (${res.status}): ${detail.slice(0, 400)}`);
      return;
    }
    await _readEventStream(res.body);
  } catch (err) {
    // A new-session reset aborts the stream on purpose — stay silent.
    if (err.name === 'AbortError') return;
    _hideProgress();
    _addMessage('error', `Network error: ${err.message}`);
  } finally {
    _sending = false;
    _abortController = null;
    input.focus();
    _refreshSendDisabled();
  }
}

/** Discard the current conversation and start fresh. Aborts any in-flight
 *  stream, clears the transcript + per-turn state, and drops the session id
 *  so the next send opens a new Quix AI session (backend creates one when
 *  session_id is null). Frontend-only — no request is made here. */
export function newChatSession() {
  _abortController?.abort();
  _sessionId = null;
  _sending = false;
  _activeAnswer = null;
  _statusKey = null;
  _statusLabel = '';
  _toolCards.clear();
  _envAgents.clear();
  _pendingRender.clear();
  const list = document.getElementById('chat-messages');
  if (list) list.innerHTML = '';
  _refreshSendDisabled();
  document.getElementById('chat-input')?.focus();
}

export function initChat() {
  const sendBtn = document.getElementById('chat-send');
  const input = document.getElementById('chat-input');
  if (!sendBtn || !input) return;

  sendBtn.addEventListener('click', _submit);
  document.getElementById('chat-new')?.addEventListener('click', newChatSession);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      _submit();
    }
  });
  input.addEventListener('input', _refreshSendDisabled);
  _refreshSendDisabled();
}
