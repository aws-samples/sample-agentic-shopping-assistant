# Design docs

How ARIA is built and why. Each doc opens with its status and how it was verified. Proposals start as `proposed`, become `accepted` once adopted, and `verified` once tests or a live run prove them.

| Doc | Decides | Status |
| --- | --- | --- |
| [Core beliefs](core-beliefs.md) | The principles every change is held to | accepted |
| [Where a tool belongs](tool-placement.md) | Local tools versus Gateway tools | verified |
| [Identity and tokens](identity-and-tokens.md) | How a tool learns who the shopper is without seeing the shopper's token; the agent's own Gateway token; custom headers | verified |
| [The Gateway interceptor](gateway-interceptor.md) | Actor checks and which headers reach a target | verified |
| [Product search and the tool packages](product-search.md) | The search flow, the providers, the HTTPS adapter, how to extend the tools | verified |
| [Catalog search](catalog-search.md) | Bring-your-own catalogs: the catalog definition, the generated tool schema, the OpenSearch index, hybrid search with Embed v4, the indexing job | verified |
| [Tool schemas](tool-schemas.md) | Rules for the schemas the model sees | verified |
| [Response layout](response-layout.md) | How answers, citations and cards are laid out | verified |
| [Streaming and latency](streaming-and-latency.md) | The SSE path and where the time goes | verified |
| [Memory](memory.md) | What AgentCore Memory stores and whose it is | verified |
| [Model caching](model-caching.md) | When prompt caching is allowed | verified |
| [Deployment safety](deployment-safety.md) | Reviewed plans, staged deploys, live checks | verified |
