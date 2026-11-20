"""Index a catalog into OpenSearch: `python -m aria_indexer --catalog <s3://bucket/key or path> --version <v>`.

A dry run, the default, reads and validates the catalog and estimates the embedding work; it
calls neither Bedrock nor OpenSearch. `--apply` builds `<alias>-<version>`, puts the search
pipeline and promotes the alias. The definition is the JSON the Runtime gets, in CATALOG_DEFINITION
and the values after it (the packaged example when it's empty); --apply reads the domain from OPENSEARCH_HOST.

It prints one JSON manifest line on stdout, also on failure (with "error"), and progress lines on
stderr. Neither holds product content. Run in the VPC as the stage's indexing task, through
scripts/index_catalog.sh.
"""

import argparse
import json
import os
import sys
from collections.abc import Mapping
from functools import cached_property
from pathlib import Path

from aria_catalog.definition import DefinitionError, definition_from_env

from aria_indexer.job import MANIFEST, Job, JobError


def log(message: str) -> None:
    print(f"aria_indexer: {message}", file=sys.stderr, flush=True)


def region(environ: Mapping[str, str]) -> str:
    value = environ.get("AWS_REGION") or environ.get("AWS_DEFAULT_REGION")
    if not value:
        raise JobError("set AWS_REGION or AWS_DEFAULT_REGION")
    return value


class AwsClients:
    """The job's AWS clients, created on first use, so a local dry run needs no AWS at all."""

    def __init__(self, environ: Mapping[str, str]):
        self.environ = environ

    @cached_property
    def s3(self):
        import boto3

        return boto3.client("s3", region_name=region(self.environ))

    @cached_property
    def bedrock(self):
        # Its own client, unlike the search path's: an indexing run can wait, so longer timeouts,
        # and adaptive retries that slow down under throttling before the job's own backoff.
        import boto3
        from botocore.config import Config

        config = Config(connect_timeout=10, read_timeout=120, retries={"mode": "adaptive", "max_attempts": 8})
        return boto3.client("bedrock-runtime", region_name=region(self.environ), config=config)

    @cached_property
    def opensearch(self):
        import boto3
        from opensearchpy import AWSV4SignerAuth, OpenSearch, RequestsHttpConnection

        host = (self.environ.get("OPENSEARCH_HOST") or "").replace("https://", "").rstrip("/")
        if not host:
            raise JobError("OPENSEARCH_HOST is not set")
        # The domain enforces HTTPS and SigV4, and is reachable only from inside the VPC.
        return OpenSearch(
            hosts=[{"host": host, "port": 443}],
            http_auth=AWSV4SignerAuth(boto3.Session().get_credentials(), region(self.environ)),
            use_ssl=True,
            verify_certs=True,
            connection_class=RequestsHttpConnection,
            timeout=120,
            max_retries=3,
            retry_on_timeout=True,
            retry_on_status=(429, 502, 503, 504),
        )


def read_catalog(location: str, clients: AwsClients) -> bytes:
    """The catalog file's bytes, from s3://bucket/key or a local path."""
    if location.startswith("s3://"):
        bucket, _, key = location.removeprefix("s3://").partition("/")
        if not bucket or not key:
            raise JobError("--catalog must be s3://<bucket>/<key> or a local path")
        return clients.s3.get_object(Bucket=bucket, Key=key)["Body"].read()
    path = Path(location)
    if not path.is_file():
        raise JobError(f"--catalog {location} is not a file")
    return path.read_bytes()


def describe(error: Exception) -> str:
    """An error, without anything that could quote product content."""
    if isinstance(error, JobError):
        return str(error)
    name = type(error).__name__
    response = getattr(error, "response", None)
    if isinstance(response, Mapping) and "Error" in response:  # botocore ClientError
        operation = getattr(error, "operation_name", "")
        return f"{name}: {response['Error'].get('Code', '')}" + (f" from {operation}" if operation else "")
    status = getattr(error, "status_code", None)
    if isinstance(status, int):  # opensearch-py TransportError: its info can quote documents
        return f"{name}: HTTP {status} {getattr(error, 'error', '')}".rstrip()
    return name


def parser(environ: Mapping[str, str]) -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="python -m aria_indexer", description=__doc__.split("\n", 1)[0])
    p.add_argument("--catalog", required=True, help="s3://<bucket>/<key> or a local path: a JSON array or JSONL")
    p.add_argument("--version", required=True, help="the new index is <alias>-<version>; it must not exist yet")
    p.add_argument("--replicas", type=int, choices=[0, 1, 2],
                   help="replicas per shard (default 1 with --apply; a dry run without it reports none)")
    p.add_argument("--alias", default=environ.get("OPENSEARCH_PRODUCT_INDEX") or "products",
                   help="the alias the agent searches (default OPENSEARCH_PRODUCT_INDEX, else products)")
    p.add_argument("--apply", action="store_true", help="write: create the index, load it and promote the alias")
    return p


def main(argv: list[str] | None = None, *, environ: Mapping[str, str] = os.environ, clients=None, sleep=None) -> int:
    args = parser(environ).parse_args(argv)
    clients = clients or AwsClients(environ)
    try:
        definition = definition_from_env(environ)
    except DefinitionError as error:
        log(f"CATALOG_DEFINITION: {error}")
        return 2
    # A dry run doesn't choose the replica count: scripts/index_catalog.sh passes the stack's at --apply.
    replicas = args.replicas if args.replicas is not None else (1 if args.apply else None)
    job = Job(definition=definition, alias=args.alias, version=args.version, replicas=replicas, log=log,
              **({"sleep": sleep} if sleep else {}))
    try:
        validation = job.validate(read_catalog(args.catalog, clients), args.catalog)
        if args.apply:
            job.apply(validation, opensearch=clients.opensearch, bedrock=clients.bedrock)
        else:
            log("dry run: nothing written; run with --apply to index")
    except Exception as error:
        message = describe(error)
        log(f"failed: {message}")
        print(json.dumps({"manifest": MANIFEST, **job.manifest, "error": message}, ensure_ascii=False), flush=True)
        return 1
    print(json.dumps(job.manifest, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
