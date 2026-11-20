# References

Third-party facts for people and agents working on ARIA. Notes record behavior observed on a deployed stage or stated in the vendor's guide; `*-llms.txt` files are upstream indexes, copied where the license allows.

| File | What | Source | License |
| --- | --- | --- | --- |
| [agentcore-gateway-notes.md](agentcore-gateway-notes.md) | AgentCore Gateway behavior ARIA relies on: authorizer, interceptor, headers, outbound credentials, target creation | Observed on a deployed stage, and the AgentCore developer guide | — |
| [agentcore-runtime-notes.md](agentcore-runtime-notes.md) | AgentCore Runtime behavior ARIA relies on: CloudFormation's environment limits, VPC mode and its network interfaces, where the agent's spans land | Observed on a deployed stage, and the AgentCore developer guide and API reference | — |
| [cognito-token-exchange-notes.md](cognito-token-exchange-notes.md) | Cognito RFC 8693 token exchange as AgentCore Identity uses it | Observed on a deployed stage, and the Cognito developer guide | — |
| [bedrock-embed-v4-notes.md](bedrock-embed-v4-notes.md) | Cohere Embed v4 on Bedrock: the request and response, the inference profiles, data residency and the IAM a global profile needs | Observed on a deployed stage, and the Amazon Bedrock user guide and API reference | — |
| [opensearch-hybrid-search-notes.md](opensearch-hybrid-search-notes.md) | OpenSearch 3.5 vector index, hybrid query, RRF pipeline, the Nori plugin and the HTTP IAM actions | Observed on a deployed stage, and the OpenSearch 3.5 and Amazon OpenSearch Service docs | — |
| [strands-agents.md](strands-agents.md) | Strands pages and behavior ARIA depends on | strandsagents.com | Links only |
| [nuxt-llms.txt](nuxt-llms.txt) | Nuxt documentation index | <https://nuxt.com/llms.txt>, fetched 2026-11-18 | MIT |
| [uv-llms.txt](uv-llms.txt) | uv documentation index | <https://docs.astral.sh/uv/llms.txt>, fetched 2026-11-18 | MIT or Apache-2.0 |

To refresh a copy, fetch it again and update the date here.
