#!/bin/sh
# Only an operator/manual protected CI job may execute this script.
set -eu
: "${ARIA_CONFIG:?Set the reviewed deployment configuration path}"
: "${ARIA_ALLOW_DEPLOY:?Explicit deployment authorization is required}"
[ "$ARIA_ALLOW_DEPLOY" = yes ]
export ARIA_CONFIG="$(realpath "$ARIA_CONFIG")"
python3 - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path
record = json.loads(Path("artifacts/deployment-plan.json").read_text())
revision = subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip()
if record["commit"] != revision or record["configSha256"] != hashlib.sha256(Path(os.environ["ARIA_CONFIG"]).read_bytes()).hexdigest():
    raise SystemExit("Commit/configuration differs from the reviewed deployment plan")
plan = Path("artifacts/cdk.out")
files = {str(p.relative_to(plan)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(plan.rglob("*")) if p.is_file()}
if files != record["files"]:
    raise SystemExit("Cloud assembly differs from the reviewed deployment plan")
PY
uv run --project packages/api --frozen python scripts/preflight.py
# Publishing builds the image assets, so CDK needs a container runtime here too.
# Prefer Docker, fall back to Finch. Override by exporting CDK_DOCKER.
export CDK_DOCKER="${CDK_DOCKER:-$(command -v docker || command -v finch)}"
[ -n "$CDK_DOCKER" ] || { echo "No container runtime found (docker/finch); set CDK_DOCKER" >&2; exit 1; }
# ARIA_ALLOW_DEPLOY=yes is the approval for the reviewed plan verified above, so CDK
# runs with --require-approval never and needs no terminal. In CI, run this script as
# a protected manual step.
# Normally the whole reviewed assembly. ARIA_DEPLOY_STACKS (space-separated stack names)
# deploys only those stacks from the same verified assembly, for the two-step deploy that
# removing a cross-stack export needs (docs/design-docs/deployment-safety.md).
if [ -n "${ARIA_DEPLOY_STACKS:-}" ]; then
    # shellcheck disable=SC2086
    pnpm exec cdk deploy --app artifacts/cdk.out $ARIA_DEPLOY_STACKS --exclusively --require-approval never --lookups false
else
    pnpm exec cdk deploy --app artifacts/cdk.out --all --require-approval never --lookups false
fi
