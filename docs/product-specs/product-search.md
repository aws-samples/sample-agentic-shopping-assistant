# Product search

Status: shipped

How Aria finds products, seen from the shopper's side: what can be searched, what comes back, and what happens when nothing matches or a value is wrong. The tool is `search_products`, a local Strands tool in the agent; the search code is in `packages/catalog`. Its parameters come from the stage's catalog definition, so this spec describes them for the example catalog, which the Dev template uses. How the definition drives the tool, the index and the query is in [catalog search](../design-docs/catalog-search.md); implementation detail is in the [product search design doc](../design-docs/product-search.md), and the rules for the schema the model sees are in [tool schemas](../design-docs/tool-schemas.md).

## What the shopper sees

- The shopper describes what they want ("봄에 신을 운동화"). Aria says what it will look for, the status line reads "카탈로그를 검색하고 있어요", and the answer shows cards for the products Aria cites.
- Results aren't listed on their own. A returned product the answer doesn't cite isn't shown.
- A search returns up to 3 products unless Aria asks for more, and never more than 20.
- When nothing matches, Aria says so in plain prose and suggests how to broaden the request.
- When Aria sends a value the catalog doesn't declare, or a parameter the tool doesn't have, the call fails, the status line reads "검색 조건을 다시 확인하고 있어요", and Aria retries with a corrected call. The failed call isn't shown.
- When the catalog can't be reached, Aria says it couldn't check the catalog right now and offers to try again.
- Search doesn't know who the shopper is: the same search returns the same products for everyone.

## Filters

Every parameter is optional, and any one of them makes a valid search. With the example definition:

| Parameter | Type | Matches | Notes |
| --- | --- | --- | --- |
| `query` | string | `title` (weight 3) and `description`, by keyword; with OpenSearch, also by meaning | Free text for what the filters don't cover; at most 200 characters |
| `season` | string, enum | `season`, exactly | Summer, Fall, Winter, Spring |
| `gender` | string, enum | `gender`, exactly | Men, Women, Unisex, Boys, Girls |
| `category1` | string, enum | `category`, exactly | 7 values, below |
| `category2` | string, enum | `subcategory`, exactly | 45 values, below. The description adds: "Jackets and coats are Topwear; sneakers, running shoes and boots are Shoes." |
| `year` | integer | `year`, exactly | Release year, such as 2025 |
| `usage` | string, enum | `usage`, exactly | Casual, Smart Casual, Formal, Sports, Travel, Party, Home |
| `limit` | integer | | How many results: 3 by default, at most 20 |

The tool description tells the model to prefer the filters to free text, in the order season, gender, category1, category2, year, usage, and to use `query` only for what they leave out. Filter values are English, as the catalog stores them.

### Values

The values come from the catalog definition, `packages/catalog/src/aria_catalog/example/catalog.toml`, and the tool schema lists all of them as enums, so the model sees only values the catalog accepts.

- **category1:** Apparel, Accessories, Footwear, Personal Care, Free Items, Sporting Goods, Home.
- **category2:** Topwear, Shoes, Bags, Bottomwear, Watches, Innerwear, Jewellery, Eyewear, Fragrance, Sandal, Wallets, Flip Flops, Belts, Socks, Lips, Dress, Loungewear and Nightwear, Saree, Nails, Makeup, Headwear, Ties, Accessories, Scarves, Cufflinks, Apparel Set, Free Gifts, Stoles, Skin Care, Skin, Eyes, Mufflers, Shoe Accessories, Sports Equipment, Gloves, Hair, Bath and Body, Water Bottle, Perfumes, Umbrellas, Beauty Accessories, Wristbands, Sports Accessories, Home Furnishing, Vouchers.
- **gender:** Men, Women, Unisex, Boys, Girls.
- **season:** Summer, Fall, Winter, Spring. Autumn is `Fall`; "Autumn" is rejected.
- **usage:** Casual, Smart Casual, Formal, Sports, Travel, Party, Home.

### What can't be searched

Colour (`colour`), article type (`type`), price, stock, size, material and brand are not filters in the example. Colour and article type are on every product and on its card, but they aren't search fields or part of the embedded text, so "검은색 자켓" finds jackets whatever their colour, unless a title names it.

## Rules

- **No criteria, no results.** A call with no criteria, or only blank ones, returns `{"schemaVersion": "product.v1", "count": 0, "products": []}` without calling the catalog.
- **Exact filters, forgiving case.** Keyword filters match a declared value ignoring case, and come back in its declared spelling, so `men` becomes `Men`. Any other value fails with the allowed list, for example `invalid category2 'Outerwear'; expected one of: Topwear, Shoes, ...`, and the model can correct the call.
- **Whole numbers.** `year` and `limit` must be whole numbers; `"2024"` and `2024.0` are accepted, `"2024.5"` isn't. A `limit` over 20 becomes 20, and one below 1 fails.
- **Unknown arguments fail.** A parameter the tool doesn't have, such as `colour`, fails with the accepted list: `unknown parameter 'colour'; expected one of: query, season, gender, category1, category2, year, usage, limit`.
- **Free text.** `query` is trimmed, and a blank one counts as absent. One over 200 characters fails, asking for a shorter query or the filters. How it matches depends on the provider (below).
- **Results.** A `product.v1` envelope with at most `limit` products, each with exactly the ten fields listed in [product cards](product-cards.md#the-productv1-envelope). There are no prices, stock, ratings or images.
- **Every provider's products are checked.** A product with a missing or invalid field, or an ID the web can't cite, is dropped and counted in the `product_search_invalid_product` log event. If the catalog returns products and none pass, the search fails rather than reporting no match.
- **Keyword search when embedding fails.** With OpenSearch, a query that can't be embedded is searched by keyword alone, with the same filters, and logged as `product_search_embedding_failed`. The shopper still gets results.
- **Errors.** An invalid argument reaches the model as an actionable error. Every other failure, a catalog fault or a bad response, is logged as `product_search_failed` and reported to the model as `Product search failed`, without details, so the model doesn't retry a call that can't succeed.
- **Budget.** Every search, retries included, counts toward the turn's 8 tool calls ([limits](shopping-conversation.md#limits)).

## Providers

`tools.productSearch.provider` selects the catalog behind the tool. The shopper can't tell which one runs, except by what comes back.

| Provider | What the shopper gets |
| --- | --- |
| `fixture` (the Dev template; Prod rejects it) | The example catalog's 150 synthetic products, searched in memory. Every filter must hold. Each `query` word found in a title, ignoring case, scores 3, and in a description 1; products that score nothing are left out, and ties keep catalog order. No match by meaning |
| `opensearch` | The customer's catalog, indexed from its definition. `query` is matched by keyword (the search fields with their weights, analyzed with Nori for Korean when the definition says so) and by meaning (Embed v4 vectors), and the two rankings are fused. The filters apply to both. A filter-only search is by keyword. The domain is reachable only from the VPC, where the Runtime always runs |
| `http` | The customer's HTTPS catalog API. It receives the canonical arguments, with `null` for those not given, and returns products in the definition's field names, which map onto the ten card fields; products that fail the check are dropped. More than 20 products or a response over 256 KiB fails the search. 10-second timeout, no redirects; the credential is cached for 5 minutes and fetched again once after a 401 or 403 |
| `disabled` | No search tool, so Aria can't search the catalog |

The Prod template selects `http` with a placeholder endpoint. Which providers have run end to end is in the [quality score](../QUALITY_SCORE.md).

## The example catalog

`packages/catalog/src/aria_catalog/example/` holds the definition, `catalog.toml`, and 150 synthetic products, `products.json`. They are not customer inventory.

- **IDs:** `sample-1` to `sample-3`, then `nordly-001` to `nordly-147`.
- **Text:** titles in Korean and English, such as "겨울 자켓 Winter Jacket", and a one-sentence Korean description.
- **Spread:** Apparel (73), Accessories (48) and Footwear (29); all four seasons and five genders; years 2023 to 2026; seven usages and 20 colours. No prices.

With the fixture:

- "jacket" and "자켓" both find jackets, `sample-1` first.
- `season` Winter with `gender` Men returns `sample-1`, a wool blend coat and a cable knit sweater, in catalog order.
- Any query word can match, as a substring: "winter shoes" returns the Winter Jacket and two pairs of running shoes, each with one of the words in its title.

## Acceptance checks

Automated, part of `pnpm check`:

- On the fixture, `query="Winter", gender="Men"` returns a `product.v1` envelope as JSON text with only `sample-1`, called directly and through a Strands agent, and the Korean name is not escaped (`packages/agent/tests/unit/test_product_search_tool.py`).
- Filters alone are a search: `gender="men", season="WINTER", category2="topwear"` returns `sample-1`, `nordly-002` and `nordly-025` (same file).
- `category2="Outerwear"` returns an error that names `Outerwear` and lists `Topwear`; an unknown parameter, a `query` over 200 characters, `limit=0` and a non-numeric year return errors the model can act on (same file).
- A backend exception is reported as `Product search failed`, without its details, and logged as `product_search_failed` without argument values (same file).
- The schema is generated from the definition and lists every declared value as an enum; every declared value is accepted, ignoring case (same file; `packages/catalog/tests/test_definition.py`).
- No criteria, or only blank ones, return the empty envelope without calling a backend (`packages/catalog/tests/test_search.py`).
- `limit` defaults to 3 and is capped at 20; `gender="men"` becomes `Men`; `year="2024"` becomes 2024 (`test_definition.py`).
- With OpenSearch, a query runs the hybrid search through `<alias>-hybrid`; filters alone run without an embedding; a failed or malformed embedding falls back to the keyword search and is logged without values (`test_search.py`, and `test_product_search_tool.py` through the tool).
- The `http` provider receives the canonical arguments, flat, with `null` for criteria not given and ranges as `min` and `max`; an invalid product is dropped and counted; a malformed response is a catalog fault, never an argument error (`packages/catalog/tests/test_providers.py`, `test_search.py`, `test_contract.py`).
- The fixture weighs title words three times description words, keeps catalog order on ties, and serves only the example definition (`test_providers.py`).
- Every example product passes the definition's checks (`packages/catalog/tests/test_example.py`).

On a Dev stage with the fixture:

- "남성용 겨울 자켓 추천해 주세요." searches with season `Winter` and gender `Men`, and the answer cites products the search returned.
- "가을 코디 추천해줘" searches with season `Fall`, and the answer cites only products the search returned.

## Where it lives in the code

| What | Path |
| --- | --- |
| The tool, its schema and its error handling | `packages/agent/src/tools/product_search.py` |
| The definition: schema, argument checks, query, cards | `packages/catalog/src/aria_catalog/definition.py` |
| Provider selection, the hybrid query and its fallback | `packages/catalog/src/aria_catalog/search.py` |
| Fixture and HTTPS providers, the credential cache | `packages/catalog/src/aria_catalog/providers.py` |
| The query embedding | `packages/catalog/src/aria_catalog/embedding.py` |
| The product check every provider's products pass | `packages/catalog/src/aria_catalog/contract.py` |
| `CatalogQueryError` (the model's fault) and `CatalogBackendError` (the catalog's) | `packages/catalog/src/aria_catalog/errors.py` |
| The example catalog | `packages/catalog/src/aria_catalog/example/` |
| The `product.v1` envelope | `packages/catalog/src/aria_catalog/__init__.py` |
| Provider settings, `catalogFile` and validation | `packages/infra/config/loader.py` (`ProductSearchConfig`) |
| The indexing job | `packages/indexer/`, `scripts/index_catalog.sh` |

## Known gaps

- The example's values include categories outside the store the prompt describes (apparel, footwear and accessories), such as Personal Care, Home, Free Items and Vouchers, which no example product has.
- The card's ten fields are fashion-shaped, so a catalog of anything else must still map a field to each ([tracker](../exec-plans/tech-debt-tracker.md)).
