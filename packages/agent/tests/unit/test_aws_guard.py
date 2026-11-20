"""The AWS guard in tests/conftest.py applies to every test in the package, and refuses botocore requests."""

import boto3
import botocore.httpsession
import pytest

# Read when the module is collected, before the guard patches it for each test.
UNGUARDED_SEND = botocore.httpsession.URLLib3Session.send


def test_a_botocore_request_fails_its_test_and_is_never_sent():
    # The test doesn't request the guard, so it must be on anyway; if it isn't, stop before the call can go out.
    assert botocore.httpsession.URLLib3Session.send is not UNGUARDED_SEND
    session = boto3.session.Session(  # nosec B106 # fake credentials: the test proves no request is sent
        aws_access_key_id="testing", aws_secret_access_key="testing", region_name="us-east-1"  # nosec B106 # fake credentials: the test proves no request is sent
    )
    with pytest.raises(pytest.fail.Exception, match="An AWS network call escaped its test double"):
        session.client("sts").get_caller_identity()
