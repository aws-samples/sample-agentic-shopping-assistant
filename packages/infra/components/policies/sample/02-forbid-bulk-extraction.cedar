// Anti-scraping guard: cap how many results one tool call can request.
//
// A shopping assistant needs a handful of results per call. A request for
// hundreds is data extraction, not shopping — whether from a prompt-injected
// agent or a misbehaving client.
//
// forbid overrides permit in Cedar, so this wins over the baseline grant
// regardless of policy ordering.
//
// `context.input.<name>` reads the tool's arguments. The Lambda also clamps
// limit to MAX_LIMIT; this enforces the same intent at the Gateway boundary,
// before the tool is ever invoked.
forbid(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"{{TOOL}}",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  context has input &&
  context.input has limit &&
  context.input.limit > 20
};
