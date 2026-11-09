// @vitest-environment node
import { describe, it, expect } from 'vitest';
import { readFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const css = readFileSync(fileURLToPath(new URL('./styles.css', import.meta.url)), 'utf8');

function tokens(theme) {
  const start = css.indexOf(`:root[data-theme="${theme}"] {`);
  const end = css.indexOf('\n}', start);
  const out = {};
  for (const m of css
    .slice(start, end)
    .matchAll(/^\s*(--[\w-]+):\s*(.+?);\s*(?:\/\*.*\*\/)?\s*$/gm))
    out[m[1]] = m[2];
  return out;
}

const dark = tokens('dark');
const light = tokens('light');
const NON_COLOUR = new Set(['--row-control-h']);

function rgba(v) {
  let m = /^#([0-9a-f]{6})$/i.exec(v);
  if (m) {
    const n = parseInt(m[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  m = /^rgba?\(([^)]+)\)$/.exec(v);
  if (m) {
    const p = m[1].split(',').map((x) => parseFloat(x));
    return [p[0], p[1], p[2], p[3] ?? 1];
  }
  throw new Error('unparseable colour: ' + v);
}

function over(fg, bg) {
  const [r, g, b, a] = fg;
  return [r, g, b].map((c, i) => c * a + bg[i] * (1 - a));
}

function lum([r, g, b]) {
  const f = (c) => {
    c /= 255;
    return c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * f(r) + 0.7152 * f(g) + 0.0722 * f(b);
}

function contrast(fgTok, bgTok, set) {
  // Translucent backgrounds are composited over the panel surface.
  const base = rgba(set['--surface']).slice(0, 3);
  const bg = over(rgba(set[bgTok]), base);
  const fg = over(rgba(set[fgTok]), bg);
  const [a, b] = [lum(fg), lum(bg)].sort((x, y) => y - x);
  return (a + 0.05) / (b + 0.05);
}

describe('theme tokens', () => {
  it('both themes define exactly the same variables', () => {
    const names = (t) =>
      Object.keys(t)
        .filter((k) => !NON_COLOUR.has(k))
        .sort();
    expect(names(light)).toEqual(names(dark));
    expect(Object.keys(dark).length).toBeGreaterThan(50);
  });

  it('dark theme keeps the original palette', () => {
    expect(dark['--bg']).toBe('#1a1a1a');
    expect(dark['--surface']).toBe('#222229');
  });
});

describe('light theme WCAG AA contrast', () => {
  const normalText = [
    ['--text', '--bg'],
    ['--text', '--surface'],
    ['--text-strong', '--surface'],
    ['--text-muted', '--surface'],
    ['--text-muted', '--bg'],
    ['--accent', '--surface'],
    ['--accent', '--bg'],
    ['--on-accent', '--accent'],
    ['--on-accent', '--accent-hover'],
    ['--green', '--surface'],
    ['--warn-text', '--surface'],
    ['--red', '--surface'],
    ['--color-text', '--color-surface'],
    ['--color-txt2', '--color-surface'],
    ['--color-muted', '--color-surface'],
    ['--color-text', '--color-head-bg'],
    ['--color-text', '--color-terminal'],
    ['--color-text', '--color-code-bg'],
    ['--color-fab-fg', '--color-accent'],
    ['--color-err-text', '--color-surface'],
    ['--toast-warn', '--toast-warn-bg'],
    ['--toast-success', '--toast-success-bg'],
    ['--text', '--toast-bg'],
    ['--text', '--toast-error-bg'],
  ];
  it.each(normalText)('%s on %s >= 4.5:1', (fg, bg) => {
    expect(contrast(fg, bg, light)).toBeGreaterThanOrEqual(4.5);
  });

  it('borders meet 3:1 non-text contrast against the surface (form controls)', () => {
    expect(contrast('--border', '--surface', light)).toBeGreaterThanOrEqual(1.5);
  });
});
