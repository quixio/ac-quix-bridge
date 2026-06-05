/**
 * Chat UI glue. Submits prompts to /api/chat (JSONL stream), renders
 * assistant replies (status / answer_delta / answer_break / tool_start /
 * tool_args / tool_end / tool_result / clarify / plot / error) including
 * tool cards, and forwards plot plans to applyPlotPlan so they drive
 * the existing manual UI surfaces (dropdowns + lap chips + signal chips
 * + Plot button).
 *
 * Wire flow:
 *   user types -> submit() -> POST /api/chat
 *   response body = ndjson, read line by line
 *   each line dispatched to handleEvent()
 *   plot event -> applyPlotPlan(plan) -> existing /api/telemetry pipeline
 */

import { applyPlotPlan } from './ai-plot-glue.js';
import { renderMarkdown } from './markdown.js';

let _sessionId = null;
let _activeAnswer = null; // current accumulating assistant bubble
let _sending = false;
const _toolCards = new Map(); // tool_call_id -> { argsBuf, argsEl, resultEl }

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

function _addToolCard(toolCallId, label, toolName) {
  const list = document.getElementById('chat-messages');
  if (!list) return;
  const card = document.createElement('div');
  card.className = 'chat-tool-card chat-tool-running';
  const head = document.createElement('div');
  head.className = 'chat-tool-head';
  head.textContent = label || 'tool';
  const args = document.createElement('pre');
  args.className = 'chat-tool-args';
  const result = document.createElement('div');
  result.className = 'chat-tool-result';
  card.append(head, args, result);
  list.appendChild(card);
  _toolCards.set(toolCallId, {
    argsBuf: '',
    argsEl: args,
    resultEl: result,
    cardEl: card,
    name: toolName || '',
  });
  _scrollBottom(list);
}

function _finalizeToolArgs(toolCallId) {
  const c = _toolCards.get(toolCallId);
  if (!c) return;
  let parsed = null;
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

function _fillToolResult(toolCallId, text, isError) {
  const c = _toolCards.get(toolCallId);
  if (!c) return;
  c.cardEl.classList.remove('chat-tool-running');
  c.cardEl.classList.add(isError ? 'chat-tool-error' : 'chat-tool-done');
  c.resultEl.textContent = typeof text === 'string' ? text : JSON.stringify(text);
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
      _showProgress(evt.message);
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
      _finalizeToolArgs(evt.tool_call_id);
      break;
    case 'tool_result':
      _fillToolResult(evt.tool_call_id, evt.result, evt.is_error);
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
  _addMessage('user', text);
  _showProgress('Thinking…');

  try {
    const res = await fetch('/api/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message: text, session_id: _sessionId }),
    });
    if (!res.ok || !res.body) {
      _hideProgress();
      const detail = await res.text();
      _addMessage('error', `Backend error (${res.status}): ${detail.slice(0, 400)}`);
      return;
    }
    await _readEventStream(res.body);
  } catch (err) {
    _hideProgress();
    _addMessage('error', `Network error: ${err.message}`);
  } finally {
    _sending = false;
    input.focus();
    _refreshSendDisabled();
  }
}

export function initChat() {
  const sendBtn = document.getElementById('chat-send');
  const input = document.getElementById('chat-input');
  if (!sendBtn || !input) return;

  sendBtn.addEventListener('click', _submit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      _submit();
    }
  });
  input.addEventListener('input', _refreshSendDisabled);
  _refreshSendDisabled();
}
