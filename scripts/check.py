#!/usr/bin/env python3
"""Local/CI release checks. No AWS credentials, model invocations or deployments."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PACKAGES = ["contracts", "api", "agent", "catalog", "indexer", "mcp-servers/saved-items-lambda", "mcp-servers/gateway-interceptor", "infra"]


def main():
    env = {**os.environ, "AWS_EC2_METADATA_DISABLED": "true", "AWS_ACCESS_KEY_ID": "testing", "AWS_SECRET_ACCESS_KEY": "testing", "AWS_SESSION_TOKEN": "", "AWS_DEFAULT_REGION": "ap-northeast-2"}  # nosec B105 # placeholder AWS credentials, so a test can never use real ones
    for package in PACKAGES:
        cwd = ROOT / "packages" / package
        subprocess.run(["uv", "sync", "--frozen"], cwd=cwd, env=env, check=True)
        subprocess.run(["uv", "run", "--frozen", "pytest", "--confcutdir", str(ROOT)], cwd=cwd, env=env, check=True)
    subprocess.run(["python3", "-m", "unittest", "discover", "-s", "scripts/tests"], cwd=ROOT, env=env, check=True)
    # Links, indexes, Korean twins, AGENTS.md sizes, status lines, and docs/generated/ (gen_docs.py --check).
    subprocess.run(["python3", "scripts/check_docs.py"], cwd=ROOT, env=env, check=True)
    subprocess.run(["pnpm", "--filter", "nordly-web", "test"], cwd=ROOT, env=env, check=True)
    subprocess.run(["pnpm", "--filter", "nordly-web", "build"], cwd=ROOT, env=env, check=True)


if __name__ == "__main__":
    main()
