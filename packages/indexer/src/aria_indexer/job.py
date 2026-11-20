"""The indexing job's steps, with the clients passed in so tests can fake them.

1. Read the catalog: a JSON array, or JSONL (one product per line).
2. Validate every product against the definition (`check_product`), and reject duplicate IDs and
   blank embedding text. Rejected products are counted by reason; reasons name fields, never values.
3. Build each product's embedding text from the definition's template.

A dry run stops there, with an estimate. An applied run goes on:

4. Refuse a version that already exists, and an alias that is a concrete index.
5. Create `<alias>-<version>` with the definition's strict mapping.
6. Embed the products with `search_document`, one batch of 96 per call, backing off on throttling,
   and bulk-load each batch as it's embedded, so memory holds one batch of vectors.
7. Refresh, then check the count.
8. Put the search pipeline `<alias>-hybrid`.
9. Move the alias to the new index in one `_aliases` call.

A failure before step 9 leaves the alias where it was. The manifest records what was done.
"""

import hashlib
import json
import math
import re
import time
from collections import Counter
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Any

from aria_catalog import embedding
from aria_catalog.definition import (
    DIMENSION,
    EMBEDDING_FIELD,
    CatalogDefinition,
    check_product,
    embedding_text,
    index_body,
    pipeline_body,
    pipeline_name,
)

MANIFEST = "aria.catalog-index.v1"
NAME = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
BATCH = embedding.BATCH
# Bedrock errors worth waiting out. Any other error stops the job.
RETRYABLE = ("ThrottlingException", "TooManyRequestsException", "ServiceUnavailableException", "ModelNotReadyException")
ATTEMPTS = 6
BACKOFF_SECONDS = 2.0
MAX_BACKOFF_SECONDS = 60.0
PROGRESS_EVERY = 10  # batches between progress lines


class JobError(Exception):
    """The job can't go on. The message names the step and never holds product values."""


class _BadLine:
    """A JSONL line that isn't JSON."""


BAD_LINE = _BadLine()


@dataclass
class Validation:
    total: int = 0
    accepted: list[dict] = field(default_factory=list)
    texts: list[str] = field(default_factory=list)
    rejected: int = 0
    reasons: Counter = field(default_factory=Counter)


def parse_catalog(raw: bytes) -> tuple[str, list]:
    """The catalog's format ("json" or "jsonl") and its entries. A JSONL line that isn't JSON is BAD_LINE."""
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        raise JobError("the catalog is not UTF-8 text") from None
    if text.lstrip().startswith("["):
        try:
            products = json.loads(text)
        except json.JSONDecodeError as error:
            raise JobError(f"the catalog is not a valid JSON array (line {error.lineno}, column {error.colno})") from None
        return "json", products
    entries: list = []
    for line in text.splitlines():
        if not line.strip():
            continue
        try:
            entries.append(json.loads(line))
        except json.JSONDecodeError:
            entries.append(BAD_LINE)
    return "jsonl", entries


def validate(definition: CatalogDefinition, entries: list) -> Validation:
    """Split the entries into accepted products, with their embedding text, and rejection counts."""
    result = Validation(total=len(entries))
    seen: set[str] = set()
    for entry in entries:
        if entry is BAD_LINE:
            problems = ["the line is not valid JSON"]
        else:
            problems = check_product(definition, entry)
        text = ""
        if not problems:
            product_id = str(entry[definition.id_field])
            text = embedding_text(definition, entry)
            if product_id in seen:
                problems = [f"{definition.id_field}: duplicate ID"]
            elif not text:
                problems = ["embedding text: blank"]
        if problems:
            result.rejected += 1
            result.reasons.update(problems)
            continue
        seen.add(product_id)
        result.accepted.append(entry)
        result.texts.append(text)
    return result


def document(definition: CatalogDefinition, product: Mapping, vector: list[float]) -> dict:
    """The indexed document: the definition's fields only, since the mapping is strict, and the vector."""
    source = {f.name: product[f.name] for f in definition.fields if product.get(f.name) is not None}
    source[EMBEDDING_FIELD] = vector
    return source


def _error_code(error: Exception) -> str:
    response = getattr(error, "response", None)
    if isinstance(response, Mapping):
        return str(response.get("Error", {}).get("Code", ""))
    return ""


def embed_batch(texts: list[str], *, model: str, client, sleep: Callable[[float], None], log: Callable[[str], None]):
    """One batch's vectors, retrying throttling and unavailability with exponential backoff."""
    for attempt in range(1, ATTEMPTS + 1):
        try:
            return embedding.embed(texts, "search_document", model=model, client=client)
        except Exception as error:
            code = _error_code(error)
            if code not in RETRYABLE or attempt == ATTEMPTS:
                raise
            delay = min(MAX_BACKOFF_SECONDS, BACKOFF_SECONDS * 2 ** (attempt - 1))
            log(f"embedding {code}; retrying in {delay:g} s (attempt {attempt + 1} of {ATTEMPTS})")
            sleep(delay)
    raise AssertionError("unreachable")


def bulk_load(client, index: str, documents: list[tuple[str, dict]]) -> None:
    """Index documents in one bulk request to /<index>/_bulk. A rejected item fails the job."""
    lines = []
    for doc_id, source in documents:
        lines.append(json.dumps({"index": {"_id": doc_id}}))
        lines.append(json.dumps(source, ensure_ascii=False))
    response = client.bulk(body="\n".join(lines) + "\n", index=index)
    if response.get("errors"):
        # Error types only: an error's reason can quote the document's values.
        types = Counter(
            (item.get("index") or {}).get("error", {}).get("type", "unknown")
            for item in response.get("items", [])
            if (item.get("index") or {}).get("error")
        )
        summary = ", ".join(f"{name} x{count}" for name, count in sorted(types.items()))
        raise JobError(f"the bulk load rejected {sum(types.values())} documents ({summary}); the alias is unchanged")


def alias_indices(client, alias: str) -> list[str]:
    """The indexes behind the alias now ([] when there is none). Refuses a concrete index named like the alias."""
    if not client.indices.exists(index=alias):
        return []
    found = client.indices.get(index=alias)
    if alias in found:
        raise JobError(f"a concrete index named {alias!r} exists, so it can't be an alias; choose another --alias")
    return sorted(found)


def check_names(alias: str, version: str) -> None:
    for name, value in (("--alias", alias), ("--version", version)):
        if not NAME.fullmatch(value):
            raise JobError(f"{name} must be 1 to 63 lowercase letters, digits and hyphens, starting with a letter or digit")


def manifest(definition: CatalogDefinition, raw: bytes, source: str, catalog_format: str, validation: Validation,
             *, alias: str, version: str, replicas: int | None) -> dict:
    """What the job read, accepted and would load. Counts and names only, never product content."""
    accepted = len(validation.accepted)
    return {
        "manifest": MANIFEST,
        "catalog": definition.name,
        "source": source,
        "sourceSha256": hashlib.sha256(raw).hexdigest(),
        "format": catalog_format,
        "products": validation.total,
        "accepted": accepted,
        "rejected": validation.rejected,
        "rejectedReasons": dict(sorted(validation.reasons.items())),
        "alias": alias,
        "index": f"{alias}-{version}",
        "pipeline": pipeline_name(alias),
        "replicas": replicas,
        "embeddingModel": definition.embedding_model,
        "embeddingCalls": math.ceil(accepted / BATCH),
        "embeddingCharacters": sum(len(text) for text in validation.texts),
        "vectorBytes": accepted * DIMENSION * 4,
        "applied": False,
        "promoted": False,
    }


@dataclass
class Job:
    """One run. `manifest` grows as the steps finish, so a failure still reports how far it got."""

    definition: CatalogDefinition
    alias: str
    version: str
    replicas: int | None  # None only in a dry run
    log: Callable[[str], None]
    sleep: Callable[[float], None] = time.sleep
    manifest: dict = field(default_factory=dict)

    def validate(self, raw: bytes, source: str) -> Validation:
        check_names(self.alias, self.version)
        catalog_format, entries = parse_catalog(raw)
        result = validate(self.definition, entries)
        self.manifest = manifest(self.definition, raw, source, catalog_format, result,
                                 alias=self.alias, version=self.version, replicas=self.replicas)
        self.log(f"read {result.total} products ({catalog_format}): {len(result.accepted)} accepted, "
                 f"{result.rejected} rejected")
        if not result.accepted:
            raise JobError("no product passed validation; nothing to index")
        return result

    def apply(self, validation: Validation, *, opensearch, bedrock) -> dict:
        index = self.manifest["index"]
        if opensearch.indices.exists(index=index):
            raise JobError(f"index {index} already exists; choose a new --version")
        previous = alias_indices(opensearch, self.alias)

        opensearch.indices.create(index=index, body=index_body(self.definition, self.replicas))
        self.manifest["created"] = True
        self.log(f"created {index}")

        products, texts = validation.accepted, validation.texts
        batches = math.ceil(len(products) / BATCH)
        for number, start in enumerate(range(0, len(products), BATCH), 1):
            batch = products[start : start + BATCH]
            vectors = embed_batch(texts[start : start + BATCH], model=self.definition.embedding_model,
                                  client=bedrock, sleep=self.sleep, log=self.log)
            bulk_load(opensearch, index, [
                (str(product[self.definition.id_field]), document(self.definition, product, vector))
                for product, vector in zip(batch, vectors)
            ])
            self.manifest["loaded"] = start + len(batch)
            if number % PROGRESS_EVERY == 0 or number == batches:
                self.log(f"embedded and loaded {start + len(batch)} of {len(products)} ({number}/{batches} batches)")

        opensearch.indices.refresh(index=index)
        count = int(opensearch.count(index=index)["count"])
        self.manifest["indexed"] = count
        if count != len(products):
            raise JobError(f"{index} holds {count} documents, not the {len(products)} accepted; the alias is unchanged")

        opensearch.search_pipeline.put(id=self.manifest["pipeline"], body=pipeline_body())
        self.log(f"put the search pipeline {self.manifest['pipeline']}")

        actions: list[dict[str, Any]] = [{"remove": {"index": name, "alias": self.alias}} for name in previous]
        actions.append({"add": {"index": index, "alias": self.alias}})
        opensearch.indices.update_aliases(body={"actions": actions})
        self.manifest.update(applied=True, promoted=True, previousIndices=previous)
        self.log(f"moved {self.alias} to {index}" + (f" from {', '.join(previous)}" if previous else ""))
        return self.manifest
