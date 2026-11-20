#!/bin/sh
# Read-only AWS inspection plus local synthesis. Does not deploy or create change sets.
set -eu
: "${ARIA_CONFIG:?Set the reviewed deployment configuration path}"
if [ -n "$(git status --porcelain)" ]; then
    echo "Deployment plans require a clean, committed checkout" >&2
    exit 1
fi
export ARIA_CONFIG="$(realpath "$ARIA_CONFIG")"
aria_root=$(pwd)
uv run --project packages/api --frozen python scripts/preflight.py
mkdir -p artifacts
# deploy.sh builds the image assets staged here with a container runtime, so check for
# one before planning. Prefer Docker, fall back to Finch (Docker-compatible). Override
# by exporting CDK_DOCKER.
export CDK_DOCKER="${CDK_DOCKER:-$(command -v docker || command -v finch)}"
[ -n "$CDK_DOCKER" ] || { echo "No container runtime found (docker/finch); set CDK_DOCKER" >&2; exit 1; }
echo "Container runtime: CDK_DOCKER=$CDK_DOCKER" >&2
# Call the workspace CDK directly: packages/infra has no package.json, so
# `pnpm exec` would run from the workspace root and miss packages/infra/cdk.json.
cdk="$aria_root/node_modules/.bin/cdk"
cd packages/infra
# Start from an empty assembly: synth never removes old assets, and the plan hashes
# every file in the folder, so stale image contexts would be "reviewed" too.
rm -rf "$aria_root/artifacts/cdk.out"
"$cdk" synth --lookups false --output "$aria_root/artifacts/cdk.out"
"$cdk" diff --app "$aria_root/artifacts/cdk.out" --change-set=false --lookups false > "$aria_root/artifacts/deployment-diff.txt" 2>&1
cd "$aria_root"
python3 - <<'PY'
import hashlib, json, os, subprocess
from pathlib import Path
plan = Path("artifacts/cdk.out")
files = {str(p.relative_to(plan)): hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(plan.rglob("*")) if p.is_file()}
record = {
    "commit": subprocess.check_output(["git", "rev-parse", "HEAD"], text=True).strip(),
    "configSha256": hashlib.sha256(Path(os.environ["ARIA_CONFIG"]).read_bytes()).hexdigest(),
    "files": files,
}
Path("artifacts/deployment-plan.json").write_text(json.dumps(record, indent=2) + "\n")
PY
