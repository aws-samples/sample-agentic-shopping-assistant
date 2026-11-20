# Catalog search

Status: verified

A customer brings their own catalog as JSON, together with a catalog definition: which fields exist, which become filters, which are searched as text, what text is embedded, and how each product maps onto a card. From that definition ARIA builds the search tool's parameters, an OpenSearch 3.5 index, the hybrid query (lexical and vector) the tool sends, and the indexing job that embeds the catalog with Cohere Embed v4. The repository ships a worked example, the synthetic NORDLY store.

The code is in `packages/catalog/src/aria_catalog/` (`definition.py`, `search.py`, `providers.py`, `embedding.py`), `packages/indexer/`, `scripts/index_catalog.sh` and, for the infrastructure, `packages/infra/components/catalog_indexer.py`, `opensearch_cluster.py` and `stacks/opensearch_stack.py`. Setting one up is in [integrations](../guides/integrations.md#bring-your-own-catalog), and running it in [operations](../guides/operations.md#catalog-runbook).

## The catalog definition

The stage's `.toml` names a definition file, `tools.productSearch.catalogFile`, relative to the config file. Empty means the packaged example, `packages/catalog/src/aria_catalog/example/catalog.toml`, with its 150 products in `example/products.json`. The loader validates the definition at synth (`parse_definition()`), so a bad definition fails the plan, not a shopper's search. Unknown keys are errors.

An excerpt of the example:

```toml
[catalog]
name = "NORDLY"
description = "Search fashion products in the NORDLY catalog. Use this tool only for searching products."
idField = "sku"
analyzer = "nori"

[[catalog.fields]]
name = "title"
type = "text"
search = true
boost = 3
description = "Product name, in Korean and English"

[[catalog.fields]]
name = "category"
parameter = "category1"
type = "keyword"
filter = true
values = ["Apparel", "Accessories", "Footwear", "Personal Care", "Free Items", "Sporting Goods", "Home"]
description = "Top-level category"

[[catalog.fields]]
name = "year"
type = "integer"
filter = true
description = "Release year, e.g. 2025."

# … sku, description, season, gender, subcategory, type, colour and usage

[catalog.embedding]
text = "{title}. {description}"
model = "global.cohere.embed-v4:0"

# Every product.v1 card field, each from a catalog field.
[catalog.card]
id = "sku"
productDisplayName = "title"
gender = "gender"
masterCategory = "category"
subCategory = "subcategory"
articleType = "type"
baseColour = "colour"
season = "season"
year = "year"
usage = "usage"

[catalog.search]
k = 50
```

- **Types:** `keyword`, `text`, `integer`, `float`, `boolean`.
- **Search fields:** `text` or `keyword` fields with `search = true`, each with a `boost` up to 10. At least one is required.
- **Filters:** `filter = true` is an exact filter on any type but `text`; a `keyword` filter lists its `values`, which the tool shows the model as an enum. `filter = "range"` is a minimum and maximum on an `integer` or `float` field. At most 12 filters. A filter's tool parameter is its field name unless `parameter` names another; `query` and `limit` are reserved.
- **Embedding:** Cohere Embed v4 at 1024 float dimensions. The definition chooses the text, a template of `{field}` placeholders, and the model ID or inference profile: `global.cohere.embed-v4:0` by default, or a geographic profile such as `us.cohere.embed-v4:0` where Bedrock offers one ([Embed v4 notes](../references/bedrock-embed-v4-notes.md#inference-profiles)).
- **Cards:** results map into `product.v1`, the contract the web renders. Every card field is required and names a catalog field: `id` the `idField`, `year` an integer or keyword field, the rest text or keyword fields.

[Integrations](../guides/integrations.md#the-definition-file) lists every key and rule.

## The query

The model never writes a query. Its arguments are checked against the definition, and `aria_catalog` builds the query itself.

- **What the model sees:** a tool schema generated from the definition (`tool_spec()`). It has a free-text `query` of at most 200 characters, one parameter per filter (an enum, a typed value, or an object with `min` and `max`) and `limit`, 3 by default and at most 20. Nothing is required. The descriptions come from the definition, and the tool's description asks the model to prefer the filters, in definition order, to free text. For the example: `query`, `season`, `gender`, `category1`, `category2`, `year`, `usage`, `limit`.
- **The checks:** `canonical_arguments()` refuses an unknown parameter, a value outside the declared values, a wrong type, a range whose minimum exceeds its maximum, a `query` over 200 characters and a `limit` below 1, each with a message the model can act on. Keyword values match ignoring case and come back in their declared spelling. A blank or null value is no criterion, and a call with no criteria returns nothing without calling the catalog.
- **What the tool sends** (`search_body()`): filters become `term` and `range` clauses. With a query, the body is a `hybrid` query of two parts, which the search pipeline `<alias>-hybrid` fuses by reciprocal rank fusion (RRF):
  - a `bool` query with the filters and a `multi_match` over the search fields, with their boosts;
  - a `knn` query on the `embedding` field, `k` from `[catalog.search]` (50 by default, at most 100), with the same filters.

  `pagination_depth` is the larger of `k` and `limit`, and `_source` holds only the card fields. Without a query, the body is the filters alone, sent without the pipeline and without an embedding.
- **The query embedding:** Bedrock, `input_type` `search_query`, through a client with a 1-second connect and 3-second read timeout and no retry. If embedding fails for any reason, the tool sends the lexical part alone and logs `product_search_embedding_failed` with the error type, so the shopper still gets an answer; the `SearchEmbeddingFailures` alarm counts those events, since nothing else reports a fallback.
- **Every provider takes the same canonical arguments:**
  - `opensearch` runs the hybrid query on the index behind the alias;
  - `fixture` searches the example catalog in memory, lexically: every filter must hold, and each query word found in a search field scores that field's boost;
  - `http` posts the arguments to the customer's API, flat in the tool's parameter names, and maps the products it returns, in the definition's field names, through `[catalog.card]`.

## The index

- **Engine:** OpenSearch 3.5 (`EngineVersion.OPENSEARCH_3_5`).
- **Analysis:** with `analyzer = "nori"`, the domain gets the Nori (Korean) plugin, an optional Amazon OpenSearch Service package whose ID differs by region and engine version. CloudFormation has no property for it, so two custom resources look the package up (`DescribePackages`) and associate it with the domain (`AssociatePackage`). Associating runs a blue/green deployment on the domain, and finishes after the deploy, so `scripts/index_catalog.sh` checks the association is `ACTIVE` before it writes. The association is kept in every stage and nothing dissociates it: the service can't dissociate a package an index still uses, and a Nori-analyzed version keeps serving searches and rollbacks after the analyzer changes ([OpenSearch notes](../references/opensearch-hybrid-search-notes.md#the-nori-plugin)).
- **Mapping:** generated from the definition (`index_body()`) and strict, so a document holds only the definition's fields and its vector. Text fields use the definition's analyzer. `embedding` is a k-NN vector of 1024 dimensions, HNSW on faiss with cosine similarity (`m` 16, `ef_construction` 128). One primary shard; 0 replicas on single-node Dev and 1 in Prod by default.
- **Versions:** each load is an index of its own, `<alias>-<version>`. The job refuses a version that exists and an alias that is a concrete index. It checks the count, then moves the alias from every index behind it to the new one in one `_aliases` call. Rolling back means indexing an earlier source as a new version, or moving the alias back.
- **Search pipeline:** `<alias>-hybrid`, a `score-ranker-processor` with the `rrf` technique (`pipeline_body()`). The job puts it before it moves the alias.

## The indexing job

The domain is reachable only from the VPC, so indexing runs inside it as a Fargate task, with no time limit on the catalog's size.

- **Stack:** the `Opensearch` stack holds everything the job touches, so it needs no cross-stack export: the domain, the Nori plugin, the catalog bucket and the indexing task. The task definition (ARM64, 1 vCPU, 4 GiB, read-only root file system, the image from `packages/indexer`) runs in the stage's ECS cluster, which `CommonApp` creates under a fixed name. It runs in the private subnets with no public IP. The stack's outputs name the bucket, cluster, task definition, subnets, security group, log group, alias, replica count, domain and Nori package; the script reads them by those names.
- **Steps** (`packages/indexer`):
  1. Read the catalog, a JSON array or JSONL, from the stage's catalog bucket, and validate each product against the definition (`check_product()`), rejecting duplicate IDs and blank embedding text.
  2. Create `<alias>-<version>` with the strict mapping.
  3. Embed the products with `input_type` `search_document`, 96 per call, backing off on throttling, and bulk-load each batch as it's embedded. A rejected document fails the job.
  4. Refresh and check the count.
  5. Put the search pipeline, then move the alias.

  The job prints one JSON manifest: accepted and rejected counts with reasons that name fields, the index, the estimated embedding work, and how far it got. It never holds product content. A dry run, the default, validates and estimates without writing or calling Bedrock.
- **Failure:** a failure before the last step leaves the alias where it was. The index it created stays, unpromoted: the task can't delete indexes, so an operator deletes it.
- **Access:**
  - **Network:** its own security group, with one TCP 443 rule into the domain's group and no ingress.
  - **Role**, trusted only by ECS tasks in the stage's account and region:
    - `s3:GetObject` on the bucket's `catalog/` prefix;
    - `bedrock:InvokeModel` on the definition's embedding model, and the foundation model in the regions its profile routes to;
    - `es:ESHttpGet`, `es:ESHttpHead`, `es:ESHttpPut` and `es:ESHttpPost` on the alias, `<alias>-*`, `_aliases` and the pipeline. No `es:ESHttpDelete`.
  - **No endpoint:** an operator starts it with `scripts/index_catalog.sh`, which runs `aws ecs run-task`.

## The network: VPC mode always

The Runtime always runs in the VPC's private subnets with its own security group.

- **Egress:**
  - **Interface endpoints:** ECR and CloudWatch Logs by default, plus the S3 gateway endpoint. `vpc.interfaceEndpoints` adds others, such as `bedrock` for model and embedding calls, and `ssm`.
  - **NAT gateway:** everything else. That's the Gateway, AgentCore Identity and Memory, Secrets Manager and public catalogs, and Bedrock and SSM unless their endpoints are added. The indexing task reaches Bedrock the same way.
- **Availability Zones:** AgentCore supports VPC connectivity only in some AZ IDs in each region. For example, Seoul supports `apne2-az1` to `az3`. Preflight checks the Runtime's subnets.
- **Network interfaces:** AgentCore keeps a Runtime's interfaces for up to 8 hours after the Runtime is deleted.

## Security

- **Tool arguments:** validated against the definition: enum membership, types, ranges, the length of `query`, the `limit` cap and unknown parameters. The model never chooses an index, a field, a URL or a query body.
- **Runtime role:** `es:ESHttpPost` and `es:ESHttpGet` on the alias's `_search` paths only, which the pipeline parameter travels on, and `bedrock:InvokeModel` on the embedding model only. The indexing role is separate, and only it writes.
- **Catalog bucket:** private, with every public access block on, TLS enforced, S3-managed encryption and versioning. Operators write it through the script; only the indexing role reads it. Prod retains it.
- **Catalog text:** it's the customer's own data. Local search results aren't screened by the guardrail ([tracker](../exec-plans/tech-debt-tracker.md)).
- **Logs:** the job's manifest and errors name fields and error types, never product values, and the search tool logs counts, timings and error types, never arguments.
- **Data residency:** in ap-northeast-2, Embed v4 is offered only through the `global` inference profile, so embedding requests, the catalog's text at indexing and the shopper's query at search, can be processed outside the region.

## Limits and cost

- **Memory:** a 1024-dimension float vector takes 4 KB. 100,000 products take about 0.4 GB plus the HNSW graph, which fits the Dev `r6g.large` node.
- **Calls:** indexing makes one Embed v4 call per 96 products. Each search with a query makes one more, which gives up after about 4 seconds. On Dev a hybrid search took 0.25 to 0.5 s in the tool, the embedding included (in ap-northeast-2, about 2 s for a session's first search, with the first connection to the domain), a keyword-only search 60 to 190 ms, and a filter-only search about 25 ms.
- **Setup time:** on Dev, creating the domain took about 15 minutes, and associating Nori about 30 more; the first load waits for it.
- **Query quality:** the vector half finds products by meaning: on Dev, "downpour puddles stomp", with no word in the catalog, ranked the rain boots first. A single short Korean word embeds less well ("장화" put them ninth), so the model's fuller queries, with filters, rank best.
- **Definition size:** the Runtime and the indexing task get the definition as compact JSON in environment values. AgentCore Runtime's CloudFormation resource takes at most 2,048 characters a value, so `definition_environment()` splits the JSON, never next to whitespace, across `CATALOG_DEFINITION`, `CATALOG_DEFINITION_2` and on, at most 2,048 UTF-8 bytes each, and `definition_from_env()` joins them. At most 8 values, 16 KB; synth fails above that. The example, about 2,400 characters, takes two.
- **Schema:** at most 12 filters, 100 values per filter and 100 characters per value.

## Alternatives considered

- **The model writes the query:** rejected; model input never chooses a raw query.
- **A generic `product.v2` card:** not chosen. Results map into `product.v1`, which the web renders, with every field required.
- **A Lambda indexer:** rejected for its 15-minute limit.
- **1536 dimensions, or int8:** 1024 float balances memory and recall.
- **Weighted min-max fusion:** its weights need tuning per catalog; RRF needs none.
- **Embeddings inside OpenSearch:** an ML connector and the `neural` query need fine-grained access control and role mapping. Embedding in the indexer and the agent is simpler to run.
- **A choice of network mode for the Runtime:** one mode means one network path to secure and test.
