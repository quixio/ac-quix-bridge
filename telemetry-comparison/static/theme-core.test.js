import { describe, it, expect, beforeEach, afterEach, vi } from 'vitest';

const T = () => window.TETheme;

describe('resolveTheme', () => {
  it('prefers query over saved over system', () => {
    expect(T().resolveTheme({ query: 'light', saved: 'dark', system: 'dark' })).toBe('light');
    expect(T().resolveTheme({ query: 'dark', saved: 'light', system: 'light' })).toBe('dark');
    expect(T().resolveTheme({ saved: 'light', system: 'dark' })).toBe('light');
    expect(T().resolveTheme({ system: 'light' })).toBe('light');
  });

  it('falls back to dark with no signal', () => {
    expect(T().resolveTheme()).toBe('dark');
    expect(T().resolveTheme({})).toBe('dark');
    expect(T().resolveTheme({ system: undefined })).toBe('dark');
  });

  it('ignores invalid values and falls through', () => {
    expect(T().resolveTheme({ query: 'blue', saved: 'light' })).toBe('light');
    expect(T().resolveTheme({ query: '', saved: 'nope', system: 'light' })).toBe('light');
    expect(T().resolveTheme({ query: null, saved: null, system: 'purple' })).toBe('dark');
  });

  it('is case sensitive (only exact light/dark accepted)', () => {
    expect(T().resolveTheme({ query: 'LIGHT', saved: 'dark' })).toBe('dark');
  });
});

describe('applyTheme / setTheme / toggleTheme', () => {
  beforeEach(() => {
    localStorage.clear();
    T().applyTheme('dark');
  });

  it('sets data-theme on <html>', () => {
    T().applyTheme('light');
    expect(document.documentElement.getAttribute('data-theme')).toBe('light');
    expect(T().currentTheme()).toBe('light');
  });

  it('applyTheme does not persist', () => {
    T().applyTheme('light');
    expect(localStorage.getItem(T().STORAGE_KEY)).toBeNull();
  });

  it('setTheme persists and applies', () => {
    expect(T().setTheme('light')).toBe('light');
    expect(localStorage.getItem('te-theme')).toBe('light');
    expect(T().currentTheme()).toBe('light');
  });

  it('setTheme ignores invalid values', () => {
    T().setTheme('light');
    expect(T().setTheme('neon')).toBe('light');
    expect(localStorage.getItem('te-theme')).toBe('light');
  });

  it('toggleTheme flips and persists', () => {
    expect(T().toggleTheme()).toBe('light');
    expect(T().toggleTheme()).toBe('dark');
    expect(localStorage.getItem('te-theme')).toBe('dark');
  });

  it('fires themechange with previous/new theme only on a real change', () => {
    const spy = vi.fn();
    document.addEventListener('themechange', spy);
    T().applyTheme('light');
    T().applyTheme('light');
    document.removeEventListener('themechange', spy);
    expect(spy).toHaveBeenCalledTimes(1);
    expect(spy.mock.calls[0][0].detail).toEqual({ theme: 'light', previous: 'dark' });
  });

  it('still applies when localStorage throws', () => {
    const orig = Object.getOwnPropertyDescriptor(globalThis, 'localStorage');
    Object.defineProperty(globalThis, 'localStorage', {
      configurable: true,
      value: {
        getItem: () => {
          throw new Error('blocked');
        },
        setItem: () => {
          throw new Error('blocked');
        },
      },
    });
    try {
      expect(T().setTheme('light')).toBe('light');
      expect(T().detectTheme()).toBe('dark');
    } finally {
      Object.defineProperty(globalThis, 'localStorage', orig);
    }
  });
});

describe('detectTheme (browser inputs)', () => {
  const setSearch = (q) => window.history.replaceState({}, '', '/' + q);
  const setMatchMedia = (fn) =>
    Object.defineProperty(window, 'matchMedia', { configurable: true, writable: true, value: fn });
  const mockSystem = (light) =>
    setMatchMedia((q) => ({
      matches: q.includes('light') ? light : !light,
      addEventListener() {},
      removeEventListener() {},
    }));

  beforeEach(() => {
    localStorage.clear();
    setSearch('');
  });

  afterEach(() => setMatchMedia(undefined));

  it('uses prefers-color-scheme when nothing saved', () => {
    mockSystem(true);
    expect(T().detectTheme()).toBe('light');
    mockSystem(false);
    expect(T().detectTheme()).toBe('dark');
  });

  it('saved choice beats system', () => {
    mockSystem(true);
    localStorage.setItem('te-theme', 'dark');
    expect(T().detectTheme()).toBe('dark');
  });

  it('?theme= beats saved choice and is not persisted', () => {
    mockSystem(false);
    localStorage.setItem('te-theme', 'dark');
    setSearch('?theme=light');
    expect(T().detectTheme()).toBe('light');
    expect(localStorage.getItem('te-theme')).toBe('dark');
  });

  it('invalid ?theme= is ignored', () => {
    mockSystem(true);
    setSearch('?theme=banana');
    expect(T().detectTheme()).toBe('light');
  });

  it('defaults to dark without matchMedia', () => {
    setMatchMedia(() => {
      throw new Error('unsupported');
    });
    expect(T().detectTheme()).toBe('dark');
  });
});

describe('chart theming', () => {
  it('trace palettes are index-aligned and the same length', () => {
    const d = T().traceColors('dark');
    const l = T().traceColors('light');
    expect(d.length).toBe(l.length);
    expect(new Set(l).size).toBe(l.length);
  });

  it('mapTraceColor translates between themes and round-trips', () => {
    const d = T().traceColors('dark');
    const l = T().traceColors('light');
    d.forEach((c, i) => {
      expect(T().mapTraceColor(c, 'dark', 'light')).toBe(l[i]);
      expect(T().mapTraceColor(l[i], 'light', 'dark')).toBe(c);
    });
    expect(T().mapTraceColor(d[0].toUpperCase(), 'dark', 'light')).toBe(l[0]);
  });

  it('mapTraceColor passes unknown colours through', () => {
    expect(T().mapTraceColor('#123456', 'dark', 'light')).toBe('#123456');
    expect(T().mapTraceColor('#123456', 'dark', 'bogus')).toBe('#123456');
  });

  it('plotlyLayout reflects the theme', () => {
    const dark = T().plotlyLayout('dark');
    const light = T().plotlyLayout('light');
    expect(dark.paper_bgcolor).toBe('#222229');
    expect(light.paper_bgcolor).toBe('#ffffff');
    expect(light.xaxis.gridcolor).not.toBe(dark.xaxis.gridcolor);
    expect(light.hoverlabel).toBeDefined();
    expect(dark.hoverlabel).toBeUndefined();
  });

  it('plotlyLayout/chartTheme default to the active theme', () => {
    T().applyTheme('light');
    expect(T().plotlyLayout().paper_bgcolor).toBe('#ffffff');
    expect(T().chartTheme().text).toBe(T().chartTheme('light').text);
    T().applyTheme('dark');
    expect(T().plotlyLayout().paper_bgcolor).toBe('#222229');
  });

  it('trackColors overrides server colours in light only', () => {
    const server = { hairpin: '#ff9999', straight: '#99ff99', other: '#abcdef' };
    expect(T().trackColors(server, 'dark')).toBe(server);
    const light = T().trackColors(server, 'light');
    expect(light.hairpin).not.toBe('#ff9999');
    expect(light.other).toBe('#abcdef');
    expect(server.hairpin).toBe('#ff9999');
  });
});
