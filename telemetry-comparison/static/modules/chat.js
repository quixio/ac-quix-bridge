/**
 * Chat conversation UI: message list rendering, input field, send button,
 * mock backend. Slice 1 returns a hardcoded Mode-1 plot plan after a
 * 1-second delay so we can wire the full UX before plumbing the real
 * /api/plot call.
 *
 * Mode-1 plan shape lives in ai-plot-glue.js. This module emits the plan
 * to a callback the panel-level code wires up (so chat.js stays unaware
 * of how plans are applied).
 */

import { applyPlotPlan } from './ai-plot-glue.js';

const _MOCK_PLAN = {
  type: 'plot',
  title: 'Ludvik VideoSyncFix - Laps 1-4',
  signals: ['speedKmh', 'gas', 'brake', 'rpms'],
  traces: [1, 2, 3, 4].map((lap) => ({
    session_id: '2026-04-17T06:39:45.652Z',
    lap,
    driver: 'ludvik',
    carModel: 'bmw_1m',
    track: 'ks_nurburgring',
    experiment: 'VideoSyncFix',
    environment: 'prague_office',
    test_rig: 'g29',
  })),
};

let _messages = [];

function _render() {
  const list = document.getElementById('chat-messages');
  if (!list) return;
  list.innerHTML = _messages
    .map((m) => `<div class="chat-msg chat-msg-${m.role}">${m.text}</div>`)
    .join('');
  list.scrollTop = list.scrollHeight;
}

function _push(role, text) {
  _messages.push({ role, text });
  _render();
}

async function _mockSend(_userMsg) {
  _push('assistant-status', 'Thinking…');
  await new Promise((r) => setTimeout(r, 1000));
  // Pop the status, replace with the canned answer
  _messages.pop();
  _push('assistant', 'Plotting Ludvik’s 4 laps from VideoSyncFix.');
  applyPlotPlan(_MOCK_PLAN);
}

export function initChat() {
  const sendBtn = document.getElementById('chat-send');
  const input = document.getElementById('chat-input');
  if (!sendBtn || !input) return;

  const submit = () => {
    const text = input.value.trim();
    if (!text) return;
    _push('user', text);
    input.value = '';
    _mockSend(text);
  };

  sendBtn.addEventListener('click', submit);
  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault();
      submit();
    }
  });
}
