import { vi } from 'vitest';

// vitest resolves vi.mock specifiers from the location of THIS setup file,
// not from the import-site (chat.js uses './markdown.js' from static/modules/).
// They land on the same absolute path, so vitest intercepts correctly.
vi.mock('./static/modules/markdown.js', () => ({ renderMarkdown: (t) => t || '' }));
