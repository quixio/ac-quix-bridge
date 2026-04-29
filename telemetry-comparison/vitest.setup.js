import { vi } from 'vitest';

vi.mock(
  '/Users/daniel/repos/ac-quix-bridge/telemetry-comparison/static/modules/markdown.js',
  () => ({ renderMarkdown: (t) => t || '' }),
);
