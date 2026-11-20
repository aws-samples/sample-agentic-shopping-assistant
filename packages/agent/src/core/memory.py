"""AgentCore Memory namespaces.

The namespace strings here MUST match the `namespaces` declared on each
CfnMemory strategy in packages/infra. `{actorId}` and `{sessionId}` are
resolved by AgentCoreMemorySessionManager via str.format at retrieval time.
"""

# Cross-session, per-user preferences.
PREFERENCES_NAMESPACE = "/users/{actorId}/preferences"

# Cross-session, per-user extracted facts (sizes, brands, past purchases).
FACTS_NAMESPACE = "/users/{actorId}/facts"

# Per-session recap, used to rehydrate a returning conversation.
SUMMARIES_NAMESPACE = "/summaries/{actorId}/{sessionId}"
