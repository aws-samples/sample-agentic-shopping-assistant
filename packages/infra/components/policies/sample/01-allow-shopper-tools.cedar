// An authenticated caller may use a shopper tool with a bounded result size.
//
// Cedar is default-deny, so WITHOUT this policy every tool call is refused
// once the policy engine runs in ENFORCE mode. Keep a permit for each tool
// you expose, or the agent silently loses its tools.
//
// Placeholders substituted at synth time:
//   {{GATEWAY_ARN}}  the gateway this engine is attached to
//   {{TOOL}}         "<target-name>___<tool-name>", e.g. saved-items___get_saved_items
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"{{TOOL}}",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when {
  context has input &&
  (
    !(context.input has limit) ||
    (context.input has limit && context.input.limit >= 1 && context.input.limit <= 20)
  )
};
