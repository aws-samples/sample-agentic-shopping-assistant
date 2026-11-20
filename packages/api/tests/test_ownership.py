import pytest
from aria_contracts.identity import actor_id, new_session_id, require_session_owner


def test_ownership_is_stateless_and_issuer_scoped():
    alice = actor_id({"iss": "https://one.example", "sub": "alice"})
    bob = actor_id({"iss": "https://one.example", "sub": "bob"})
    elsewhere = actor_id({"iss": "https://two.example", "sub": "alice"})
    session = new_session_id(alice)
    assert len(session) == 97
    require_session_owner(session, alice)
    for wrong in (bob, elsewhere):
        with pytest.raises(ValueError):
            require_session_owner(session, wrong)
    with pytest.raises(ValueError):
        require_session_owner("s" + "a" * 32, alice)
