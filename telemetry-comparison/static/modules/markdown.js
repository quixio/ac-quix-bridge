/**
 * markdown-it wrapper with a custom link renderer:
 *   - target=_blank + noopener for every link
 *   - reject schemes other than http(s) (mailto/javascript blocked)
 *
 * Self-hosted via the importmap in index.html so we don't pin a CDN URL.
 */

import MarkdownIt from 'markdown-it';

const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

const defaultLinkOpen =
  md.renderer.rules.link_open ||
  ((tokens, idx, options, _env, self) => self.renderToken(tokens, idx, options));

md.renderer.rules.link_open = (tokens, idx, options, env, self) => {
  const href = tokens[idx].attrGet('href') || '';
  if (!/^https?:\/\//i.test(href)) tokens[idx].attrSet('href', '#');
  tokens[idx].attrSet('target', '_blank');
  tokens[idx].attrSet('rel', 'noopener noreferrer');
  return defaultLinkOpen(tokens, idx, options, env, self);
};

export function renderMarkdown(text) {
  return md.render(text || '');
}
