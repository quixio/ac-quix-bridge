import { describe, it, expect, beforeEach, vi } from 'vitest';
import { appState } from './state.js';
import { initThemeToggle, rethemeCharts, updateToggleButton } from './theme.js';

function fakeDiv() {
  return {
    data: [{}],
    layout: { shapes: [{ type: 'line', line: { color: '#ffffff', width: 1.5 } }] },
    _cornerAnnotations: [],
  };
}

beforeEach(() => {
  window.Plotly = { restyle: vi.fn(), relayout: vi.fn(), deleteTraces: vi.fn() };
  appState.plotDivs = [];
  appState.plotTraces = [];
  appState.highlightedLabel = null;
  window.trackData = null;
  window.trackConfig = null;
  localStorage.clear();
  window.TETheme.applyTheme('dark');
});

describe('rethemeCharts', () => {
  it('remaps trace colours and relayouts chart colours', () => {
    const T = window.TETheme;
    const div = fakeDiv();
    appState.plotDivs = [div];
    appState.plotTraces = [[{ label: 'a', color: T.traceColors('dark')[0], x: [0, 1], y: [0, 1] }]];

    rethemeCharts('dark', 'light');

    expect(appState.plotTraces[0][0].color).toBe(T.traceColors('light')[0]);
    expect(Plotly.restyle).toHaveBeenCalledWith(
      div,
      { 'line.color': T.traceColors('light')[0] },
      [0],
    );
    const update = Plotly.relayout.mock.calls[0][1];
    expect(update.paper_bgcolor).toBe('#ffffff');
    expect(update['xaxis.gridcolor']).toBe(T.chartTheme('light').grid);
    expect(update.shapes[0].line.color).toBe(T.chartTheme('light').markerLine);
    expect(update.shapes[0].line.width).toBe(1.5);
  });

  it('re-renders the track map when track data is loaded', () => {
    window.trackData = { points: [] };
    window.trackConfig = {};
    window.renderTrackMap = vi.fn();
    rethemeCharts('dark', 'light');
    expect(window.renderTrackMap).toHaveBeenCalledTimes(1);
  });

  it('is a no-op for charts when nothing is plotted', () => {
    expect(() => rethemeCharts('dark', 'light')).not.toThrow();
    expect(Plotly.relayout).not.toHaveBeenCalled();
  });
});

describe('theme toggle button', () => {
  it('updates aria state/label for each theme', () => {
    const btn = document.createElement('button');
    updateToggleButton(btn, 'dark');
    expect(btn.getAttribute('aria-pressed')).toBe('false');
    expect(btn.getAttribute('aria-label')).toMatch(/light/i);
    updateToggleButton(btn, 'light');
    expect(btn.getAttribute('aria-pressed')).toBe('true');
    expect(btn.getAttribute('aria-label')).toMatch(/dark/i);
  });

  it('click toggles the theme and persists it', () => {
    document.body.innerHTML = '<button id="theme-toggle"></button>';
    initThemeToggle();
    document.getElementById('theme-toggle').click();
    expect(window.TETheme.currentTheme()).toBe('light');
    expect(localStorage.getItem('te-theme')).toBe('light');
    expect(document.getElementById('theme-toggle').getAttribute('aria-pressed')).toBe('true');
  });
});
