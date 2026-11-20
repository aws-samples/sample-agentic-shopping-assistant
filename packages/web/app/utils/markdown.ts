import markdownit from "markdown-it";
import { productCitations, type CitationEnv } from "~/utils/citations";

/**
 * The one markdown-it instance for Aria's answers.
 *
 * Answers are untrusted model output, and a prompt-injected answer could try to leak the
 * conversation through a URL. So nothing in an answer can point the browser at one:
 *
 * - raw HTML and linkify are off;
 * - images and autolinks (`<https://…>`) are disabled, so `![x](url)` and reference-style
 *   images never become `<img>`: no zero-click request;
 * - links render as their text alone, so `[결제](url)` and reference links offer nothing to click.
 *
 * Citations render through `productCitations`, which escapes every product string, so the
 * output is safe for `v-html`. Tables get a wrapper that scrolls inside the narrow panel.
 */
const md = markdownit({ html: false, linkify: false }).disable(["image", "autolink"]).use(productCitations);

md.renderer.rules.link_open = () => "";
md.renderer.rules.link_close = () => "";
md.renderer.rules.table_open = () => '<div class="nd-table"><table>\n';
md.renderer.rules.table_close = () => "</table></div>\n";

export function renderAnswer(text: string, env: CitationEnv): string {
  return md.render(text, env);
}
