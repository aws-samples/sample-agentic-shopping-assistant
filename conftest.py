"""Tests never call AWS: fail any botocore request that escapes its test double.

pytest stops looking for conftest.py at the directory of the pyproject.toml that configures it, so
the repository root and each Python package's test root hold identical copies of this file.
scripts/tests/test_aws_guard.py fails when a package with tests has no copy or a copy differs:
change the root conftest.py, then copy it to every package.
"""
import pytest


@pytest.fixture(autouse=True)
def block_aws_network(monkeypatch):
    """Fail the test if botocore tries to send a request."""
    try:
        import botocore.httpsession
    except ImportError:  # a package without botocore, such as the interceptor, has nothing to guard
        return

    def denied(*args, **kwargs):
        pytest.fail("An AWS network call escaped its test double")

    monkeypatch.setattr(botocore.httpsession.URLLib3Session, "send", denied)
