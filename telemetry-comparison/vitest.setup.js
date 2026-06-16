import { vi } from 'vitest';

// vitest resolves vi.mock specifiers from the location of THIS setup file,
// not from the import-site (chat.js uses './markdown.js' from static/modules/).
// They land on the same absolute path, so vitest intercepts correctly.
vi.mock('./static/modules/markdown.js', () => ({ renderMarkdown: (t) => t || '' }));

// Node 22+ exposes a global `localStorage` that requires `--localstorage-file`
// to be functional — method calls throw without it. Override with a plain
// in-memory shim so jsdom-based tests can read/write without that flag.
const _lsStore = new Map();
Object.defineProperty(globalThis, 'localStorage', {
  configurable: true,
  value: {
    getItem: (k) => (_lsStore.has(k) ? _lsStore.get(k) : null),
    setItem: (k, v) => _lsStore.set(k, String(v)),
    removeItem: (k) => _lsStore.delete(k),
    clear: () => _lsStore.clear(),
    key: (i) => Array.from(_lsStore.keys())[i] ?? null,
    get length() {
      return _lsStore.size;
    },
  },
});
