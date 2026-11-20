# Keep the Python ABI identical in the builder and minimal runtime.
FROM public.ecr.aws/chainguard/python@sha256:466bed9745baea18d220d1d048439d9daaa72ace864cfbaffe48ce4e096c0da9 AS builder
USER root
WORKDIR /workspace/api
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy
ENV UV_PYTHON_DOWNLOADS=never
RUN python -m pip install --no-cache-dir --break-system-packages uv==0.12.14

COPY contracts /workspace/contracts
COPY api/pyproject.toml api/uv.lock /workspace/api/
RUN uv sync --frozen --no-cache --no-dev --no-editable --python /usr/bin/python

FROM public.ecr.aws/chainguard/python@sha256:e1a792bc1e72e6395fbf0504bd040945df8999e3e0b22728c279767f771a04ee
WORKDIR /workspace/api
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PYTHONPATH=/workspace/api/src
ENV PATH="/workspace/api/.venv/bin:$PATH"
# The server binds loopback unless HOST is set. In the task, the ALB must reach
# the container, so listen on all interfaces; the security group admits only the ALB.
ENV HOST=0.0.0.0
COPY --from=builder /workspace/api/.venv /workspace/api/.venv
COPY api/src /workspace/api/src

EXPOSE 8000

USER 10001:10001
ENTRYPOINT []
CMD ["opentelemetry-instrument", "python", "src/server.py"]
