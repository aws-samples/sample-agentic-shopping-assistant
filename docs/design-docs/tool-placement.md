# Where a tool belongs

Status: verified

ARIA's agent has two kinds of tools:

- **Local tools** are Strands tools that run inside the agent: `@tool` functions, or an `AgentTool` such as `search_products`, whose schema the catalog definition generates.
- **Gateway tools** sit behind AgentCore Gateway and are called over MCP. Your own APIs, MCP servers and Lambda functions can be onboarded there.

This doc decides where a tool belongs and describes the Gateway sample. [Add a local tool](../guides/add-a-local-tool.md), [Gateway onboarding](../guides/gateway-onboarding.md) and [identity and tokens](identity-and-tokens.md) cover the rest.

## Where a tool belongs

| Put it in the agent (local) | Put it behind the Gateway |
| --- | --- |
| Called on most turns, so latency matters | Called occasionally |
| Needs no per-user authorization | Needs the shopper's identity, or its own credentials |
| Code you ship and test with the agent | An API or service another team owns, onboarded through config |

Product search is local. It runs on almost every turn. Behind the Gateway, each search would cost a Gateway tool call, interceptor and target included: 0.15–0.6 s on Dev, against under 10 ms in the agent. When a session has no Gateway tools, the agent makes no MCP connection at all.

`get_saved_items`, the sample Gateway tool, needs to know who the shopper is, so it sits behind the Gateway.

## Add a Gateway tool

Onboard it as a Gateway target. [Gateway onboarding](../guides/gateway-onboarding.md) covers all four target kinds (OpenAPI, API Gateway, Lambda and MCP server), with their credentials and Cedar policies. To build a Lambda target in this repository, follow `packages/mcp-servers/saved-items-lambda` and [the Lambda target contract](../guides/gateway-onboarding.md#lambda-target-contract).

## The saved-items sample

`gateway.savedItemsSample = true` deploys `get_saved_items`. It is on in the Dev template, and Prod rejects it. The sample:

- is a Lambda that refuses a call without a valid `x-aria-actor-id`;
- returns a stable `product.v1` sample per shopper from the example catalog;
- has its own Cedar policies in `packages/infra/components/policies/sample/`.

Ask Aria "내가 저장한 상품 보여줘" to see the whole path: the minted token, the actor header, the interceptor, the target, and the product cards.
