"""The AWS guard loads for every Python test: each package with tests keeps an identical copy of the
repository-root conftest.py at every test root its pyproject.toml lists in testpaths.

pytest stops looking for conftest.py at the directory of the pyproject.toml that configures it, so
a package's tests never load the root file when pytest runs inside the package. To change the
guard, edit the root conftest.py and copy it to every package.
"""

from __future__ import annotations

import fnmatch
import os
import sys
import tempfile
import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "scripts"))
import check  # noqa: E402  (PACKAGES: the packages pnpm check tests)

FIXTURE = "block_aws_network"
# Never walked, besides hidden directories such as .venv: dependencies, caches and build output.
SKIP = {"node_modules", "cdk.out", "__pycache__", "build", "dist"}
PYTEST_FILES = ["test_*.py", "*_test.py"]  # pytest's default python_files


def python_packages(root: Path) -> dict[Path, list[Path]]:
    """Each directory under packages/ with a pyproject.toml, and its .py files (not a nested package's)."""
    owned: dict[Path, list[Path]] = {}
    for directory, subdirs, files in os.walk(root / "packages"):
        subdirs[:] = sorted(name for name in subdirs if name not in SKIP and not name.startswith("."))
        here = Path(directory)
        if "pyproject.toml" in files:
            owned[here] = []
        owner = next((path for path in (here, *here.parents) if path in owned), None)
        if owner is not None:
            owned[owner] += [here / name for name in sorted(files) if name.endswith(".py")]
    return owned


def pytest_option(package: Path, name: str, default: list[str]) -> list[str]:
    config = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
    value = config.get("tool", {}).get("pytest", {}).get("ini_options", {}).get(name, default)
    return value.split() if isinstance(value, str) else list(value)


def packages_with_tests(root: Path) -> dict[Path, tuple[list[Path], set[Path]]]:
    """Each Python package with test files: its .py files, and the ones that match python_files."""
    found = {}
    for package, sources in python_packages(root).items():
        patterns = pytest_option(package, "python_files", PYTEST_FILES)
        tests = {path for path in sources if any(fnmatch.fnmatch(path.name, pattern) for pattern in patterns)}
        if tests:
            found[package] = (sources, tests)
    return found


def guard_failures(root: Path) -> list[str]:
    guard = (root / "conftest.py").read_bytes()
    failures = []

    def fail(path: Path, problem: str) -> None:
        failures.append(f"{path.relative_to(root).as_posix()}: {problem}")

    for package, (sources, tests) in packages_with_tests(root).items():
        test_roots = [package / path for path in pytest_option(package, "testpaths", [])]
        copies = [test_root / "conftest.py" for test_root in test_roots]
        if not test_roots:
            fail(package, "has tests but no testpaths in [tool.pytest.ini_options]")
        for copy in copies:
            if not copy.is_file():
                fail(copy, "missing; copy the repository-root conftest.py here")
            elif copy.read_bytes() != guard:
                fail(copy, "differs from the repository-root conftest.py; copy it again")
        for path in sources:
            inside = any(path.is_relative_to(test_root) for test_root in test_roots)
            if path in tests and not inside:
                fail(path, "outside testpaths, so the AWS guard doesn't load for it")
            elif inside and path not in copies and f"def {FIXTURE}(" in path.read_text(encoding="utf-8"):
                fail(path, f"defines its own {FIXTURE}, which overrides the guard; remove it")
    return failures


class RepositoryTest(unittest.TestCase):
    def test_every_package_with_tests_has_an_identical_copy_at_each_test_root(self) -> None:
        self.assertEqual(guard_failures(ROOT), [])

    def test_the_packages_checked_are_the_ones_pnpm_check_tests(self) -> None:
        # Also keeps the check above from passing because the walk found nothing.
        checked = {package.relative_to(ROOT / "packages").as_posix() for package in packages_with_tests(ROOT)}
        self.assertEqual(checked, set(check.PACKAGES))


class GuardCheckTest(unittest.TestCase):
    """The check itself, against a scratch tree."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.write("conftest.py", "# the guard\n")
        self.write("packages/app/pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        self.write("packages/app/tests/conftest.py", "# the guard\n")
        self.write("packages/app/tests/unit/test_app.py", "def test_app():\n    pass\n")
        # A package without tests needs no copy.
        self.write("packages/tools/pyproject.toml", '[project]\nname = "tools"\n')
        self.write("packages/tools/index.py", "")

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def write(self, name: str, text: str) -> None:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")

    def test_a_tree_with_every_copy_passes(self) -> None:
        self.assertEqual(guard_failures(self.root), [])

    def test_a_missing_copy_fails(self) -> None:
        (self.root / "packages/app/tests/conftest.py").unlink()
        self.assertEqual(guard_failures(self.root),
                         ["packages/app/tests/conftest.py: missing; copy the repository-root conftest.py here"])

    def test_a_copy_that_differs_from_the_root_fails(self) -> None:
        self.write("packages/app/tests/conftest.py", "# the guard, edited in one package\n")
        self.assertEqual(guard_failures(self.root), [
            "packages/app/tests/conftest.py: differs from the repository-root conftest.py; copy it again",
        ])

    def test_a_package_that_gains_tests_needs_testpaths_and_a_copy(self) -> None:
        self.write("packages/tools/tests/test_index.py", "")
        self.assertEqual(guard_failures(self.root), [
            "packages/tools: has tests but no testpaths in [tool.pytest.ini_options]",
            "packages/tools/tests/test_index.py: outside testpaths, so the AWS guard doesn't load for it",
        ])
        self.write("packages/tools/pyproject.toml", '[tool.pytest.ini_options]\ntestpaths = ["tests"]\n')
        self.assertEqual(guard_failures(self.root),
                         ["packages/tools/tests/conftest.py: missing; copy the repository-root conftest.py here"])

    def test_a_test_outside_testpaths_fails(self) -> None:
        self.write("packages/app/scripts/test_tool.py", "")
        self.assertEqual(guard_failures(self.root),
                         ["packages/app/scripts/test_tool.py: outside testpaths, so the AWS guard doesn't load for it"])

    def test_a_test_module_with_its_own_guard_fails(self) -> None:
        self.write("packages/app/tests/unit/test_aws.py", f"def {FIXTURE}(monkeypatch):\n    pass\n")
        self.assertEqual(guard_failures(self.root),
                         [f"packages/app/tests/unit/test_aws.py: defines its own {FIXTURE}, which overrides the guard; "
                          "remove it"])

    def test_environments_and_build_output_are_not_packages(self) -> None:
        for decoy in ("packages/app/.venv/lib/dep", "packages/app/cdk.out/asset.1", "packages/web/node_modules/dep"):
            self.write(f"{decoy}/pyproject.toml", "")
            self.write(f"{decoy}/tests/test_dep.py", "")
        self.assertEqual(guard_failures(self.root), [])


if __name__ == "__main__":
    unittest.main()
