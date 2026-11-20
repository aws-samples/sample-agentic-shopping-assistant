"""AgentCore Memory history and deletion. Backend failures are never empty success."""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone

import boto3
from botocore.config import Config
from config import get_config
from aria_contracts.identity import require_session_owner

# Bounded waits: a slow Memory call holds a worker thread and the shopper's request. Standard
# retry mode backs off on throttling and transient errors, three attempts in all.
MEMORY_CLIENT_CONFIG = Config(connect_timeout=3, read_timeout=10, retries={"mode": "standard", "total_max_attempts": 3})
# Concurrent ListEvents calls when the session list checks which sessions still hold events.
SESSION_CHECK_WORKERS = 8


def _utc(value):
    """A Memory timestamp as an aware UTC datetime, or None."""
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value.astimezone(timezone.utc)


class SessionStore:
    def __init__(self, client=None):
        config = get_config()
        self._memory_id = config.memory_id
        self._client = client or (
            boto3.client("bedrock-agentcore", region_name=config.region, config=MEMORY_CLIENT_CONFIG) if self.enabled else None
        )

    @property
    def enabled(self):
        return bool(self._memory_id)

    def _pages(self, operation, result_key, **kwargs):
        if not self.enabled:
            return []
        result, token = [], None
        seen = set()
        while True:
            response = getattr(self._client, operation)(memoryId=self._memory_id, **kwargs, **({"nextToken": token} if token else {}))
            result.extend(response.get(result_key, []))
            token = response.get("nextToken")
            if not token:
                return result
            if token in seen:
                raise RuntimeError("Memory pagination cursor repeated")
            seen.add(token)

    def list_session_summaries(self, actor_id, max_results=50, *, with_events_only=True):
        """The caller's sessions, newest first: `sessionId`, and `createdAt` (ISO 8601, UTC) when Memory reports it.

        Memory keeps listing a session after its events are deleted, so by default a session with
        no event left is skipped: a deleted conversation never comes back as an empty one.
        """
        sessions = self._pages("list_sessions", "sessionSummaries", actorId=actor_id, maxResults=max_results)
        owned = []
        for session in sessions:
            sid = session.get("sessionId", "")
            try:
                require_session_owner(sid, actor_id)
            except ValueError:
                continue  # Not bound to this caller, so it could not be opened: never listed.
            owned.append((_utc(session.get("createdAt")), sid))
        if with_events_only:
            owned = self._with_events(actor_id, owned)
        # Newest first; sessions without a creation time last.
        owned.sort(key=lambda item: (item[0] is not None, item[0] or datetime.min.replace(tzinfo=timezone.utc)), reverse=True)
        return [
            {"sessionId": sid, **({"createdAt": created.isoformat(timespec="seconds").replace("+00:00", "Z")} if created else {})}
            for created, sid in owned
        ]

    def list_sessions(self, actor_id, max_results=50):
        """Every session Memory lists for the caller, emptied ones included, for deleting them all."""
        return [summary["sessionId"] for summary in self.list_session_summaries(actor_id, max_results, with_events_only=False)]

    def _with_events(self, actor_id, owned):
        def has_events(item):
            response = self._client.list_events(memoryId=self._memory_id, actorId=actor_id, sessionId=item[1],
                                                includePayloads=False, maxResults=1)
            return bool(response.get("events"))

        if not owned:
            return owned
        with ThreadPoolExecutor(max_workers=min(SESSION_CHECK_WORKERS, len(owned))) as pool:
            kept = list(pool.map(has_events, owned))
        return [item for item, keep in zip(owned, kept) if keep]

    def _events(self, actor_id, session_id):
        require_session_owner(session_id, actor_id)
        return self._pages("list_events", "events", actorId=actor_id, sessionId=session_id, includePayloads=True, maxResults=100)

    def list_messages(self, actor_id, session_id, max_results=100):
        events = sorted(self._events(actor_id, session_id), key=lambda e: (str(e.get("eventTimestamp", "")), e.get("eventId", "")))
        messages = []
        for event in events:
            for payload in event.get("payload", []):
                conv = payload.get("conversational")
                if conv:
                    raw = conv.get("content", {}).get("text", "")
                    try:
                        saved = json.loads(raw)
                    except (ValueError, TypeError):
                        saved = {"role": conv.get("role", "").lower(), "content": [{"text": raw}]}
                elif "blob" in payload:
                    try:
                        saved = json.loads(payload["blob"])
                        if isinstance(saved, list):
                            saved = json.loads(saved[0])
                    except (ValueError, TypeError, IndexError):
                        continue
                else:
                    continue
                if not isinstance(saved, dict):
                    continue
                message = saved.get("message", saved)
                if not isinstance(message, dict) or message.get("role", "").lower() not in ("user", "assistant"):
                    continue
                content = message.get("content", [])
                text = content if isinstance(content, str) else "\n".join(block["text"] for block in content if isinstance(block, dict) and isinstance(block.get("text"), str))
                if text:
                    messages.append({"role": message["role"].lower(), "content": text})
        return messages

    def _delete_namespace(self, namespace):
        records = self._pages("list_memory_records", "memoryRecordSummaries", namespace=namespace, maxResults=100)
        for record in records:
            self._client.delete_memory_record(memoryId=self._memory_id, memoryRecordId=record["memoryRecordId"])
        return len(records)

    def delete_session(self, actor_id, session_id):
        # Read every page before mutation so deletion cannot shift pagination.
        events = self._events(actor_id, session_id)
        for event in events:
            self._client.delete_event(memoryId=self._memory_id, actorId=actor_id, sessionId=session_id, eventId=event["eventId"])
        self._delete_namespace(f"/summaries/{actor_id}/{session_id}")
        return len(events)

    def forget_preferences(self, actor_id):
        return sum(self._delete_namespace(f"/users/{actor_id}/{kind}") for kind in ("preferences", "facts"))
