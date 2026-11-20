#!/usr/bin/env python3
"""Build and export local release artifacts. Never pushes images or changes AWS."""
import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
ALLOWED_ENGINES = ("docker", "finch", "podman")


def container_engine(environ=os.environ) -> str:
    """The container CLI to use: docker (default), finch or podman.

    Read from CONTAINER_ENGINE or CDK_DOCKER; a path such as /opt/homebrew/bin/finch
    selects that engine by name. Anything else is rejected, so the environment
    chooses between three known programs but can never name the executable itself.
    """
    requested = environ.get("CONTAINER_ENGINE") or environ.get("CDK_DOCKER") or "docker"
    engine = Path(requested).name
    if engine not in ALLOWED_ENGINES:
        raise SystemExit(f"Unsupported container engine {requested!r}; use one of {list(ALLOWED_ENGINES)}")
    if not shutil.which(engine):
        raise SystemExit(f"Container engine {engine!r} was not found on PATH")
    return engine


def run_engine(engine: str, args: list[str], **kwargs) -> subprocess.CompletedProcess:
    """Run an allowlisted engine. The program is always a literal; args never go through a shell."""
    if engine == "docker":
        return subprocess.run(["docker", *args], check=True, **kwargs)
    if engine == "finch":
        return subprocess.run(["finch", *args], check=True, **kwargs)
    if engine == "podman":
        return subprocess.run(["podman", *args], check=True, **kwargs)
    raise SystemExit(f"Unsupported container engine {engine!r}")


def main():
    engine = container_engine()
    revision = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip()
    output = ROOT / "artifacts"
    output.mkdir(exist_ok=True)
    dirty = bool(subprocess.check_output(["git", "status", "--porcelain"], cwd=ROOT, text=True).strip())
    manifest = {"commit": revision, "dirty": dirty, "configSchemaVersion": 1, "images": {}, "inputs": {}}
    for package in ["agent", "api", "indexer"]:
        tag = f"aria-{package}:{revision}"
        run_engine(engine, ["build", "--platform", "linux/arm64", "-f", f"packages/{package}/Dockerfile", "-t", tag, "packages"], cwd=ROOT)
        run_engine(engine, ["save", "-o", str(output / f"{package}.tar"), tag])
        inspected = json.loads(run_engine(engine, ["image", "inspect", tag], capture_output=True, text=True).stdout)[0]
        manifest["images"][package] = {"tag": tag, "imageId": inspected["Id"], "platform": "linux/arm64"}
    for path in sorted((ROOT / "packages").rglob("uv.lock")):
        if ".venv" not in path.parts:
            manifest["inputs"][str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
    for relative in ("build-images.json", "pnpm-lock.yaml", "packages/agent/Dockerfile", "packages/api/Dockerfile",
                     "packages/indexer/Dockerfile"):
        manifest["inputs"][relative] = hashlib.sha256((ROOT / relative).read_bytes()).hexdigest()
    (output / "release.json").write_text(json.dumps(manifest, indent=2) + "\n")


if __name__ == "__main__":
    main()
