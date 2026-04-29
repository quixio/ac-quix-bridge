import { vi } from 'vitest';

vi.mock('./static/modules/markdown.js', () => ({ renderMarkdown: (t) => t || '' }));
