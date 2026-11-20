import json
from types import SimpleNamespace
import pytest
from aria_contracts.identity import actor_id, new_session_id
import sessions

OWNER = actor_id({"iss": "https://issuer.example", "sub": "alice"})
SID = new_session_id(OWNER)


def event(number):
    return {"eventId": str(number), "eventTimestamp": number, "payload": [{"conversational": {"role": "USER", "content": {"text": json.dumps({"message_id": number, "message": {"role": "user", "content": [{"text": f"message {number}"}]}})}}}]}


def test_all_pages_are_decoded_and_sorted(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="m", region="ap-northeast-2"))
    def list_events(**kw):
        return {"events": [event(1)]} if kw.get("nextToken") else {"events": [event(2)], "nextToken": "page2"}
    store = sessions.SessionStore(SimpleNamespace(list_events=list_events))
    assert [m["content"] for m in store.list_messages(OWNER, SID)] == ["message 1", "message 2"]


def test_delete_failure_propagates(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="m", region="ap-northeast-2"))
    def fail(**kw): raise RuntimeError("denied")
    store = sessions.SessionStore(SimpleNamespace(list_events=lambda **kw: {"events": [event(1)]}, delete_event=fail))
    with pytest.raises(RuntimeError, match="denied"):
        store.delete_session(OWNER, SID)


def test_without_a_memory_id_no_aws_client_is_created(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="", region="ap-northeast-2"))
    monkeypatch.setattr(sessions.boto3, "client", lambda *a, **kw: pytest.fail("AWS client created"))
    store = sessions.SessionStore()
    assert store.list_sessions(OWNER) == []
    assert store.list_messages(OWNER, SID) == []


def test_the_memory_client_has_bounded_timeouts_and_standard_retries(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="m", region="ap-northeast-2"))
    created = {}
    monkeypatch.setattr(sessions.boto3, "client", lambda service, **kw: created.update(service=service, **kw) or object())
    sessions.SessionStore()
    config = created["config"]
    assert (created["service"], created["region_name"]) == ("bedrock-agentcore", "ap-northeast-2")
    assert (config.connect_timeout, config.read_timeout) == (3, 10)
    assert config.retries == {"mode": "standard", "total_max_attempts": 3}


def test_sessions_are_listed_newest_first_with_their_creation_time(monkeypatch):
    from datetime import datetime, timedelta, timezone

    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="m", region="ap-northeast-2"))
    older, newer, undated = new_session_id(OWNER), new_session_id(OWNER), new_session_id(OWNER)
    seoul = timezone(timedelta(hours=9))
    pages = {
        None: {"sessionSummaries": [
            {"sessionId": older, "actorId": OWNER, "createdAt": datetime(2026, 11, 18, 9, 0, 0, 500000, tzinfo=timezone.utc)},
            {"sessionId": "unbound-session-id-0000000000000000", "actorId": OWNER, "createdAt": datetime(2026, 11, 19, tzinfo=timezone.utc)},
        ], "nextToken": "page2"},
        "page2": {"sessionSummaries": [
            {"sessionId": undated, "actorId": OWNER},
            # 18:30 in Seoul is 09:30 UTC: newer than `older`.
            {"sessionId": newer, "actorId": OWNER, "createdAt": datetime(2026, 11, 18, 18, 30, tzinfo=seoul)},
        ]},
    }
    store = sessions.SessionStore(SimpleNamespace(list_sessions=lambda **kw: pages[kw.get("nextToken")],
                                                  list_events=lambda **kw: {"events": [event(1)]}))
    assert store.list_session_summaries(OWNER) == [
        {"sessionId": newer, "createdAt": "2026-11-18T09:30:00Z"},
        {"sessionId": older, "createdAt": "2026-11-18T09:00:00Z"},
        {"sessionId": undated},
    ]
    assert store.list_sessions(OWNER) == [newer, older, undated]


def test_a_session_whose_events_are_deleted_is_no_longer_listed(monkeypatch):
    # Memory keeps listing a session after its events are deleted; the list skips it, and
    # deleting everything still reaches it.
    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="m", region="ap-northeast-2"))
    kept, emptied = new_session_id(OWNER), new_session_id(OWNER)
    checked = []

    def list_events(**kw):
        checked.append(kw)
        return {"events": [event(1)]} if kw["sessionId"] == kept else {"events": []}

    listed = {"sessionSummaries": [{"sessionId": kept, "actorId": OWNER}, {"sessionId": emptied, "actorId": OWNER}]}
    store = sessions.SessionStore(SimpleNamespace(list_sessions=lambda **kw: listed, list_events=list_events))
    assert store.list_session_summaries(OWNER) == [{"sessionId": kept}]
    assert all(kw["maxResults"] == 1 and kw["includePayloads"] is False and kw["actorId"] == OWNER for kw in checked)
    assert sorted(store.list_sessions(OWNER)) == sorted([kept, emptied])


def test_a_failed_event_check_fails_the_list(monkeypatch):
    monkeypatch.setattr(sessions, "get_config", lambda: SimpleNamespace(memory_id="m", region="ap-northeast-2"))
    def fail(**kw): raise RuntimeError("throttled")
    listed = {"sessionSummaries": [{"sessionId": SID, "actorId": OWNER}]}
    store = sessions.SessionStore(SimpleNamespace(list_sessions=lambda **kw: listed, list_events=fail))
    with pytest.raises(RuntimeError, match="throttled"):
        store.list_session_summaries(OWNER)
