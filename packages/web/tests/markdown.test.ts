import { describe, expect, it } from "vitest";
import { renderAnswer } from "../app/utils/markdown";
import type { Product } from "../app/types/domain";
import products from "./fixtures/products.json";

const catalog = new Map((products as Product[]).map((product) => [product.id, product]));
const render = (text: string) => renderAnswer(text, { products: catalog, restored: false });

/** Nothing in the output may make the browser request or offer a URL. */
function expectNoUrlMarkup(html: string) {
  expect(html).not.toContain("<img");
  expect(html).not.toContain("<a");
  // A URL attribute on a real tag; escaped text such as `&lt;img src=x&gt;` is only text.
  expect(html).not.toMatch(/<[a-z][^>]*\s(?:src|href|srcset)\s*=/i);
}

describe("answers can't exfiltrate through URLs", () => {
  it("drops inline images, keeping their alt text", () => {
    const html = render("요약: ![x](https://evil.example/c?q=1)");
    expectNoUrlMarkup(html);
    expect(html).toContain("x");
  });

  it("renders links as their text only", () => {
    const html = render("[결제](https://evil.example) 페이지로 이동해 주세요.");
    expectNoUrlMarkup(html);
    expect(html).toBe("<p>결제 페이지로 이동해 주세요.</p>\n");
  });

  it("leaves autolinks and bare URLs as plain text", () => {
    for (const text of ["<https://evil.example>", "https://evil.example/c?q=1", "<mailto:x@evil.example>"]) {
      expectNoUrlMarkup(render(text));
    }
  });

  it("drops reference-style images and links, and hides their definitions' targets from markup", () => {
    const html = render("![x][logo] [결제][pay]\n\n[logo]: https://evil.example/c?q=1\n[pay]: https://evil.example/pay");
    expectNoUrlMarkup(html);
    expect(html).toContain("결제");
  });

  it("drops images inside links and links with script URLs", () => {
    expectNoUrlMarkup(render("[![x](https://evil.example/i.png)](https://evil.example)"));
    expectNoUrlMarkup(render("[누르기](javascript:alert(1))"));
  });

  it("keeps raw HTML escaped", () => {
    const html = render('<img src="https://evil.example/c"> <a href="https://evil.example">x</a>');
    expectNoUrlMarkup(html);
    expect(html).toContain("&lt;img");
  });
});

describe("answer markup", () => {
  it("wraps tables so they scroll inside the panel", () => {
    const html = render("| 상품 | 색상 |\n| --- | --- |\n| 자켓 | Blue |");
    expect(html).toMatch(/^<div class="nd-table"><table>/);
    expect(html).toContain("</table></div>");
  });

  it("keeps code as escaped text", () => {
    const html = render("```\n<img src=x>\n```\n\n`<b>`");
    expect(html).toContain("<pre><code>&lt;img src=x&gt;");
    expect(html).toContain("<code>&lt;b&gt;</code>");
    expectNoUrlMarkup(html);
  });

  it("resolves product citations", () => {
    expect(render("[[Product:sample-2]]는 여름용이에요.")).toContain('<span class="product-cite">Cotton Shirt</span>');
  });
});
