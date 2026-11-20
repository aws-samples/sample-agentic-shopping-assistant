import type MarkdownIt from "markdown-it";
import type { Product } from "~/types/domain";

/**
 * Product citations in Aria's answers.
 *
 * The agent cites a product it got from a catalog search as `[[Product:<id>]]`. A citation on
 * a line of its own is a card slot: the answer shows that product's card there, between the
 * section heading and the explanation. A citation inside a sentence shows the product's name.
 *
 * Cards come only from validated `product.v1` search results already in this conversation.
 * An id the search never returned has no card, whatever the model wrote.
 */

const ID_CHAR = "[A-Za-z0-9._:-]";
const ID = `${ID_CHAR}{1,128}`;
const STANDALONE = new RegExp(`^[ \\t]*\\[\\[Product:(${ID})\\]\\][ \\t]*$`);
const INLINE = new RegExp(`^\\[\\[Product:(${ID})\\]\\]`);
const OPENER = "[[Product:";
// A citation the model has started but not finished: "[[Product:sample-", "[[Product:sample-1]".
const UNFINISHED = new RegExp(`\\[\\[Product:${ID_CHAR}{0,128}\\]?$`);

export type AnswerSegment = { kind: "text"; text: string } | { kind: "card"; id: string };

export interface CitationEnv {
  products: ReadonlyMap<string, Product>;
  /** Restored history is text-only, so its products are unknown rather than unverified. */
  restored: boolean;
}

/**
 * Remove a citation the model is still writing, so "[[Prod…" never flashes on screen.
 *
 * While streaming, a trailing prefix of the opener ("[", "[[P") is held back too. Once the
 * answer is complete, only an unfinished citation is dropped; other trailing text stays.
 */
export function withoutUnfinishedCitation(text: string, streaming: boolean): string {
  const unfinished = UNFINISHED.exec(text);
  if (unfinished) return text.slice(0, unfinished.index);
  if (streaming) {
    for (let size = OPENER.length - 1; size > 0; size--) {
      if (text.endsWith(OPENER.slice(0, size))) return text.slice(0, -size);
    }
  }
  return text;
}

/** Split an answer into prose and the card slots the agent placed on their own lines. */
export function splitAnswer(text: string, streaming: boolean): AnswerSegment[] {
  const segments: AnswerSegment[] = [];
  let prose: string[] = [];
  const flush = () => {
    const joined = prose.join("\n");
    if (joined.trim()) segments.push({ kind: "text", text: joined });
    prose = [];
  };
  for (const line of withoutUnfinishedCitation(text, streaming).split("\n")) {
    const card = STANDALONE.exec(line);
    if (card) {
      flush();
      segments.push({ kind: "card", id: card[1]! });
    } else {
      prose.push(line);
    }
  }
  flush();
  return segments;
}

/**
 * markdown-it plugin: an inline `[[Product:<id>]]` renders as the product's name.
 *
 * The output is built here with every product string escaped, and markdown-it still runs with
 * HTML disabled, so neither the model nor a catalog field can inject markup.
 */
export function productCitations(md: MarkdownIt): void {
  md.inline.ruler.before("link", "product_citation", (state, silent) => {
    const match = INLINE.exec(state.src.slice(state.pos));
    if (!match) return false;
    if (!silent) state.push("product_citation", "", 0).meta = { id: match[1] };
    state.pos += match[0].length;
    return true;
  });
  md.renderer.rules.product_citation = (tokens, index, _options, env: CitationEnv) => {
    const product = env.products.get(tokens[index]!.meta.id);
    if (product) return `<span class="product-cite">${md.utils.escapeHtml(product.productDisplayName)}</span>`;
    return env.restored ? "이 상품" : '<span class="product-cite is-unknown">확인할 수 없는 상품</span>';
  };
}
