"""The catalog indexing job: validate a JSON or JSONL catalog against its definition, embed it with
Cohere Embed v4 and load it into a versioned OpenSearch index behind the search alias.

`python -m aria_indexer` runs it (see `__main__.py`); `aria_indexer.job` holds the steps.
"""
