# packages/indexer

The catalog indexing job: `python -m aria_indexer` validates a JSON or JSONL catalog against its catalog definition, embeds it with Cohere Embed v4 and loads it into a versioned OpenSearch index behind the search alias. It runs as a Fargate task in the stage's VPC (`../infra/components/catalog_indexer.py`), started by `scripts/index_catalog.sh`.

- `src/aria_indexer/__main__.py`: arguments, AWS clients created on first use, the manifest on stdout.
- `src/aria_indexer/job.py`: the steps, with the clients passed in.
- The definition, the index body, the pipeline and the embedding calls come from `../catalog` (`aria_catalog`).

## Rules

- A dry run is the default and writes nothing; it calls neither Bedrock nor OpenSearch. Only `--apply` writes.
- The definition is `CATALOG_DEFINITION` and the values after it, the same JSON the Runtime gets; empty means the packaged example. Never take it, an index name or a field from the catalog file.
- Each load is a new index, `<alias>-<version>`. Refuse a version that exists and an alias that is a concrete index; move the alias only after the count check, in one `_aliases` call.
- A failure before promotion leaves the alias where it was. The task's role can't delete, so never add a delete to the job without a reviewed change to the role.
- Stay on the paths the task role grants: the alias, `<alias>-*`, `_aliases` and `_search/pipeline/<alias>-hybrid`. `test_indexer.py` checks every request.
- The manifest, the progress lines and every error name counts, fields, steps and error types, never product values: OpenSearch and Bedrock error messages can quote documents.
- Embed 96 texts per call with `search_document`, and retry only throttling and unavailability, with backoff.

## Commands

- `uv run --frozen pytest`. Tests never call AWS: OpenSearch, Bedrock and S3 are faked.
- The image builds from the repository root with the `packages/` context (`packages/indexer/Dockerfile`, [container builds](../../docs/guides/release.md#container-build-and-runtime-layout)).
- Running it against a stage is an operator action: [index the catalog](../../docs/guides/operations.md#index-the-catalog).

## Read

- [Catalog search design](../../docs/design-docs/catalog-search.md#the-indexing-job), [catalog runbook](../../docs/guides/operations.md#catalog-runbook)
- [Bring your own catalog](../../docs/guides/integrations.md#bring-your-own-catalog), [OpenSearch notes](../../docs/references/opensearch-hybrid-search-notes.md), [Embed v4 notes](../../docs/references/bedrock-embed-v4-notes.md)
