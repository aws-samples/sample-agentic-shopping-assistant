# OpenSearch hybrid search: verified and documented behavior

What ARIA relies on about OpenSearch 3.5 on Amazon OpenSearch Service: the vector index, the hybrid query fused by reciprocal rank fusion (RRF), the Nori plugin and the IAM actions. "Verified" means observed on a deployed stage; "Documented" means stated in the [OpenSearch 3.5 documentation](https://docs.opensearch.org/3.5/vector-search/ai-search/hybrid-search/index/), the [Amazon OpenSearch Service developer guide](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/what-is.html) or, for the Nori analyzer's name, the plugin's source. Links go to the 3.5 pages, the engine ARIA deploys.

## The engine

- **Documented:** Amazon OpenSearch Service supports OpenSearch 3.5 ([what is OpenSearch Service](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/what-is.html)).

- **Verified:** a VPC-only `OpenSearch_3.5` domain on one `r6g.large.search` node takes about 15 minutes to create. In ap-northeast-2, one of three `r6g.large.search` data nodes across three AZs with three dedicated masters took about 14.

`packages/infra/components/opensearch_cluster.py` deploys it (`ENGINE_VERSION = OPENSEARCH_3_5`).

## The vector index

- **Documented:** a vector index needs `index.knn: true` in its settings ([creating a vector index](https://docs.opensearch.org/3.5/vector-search/creating-vector-index/)).
- **Documented:** a `knn_vector` field takes a `dimension` from 1 to 16,000, a `space_type` and a `method` ([k-NN vector](https://docs.opensearch.org/3.5/mappings/supported-field-types/knn-vector/)).
- **Documented:** Faiss HNSW supports `cosinesimil` from OpenSearch 2.19. Faiss normalizes the vectors to unit length at indexing, so stored values differ from the input ([methods and engines](https://docs.opensearch.org/3.5/mappings/supported-field-types/knn-methods-engines/)).
- **Documented:** Faiss HNSW takes `m` (default 16, kept between 2 and 100) and `ef_construction` (default 100), neither updatable after the index is created. Its `ef_search` defaults to 100 and can't be updated either ([methods and engines](https://docs.opensearch.org/3.5/mappings/supported-field-types/knn-methods-engines/)).

- **Verified:** 3.5 accepts `index_body`'s mapping: a strict mapping, `nori` text fields and a 1024-dimension Faiss HNSW `cosinesimil` vector.

`definition.index_body` builds the mapping: `index.knn: true`, one shard, a strict mapping, and `embedding` as a 1024-dimension `knn_vector` with Faiss HNSW, `cosinesimil`, `m` 16 and `ef_construction` 128. A search returns only the card's fields (`source_fields`), never the vector.

## The k-NN query and its filter

- **Documented:** a `knn` query takes the query `vector`, `k` (1 to 10,000) and an optional `filter`. A filter works only with the Faiss or Lucene engines ([k-NN query](https://docs.opensearch.org/3.5/query-dsl/specialized/k-nn/index/)).
- **Documented:** Faiss filtering with HNSW is available from OpenSearch 2.9. Faiss chooses between an exact search over the pre-filtered documents and an approximate search with modified post-filtering ([efficient k-NN filtering](https://docs.opensearch.org/3.5/vector-search/filter-search-knn/efficient-knn-filtering/)).

- **Documented:** the choice between exact and approximate search depends on the number of documents, `k` and the index setting `knn.advanced.filtered_exact_search_threshold` ([efficient k-NN filtering](https://docs.opensearch.org/3.5/vector-search/filter-search-knn/efficient-knn-filtering/)).

`definition.search_body` gives the `knn` half the same `term` and `range` filters as the lexical half, with `k` from `[catalog.search].k` (default 50, at most 100, so never above the default `ef_search`).

## The hybrid query

- **Documented:** a `hybrid` query holds one to five `queries`. It scores each subquery separately, and a search pipeline normalizes and combines the scores ([hybrid query](https://docs.opensearch.org/3.5/query-dsl/compound/hybrid/)).
- **Documented:** a `hybrid` query must be the top-level query; it can't sit inside `function_score`, `constant_score`, `script_score` or `boosting`.
- **Documented:** on 3.5 its parameters are `queries` and an optional top-level `filter`, applied to every subquery. Later releases add more, such as per-subquery `weights`, which a 3.5 domain doesn't take ([hybrid query, 3.5](https://docs.opensearch.org/3.5/query-dsl/compound/hybrid/)).
- **Documented:** the request names its pipeline in the query string, `/<index>/_search?search_pipeline=<pipeline>`, or the index sets a default search pipeline ([hybrid search](https://docs.opensearch.org/3.5/vector-search/ai-search/hybrid-search/index/)).
- **Documented:** `pagination_depth`, from OpenSearch 2.19, is the most results each subquery returns from each shard. Without it, each subquery returns `from + size`. Changing it can change the order after fusion ([paginating hybrid query results](https://docs.opensearch.org/3.5/vector-search/ai-search/hybrid-search/pagination/)).

- **Verified:** the `hybrid` body, with `pagination_depth` and `search_pipeline=<alias>-hybrid`, returns fused results. When the lexical half matches nothing (the `multi_match` is a `must`), the results are the `knn` half's alone.
- **Verified:** before the first load, with neither the alias nor the pipeline in place, a search fails with HTTP 400 (`RequestError`).

With a query and its embedding, `definition.search_body` builds a top-level `hybrid` query of two parts, a `bool` with `multi_match` and the `knn` query, with `pagination_depth` set to the larger of `k` and the tool's `limit`. The lexical half then brings as many candidates to fusion as the `knn` half, and never fewer than `limit`. `search.py` sends it with `search_pipeline=<alias>-hybrid`. A filter-only search, or one whose embedding failed, is the `bool` alone, sent without a pipeline.

## RRF: the score ranker processor

- **Documented:** `score-ranker-processor`, from OpenSearch 2.19, is a search phase results processor that combines the subqueries by reciprocal rank fusion. `combination.technique` is required and must be `rrf`. `combination.rank_constant` is at least 1, default 60. `combination.parameters.weights` is optional, equal by default ([score ranker processor](https://docs.opensearch.org/3.5/search-plugins/search-pipelines/score-ranker-processor/)).
- **Documented:** a pipeline is created with `PUT /_search/pipeline/<name>`, with the processor under `phase_results_processors`.

- **Verified:** `PUT /_search/pipeline/<alias>-hybrid` with `pipeline_body` succeeds on 3.5, and searches through it.

`definition.pipeline_body` is the documented minimal pipeline: `score-ranker-processor` with `technique` `rrf`, the default rank constant and equal weights. The indexer puts it as `<alias>-hybrid` (`pipeline_name`).

## The Nori plugin

- **Documented:** Nori Analysis is an optional plugin for OpenSearch 1.3 and later, not preinstalled. Optional plugins use the `ZIP-PLUGIN` package type. Each package is compatible with one OpenSearch version and can only be associated with domains on that version ([plugins by engine version](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/supported-plugins.html)).
- **Documented:** `DescribePackages` filters on `PackageID`, `PackageName`, `PackageStatus`, `PackageType`, `EngineVersion` and `PackageOwner` ([DescribePackagesFilter](https://docs.aws.amazon.com/opensearch-service/latest/APIReference/API_DescribePackagesFilter.html)). A package's `EngineVersion` has the form `OpenSearch_X.Y`, and its status is `AVAILABLE` when it can be used ([PackageDetails](https://docs.aws.amazon.com/opensearch-service/latest/APIReference/API_PackageDetails.html)).
- **Documented:** associating or dissociating a plugin runs a blue/green deployment on the domain. A package can't be dissociated while an index still uses it: the dissociation fails ([importing and managing packages](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/custom-packages.html)).
- **Documented:** an association's `DomainPackageStatus` is `ASSOCIATING`, `ASSOCIATION_FAILED`, `ACTIVE`, `DISSOCIATING` or `DISSOCIATION_FAILED` ([DomainPackageDetails](https://docs.aws.amazon.com/opensearch-service/latest/APIReference/API_DomainPackageDetails.html)).
- **Documented (plugin source):** the plugin registers the `nori` analyzer, the `nori_tokenizer` and the `nori_part_of_speech`, `nori_readingform` and `nori_number` filters ([`AnalysisNoriPlugin.java`](https://github.com/opensearch-project/OpenSearch/blob/main/plugins/analysis-nori/src/main/java/org/opensearch/plugin/analysis/nori/AnalysisNoriPlugin.java)). The OpenSearch 3.5 documentation has no Nori page.
- **Verified:** in us-west-2, `DescribePackages` with those three filters returns the package, and `AssociatePackage` on a new domain went from `ASSOCIATING` to `ACTIVE` in about 30 minutes. In ap-northeast-2 it found the region's own package, and associating it with the three-AZ domain also took about 30 minutes; an index then loaded with one replica.
- **Verified:** the `nori` analyzer splits compound nouns: the query "장갑" matches "벙어리장갑", "벙어리" matches it too, and "부츠" matches "레인부츠" but not a product named only "Hiking Boots" in English.

How ARIA uses it:

- `scripts/preflight.py` `check_nori` asks `DescribePackages` for `analysis-nori`, `OpenSearch_3.5` and `ZIP-PLUGIN`, and fails unless one is `AVAILABLE`.
- `NoriPlugin` in `opensearch_cluster.py` looks the package ID up with the same filters at deploy, then associates it with `AssociatePackage`. The association is retained in every stage and never dissociated, because the service refuses while an index still uses the plugin; a deleted domain takes its plugin with it.
- `scripts/index_catalog.sh --apply` doesn't wait for the association: it reads `ListPackagesForDomain` and stops unless the status is `ACTIVE`.
- With `analyzer = "nori"`, `index_body` sets `analyzer: nori` on every `text` field.

## IAM actions

- **Documented:** each HTTP method to the domain has its own action: `es:ESHttpGet`, `es:ESHttpHead`, `es:ESHttpPost`, `es:ESHttpPut`, `es:ESHttpPatch` and `es:ESHttpDelete`, on the `domain` resource ([actions for OpenSearch Service](https://docs.aws.amazon.com/service-authorization/latest/reference/list_es.html)).
- **Documented:** a resource ARN can name an index or a path under the domain, with wildcards, such as `domain/<domain>/commerce*` or `domain/<domain>/commerce-data/_search` ([identity and access management](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ac.html)).
- **Documented:** `ESHttpGet` and `ESHttpHead` are Read actions; `ESHttpPost`, `ESHttpPut`, `ESHttpPatch` and `ESHttpDelete` are Write ([actions for OpenSearch Service](https://docs.aws.amazon.com/service-authorization/latest/reference/list_es.html)).
- **Documented:** an identity-based deny overrides a resource-policy allow, and an identity-based allow works where the domain's resource policy grants the account ([identity and access management](https://docs.aws.amazon.com/opensearch-service/latest/developerguide/ac.html)). The domain's policy grants `es:*` to the account, so each role's own policy scopes its access.
- **Documented:** `DescribePackages` has no resource type; `AssociatePackage` is on the domain ([actions for OpenSearch Service](https://docs.aws.amazon.com/service-authorization/latest/reference/list_es.html)).

- **Verified:** those grants suffice: the Runtime searches through the pipeline, and the indexing task creates the index, bulk-loads it, refreshes and counts it, puts the pipeline and moves the alias.

The Runtime role gets `es:ESHttpPost` and `es:ESHttpGet` on `<alias>/_search` and `<alias>/_search/*` only (`agent_runtime_stack.py`). The indexing task's role gets GET, HEAD, PUT and POST on the alias, `<alias>-*`, `_aliases` and `_search/pipeline/<alias>-hybrid`, and no `es:ESHttpDelete` (`components/catalog_indexer.py`).
