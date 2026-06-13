import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

// Hoist the spy so vi.mock factory (which runs before imports) can reference it.
const applyPlotPlanSpy = vi.fn();

vi.mock('./ai-plot-glue.js', () => ({ applyPlotPlan: applyPlotPlanSpy }));

beforeEach(() => {
  document.body.innerHTML = `
    <div id="chat-messages"></div>
    <textarea id="chat-input"></textarea>
    <button id="chat-send"></button>
  `;
  applyPlotPlanSpy.mockClear();
});

afterEach(() => {
  vi.restoreAllMocks();
  document.body.innerHTML = '';
});

function _ndjson(events) {
  const body = events.map((e) => JSON.stringify(e)).join('\n');
  return new ReadableStream({
    start(controller) {
      controller.enqueue(new TextEncoder().encode(body));
      controller.close();
    },
  });
}

function _stubFetch(events) {
  vi.stubGlobal(
    'fetch',
    vi.fn(async () => ({
      ok: true,
      body: _ndjson(events),
      text: async () => '',
    })),
  );
}

async function _flush() {
  // Allow stream reader + rAF batches to settle.
  await new Promise((r) => setTimeout(r, 10));
  await new Promise((r) => requestAnimationFrame(r));
}

/** Mirror what a real user does: type in the textarea, then click send.
 *  Setting `.value` programmatically does NOT fire `input`, so the send
 *  button stays disabled by `_refreshSendDisabled`. The dispatch wakes it. */
function _typeAndSend(text) {
  const input = document.getElementById('chat-input');
  input.value = text;
  input.dispatchEvent(new Event('input'));
  document.getElementById('chat-send').click();
}

describe('chat.js JSONL handling', () => {
  it('renders answer_delta chunks as a single assistant bubble', async () => {
    _stubFetch([
      { event: 'status', session_id: 's1', message: 'Thinking…' },
      { event: 'answer_delta', session_id: 's1', text: 'Hello ' },
      { event: 'answer_delta', session_id: 's1', text: 'world.' },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('hi');
    await _flush();

    const bubbles = document.querySelectorAll('.chat-msg-assistant');
    expect(bubbles).toHaveLength(1);
    expect(bubbles[0].textContent).toBe('Hello world.');
  });

  it('answer_break splits prose into two bubbles', async () => {
    _stubFetch([
      { event: 'answer_delta', session_id: 's', text: 'Pre.' },
      { event: 'answer_break', session_id: 's' },
      { event: 'answer_delta', session_id: 's', text: 'Post.' },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('q');
    await _flush();

    const bubbles = document.querySelectorAll('.chat-msg-assistant');
    expect(bubbles).toHaveLength(2);
  });

  it('plot event calls applyPlotPlan', async () => {
    _stubFetch([
      {
        event: 'plot',
        session_id: 's',
        plan: { type: 'plot', signals: ['speedKmh'], traces: [] },
      },
    ]);

    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('plot');
    await _flush();

    expect(applyPlotPlanSpy).toHaveBeenCalledWith({
      type: 'plot',
      signals: ['speedKmh'],
      traces: [],
    });
  });

  it('clarify event renders option chips', async () => {
    _stubFetch([
      {
        event: 'clarify',
        session_id: 's',
        question: 'Which?',
        options: ['a', 'b'],
      },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('show');
    await _flush();

    const chips = document.querySelectorAll('.chat-clarify-chip');
    expect(chips).toHaveLength(2);
    expect(chips[0].textContent).toBe('a');
    expect(chips[1].textContent).toBe('b');
  });

  it('renders a tool card with name, args, and result', async () => {
    _stubFetch([
      {
        event: 'tool_start',
        session_id: 's',
        tool_call_id: 't1',
        tool_name: 'run_query',
        display_name: 'Run Query',
      },
      { event: 'tool_args', session_id: 's', tool_call_id: 't1', delta: '{"sql":' },
      { event: 'tool_args', session_id: 's', tool_call_id: 't1', delta: ' "SELECT 1"}' },
      { event: 'tool_end', session_id: 's', tool_call_id: 't1' },
      {
        event: 'tool_result',
        session_id: 's',
        tool_call_id: 't1',
        result: '1 row',
        is_error: false,
      },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('go');
    await _flush();

    const card = document.querySelector('.chat-tool-card');
    expect(card).not.toBeNull();
    expect(card.querySelector('.chat-tool-head').textContent).toBe('Run Query');
    expect(card.querySelector('.chat-tool-args').textContent).toContain('SELECT 1');
    expect(card.querySelector('.chat-tool-result').textContent).toBe('1 row');
    expect(card.classList.contains('chat-tool-done')).toBe(true);
    // Collapsed by default; clicking the header expands it.
    expect(card.classList.contains('chat-tool-collapsed')).toBe(true);
    card.querySelector('.chat-tool-head').click();
    expect(card.classList.contains('chat-tool-collapsed')).toBe(false);
  });

  it('tool_result with is_error marks the card as errored', async () => {
    _stubFetch([
      { event: 'tool_start', session_id: 's', tool_call_id: 't1', tool_name: 'run_query' },
      {
        event: 'tool_result',
        session_id: 's',
        tool_call_id: 't1',
        result: 'bad sql',
        is_error: true,
      },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('go');
    await _flush();

    const card = document.querySelector('.chat-tool-card');
    expect(card.classList.contains('chat-tool-error')).toBe(true);
    expect(card.querySelector('.chat-tool-result').textContent).toBe('bad sql');
  });

  it('plot_data tool call drives applyPlotPlan from its args', async () => {
    const trace = {
      session_id: 's1',
      lap: 1,
      driver: 'ludvik',
      carModel: 'lambo',
      track: 'spa',
      experiment: 'E',
      environment: 'byox',
      test_rig: 'g29',
    };
    _stubFetch([
      {
        event: 'tool_start',
        session_id: 's',
        tool_call_id: 'p1',
        tool_name: 'mcp__telemetry-comparison__plot_data',
        display_name: 'Plot data',
      },
      {
        event: 'tool_args',
        session_id: 's',
        tool_call_id: 'p1',
        delta: JSON.stringify({ signals: ['speedKmh'], traces: [trace] }),
      },
      { event: 'tool_end', session_id: 's', tool_call_id: 'p1' },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('plot lap 1');
    await _flush();

    expect(applyPlotPlanSpy).toHaveBeenCalledWith({
      type: 'plot',
      signals: ['speedKmh'],
      traces: [trace],
    });
  });

  it('error event renders red bubble', async () => {
    _stubFetch([{ event: 'error', session_id: 's', detail: 'boom', status: 502 }]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('x');
    await _flush();

    const err = document.querySelector('.chat-msg-error');
    expect(err).not.toBeNull();
    expect(err.textContent).toContain('boom');
    expect(err.textContent).toContain('502');
  });

  it('env_agent events render a block with header name, command line, and completed status', async () => {
    _stubFetch([
      {
        event: 'env_agent_start',
        session_id: 's',
        agent_id: 'a1',
        workspace_id: 'ws-1',
        workspace_name: 'WS One',
        task: 'analyze laps',
      },
      {
        event: 'env_agent_activity',
        session_id: 's',
        agent_id: 'a1',
        kind: 'command',
        data: { command: 'ls', exitCode: 0, toolUseId: 'u1' },
      },
      {
        event: 'env_agent_end',
        session_id: 's',
        agent_id: 'a1',
        status: 'completed',
        summary: 'Done analyzing.',
      },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('go deep');
    await _flush();

    const block = document.querySelector('.chat-env-agent');
    expect(block).not.toBeNull();
    expect(block.querySelector('.chat-env-agent-name').textContent).toBe('WS One');
    expect(block.textContent).toContain('$ ls');
    expect(block.querySelector('.chat-env-agent-summary').textContent).toContain('Done analyzing.');
    expect(block.classList.contains('chat-env-done')).toBe(true);
  });

  it('env_agent nested tool_start/tool_result correlate into a tool card', async () => {
    _stubFetch([
      { event: 'env_agent_start', session_id: 's', agent_id: 'a1', workspace_id: 'ws-1' },
      {
        event: 'env_agent_activity',
        session_id: 's',
        agent_id: 'a1',
        kind: 'tool_start',
        data: {
          tool: 'Bash',
          displayName: 'Run command',
          toolUseId: 'u1',
          arguments: '{"cmd":"ls"}',
        },
      },
      {
        event: 'env_agent_activity',
        session_id: 's',
        agent_id: 'a1',
        kind: 'tool_result',
        data: { toolUseId: 'u1', summary: 'file list', isError: false },
      },
      { event: 'env_agent_end', session_id: 's', agent_id: 'a1', status: 'completed' },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('go deep');
    await _flush();

    const card = document.querySelector('.chat-env-agent .chat-tool-card');
    expect(card).not.toBeNull();
    expect(card.querySelector('.chat-tool-head').textContent).toBe('Run command');
    expect(card.querySelector('.chat-tool-result').textContent).toBe('file list');
    expect(card.classList.contains('chat-tool-done')).toBe(true);
  });

  it('env_agent_end with failed status marks the block as failed', async () => {
    _stubFetch([
      { event: 'env_agent_start', session_id: 's', agent_id: 'a1', workspace_id: 'ws-1' },
      {
        event: 'env_agent_end',
        session_id: 's',
        agent_id: 'a1',
        status: 'failed',
        summary: 'boom',
      },
    ]);
    const { initChat } = await import('./chat.js');
    initChat();
    _typeAndSend('go deep');
    await _flush();

    const block = document.querySelector('.chat-env-agent');
    expect(block.classList.contains('chat-env-failed')).toBe(true);
  });
});
