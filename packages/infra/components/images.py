"""The build context each container image takes from packages/."""
from pathlib import Path

PACKAGES = Path(__file__).resolve().parents[2]

# The packages each image's Dockerfile copies; tests/test_images.py checks them against the Dockerfiles.
IMAGE_PACKAGES = {
    "agent": ("agent", "catalog", "contracts"),
    "api": ("api", "contracts"),
    "indexer": ("indexer", "catalog"),
}


def build_excludes(image: str) -> list[str]:
    """Leave every other package out of the image's context, so a change to one never rebuilds and
    redeploys an image that doesn't contain it."""
    needed = IMAGE_PACKAGES[image]
    others = sorted(p.name for p in PACKAGES.iterdir() if p.is_dir() and not p.name.startswith(".") and p.name not in needed)
    return ["**/.venv", "**/node_modules", "**/__pycache__", *others]
