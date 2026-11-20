# Matching Python 3.14 builder/runtime digests; AgentCore requires linux/arm64.
FROM --platform=linux/arm64 public.ecr.aws/chainguard/python@sha256:466bed9745baea18d220d1d048439d9daaa72ace864cfbaffe48ce4e096c0da9 AS builder
USER root
WORKDIR /workspace/agent
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
RUN python -m pip install --no-cache-dir --break-system-packages uv==0.12.14

COPY contracts /workspace/contracts
COPY catalog /workspace/catalog
COPY agent/pyproject.toml agent/uv.lock /workspace/agent/
RUN uv sync --frozen --no-cache --no-dev --no-editable --python /usr/bin/python

FROM --platform=linux/arm64 public.ecr.aws/chainguard/python@sha256:e1a792bc1e72e6395fbf0504bd040945df8999e3e0b22728c279767f771a04ee
WORKDIR /workspace/agent
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/agent/src
ENV PATH="/workspace/agent/.venv/bin:$PATH"
COPY --from=builder /workspace/agent/.venv /workspace/agent/.venv
COPY agent/src /workspace/agent/src

# AgentCore Runtime contract: serve on 8080.
EXPOSE 8080

# opentelemetry-instrument wires the ADOT sidecar. Do NOT set OTEL_* env vars —
# AgentCore configures them for hosted agents.
USER 10001:10001
ENTRYPOINT []
CMD [ "opentelemetry-instrument", "python", "src/app.py"]
