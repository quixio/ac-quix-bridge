import MarkdownIt from "markdown-it";

// `html: false` means raw HTML in message content is escaped — XSS-safe by default.
const md = new MarkdownIt({
  html: false,
  linkify: true,
  breaks: true,
});

export function renderMarkdown(text) {
  return md.render(text || "");
}
