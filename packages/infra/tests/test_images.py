"""Each image's build context holds exactly the packages its Dockerfile copies."""
import re

import pytest

from components.images import IMAGE_PACKAGES, PACKAGES, build_excludes


def copied_packages(image: str) -> set[str]:
    """The packages/ directories the Dockerfile copies from the build context (not from a build stage)."""
    lines = (PACKAGES / image / "Dockerfile").read_text().splitlines()
    return {re.split(r"\s+", line)[1].split("/")[0] for line in lines if line.startswith("COPY ") and "--from" not in line}


@pytest.mark.parametrize("image", sorted(IMAGE_PACKAGES))
def test_the_context_holds_what_the_dockerfile_copies(image):
    assert copied_packages(image) == set(IMAGE_PACKAGES[image])


@pytest.mark.parametrize("image", sorted(IMAGE_PACKAGES))
def test_every_other_package_is_left_out(image):
    # A change to a package an image doesn't contain must not rebuild and redeploy it.
    packages = {p.name for p in PACKAGES.iterdir() if p.is_dir() and not p.name.startswith(".")}
    excluded = set(build_excludes(image))
    assert packages - set(IMAGE_PACKAGES[image]) <= excluded
    assert not excluded & set(IMAGE_PACKAGES[image])
    assert {"indexer", "infra", "web", "mcp-servers"} - set(IMAGE_PACKAGES[image]) <= excluded
