# The catalog indexing task. Matching Python 3.14 builder/runtime digests; it runs on Fargate linux/arm64.
FROM --platform=linux/arm64 public.ecr.aws/chainguard/python@sha256:466bed9745baea18d220d1d048439d9daaa72ace864cfbaffe48ce4e096c0da9 AS builder
USER root
WORKDIR /workspace/indexer
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
RUN python -m pip install --no-cache-dir --break-system-packages uv==0.12.14

COPY catalog /workspace/catalog
COPY indexer/pyproject.toml indexer/uv.lock /workspace/indexer/
RUN uv sync --frozen --no-cache --no-dev --no-editable --python /usr/bin/python

FROM --platform=linux/arm64 public.ecr.aws/chainguard/python@sha256:e1a792bc1e72e6395fbf0504bd040945df8999e3e0b22728c279767f771a04ee
WORKDIR /workspace/indexer
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/indexer/src
ENV PATH="/workspace/indexer/.venv/bin:$PATH"
COPY --from=builder /workspace/indexer/.venv /workspace/indexer/.venv
COPY indexer/src /workspace/indexer/src

# scripts/index_catalog.sh passes the arguments as the task's command override.
USER 10001:10001
ENTRYPOINT ["python", "-m", "aria_indexer"]
CMD ["--help"]
