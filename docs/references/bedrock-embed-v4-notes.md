# Cohere Embed v4 on Bedrock: verified and documented behavior

What ARIA relies on about Cohere Embed v4 on Amazon Bedrock, which embeds the catalog for the indexer and each query for the search tool. "Verified" means observed on a deployed stage; "Documented" means stated in the [Embed v4 request and response reference](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html), the [Embed v4 model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-v4.html) or the Bedrock pages linked below.

## The model

- **Documented:** the model ID is `cohere.embed-v4:0`, called through `InvokeModel` on the `bedrock-runtime` endpoint. Converse and response streaming aren't supported ([model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-v4.html)).
- **Documented:** it embeds text, images or interleaved text and images, with a context window of about 128,000 tokens per input ([request and response](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html)).
- **Documented:** Bedrock Guardrails aren't supported for the model ([model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-v4.html)).
- **Documented:** only the Standard service tier is offered; Priority, Flex and Reserved aren't ([model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-v4.html)).

- **Verified:** from us-west-2, `global.cohere.embed-v4:0` answers `search_document` batches of 96 for the indexing task and `search_query` calls for the search tool, at 1024 float dimensions, from inside the VPC through the NAT gateway. A search with a query took 0.25 to 0.5 s in the tool, the embedding included.
- **Verified:** from ap-northeast-2 too, for the indexing task and the search tool, through the `global` profile, the only one offered there. The first search of a session took about 2 s, the embedding and the first connection to the domain included; later ones took 0.3 to 0.5 s.
- **Verified:** the vector half finds products by meaning: "downpour puddles stomp", with no word in the catalog, ranked the rain boots first, then an umbrella and a rain jacket. A single short Korean word embeds less well: "장화" ranked them ninth.

ARIA embeds text only. `packages/catalog/src/aria_catalog/embedding.py` makes every call, and `definition.py` fixes the model family (`cohere.embed-v4:0`, optionally with a profile prefix) and `DIMENSION`.

## The request

Documented ([request and response](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html)), with content type `application/json`:

| Field | What the docs say | What ARIA sends |
| --- | --- | --- |
| `input_type` | Required. `search_document`, `search_query`, `classification` or `clustering`. For search, embed the corpus with `search_document` and queries with `search_query` | `search_document` from the indexer, `search_query` from the search tool |
| `texts` | Strings to embed, at most 96 per call. Not sent together with `images` | Up to 96 embedding texts per call (`embedding.BATCH`) |
| `embedding_types` | One or more of `float`, `int8`, `uint8`, `binary`, `ubinary`. Floats when omitted | `["float"]` |
| `output_dimension` | `256`, `512`, `1024` or `1536`. `1536` when omitted | `1024` |
| `max_tokens` | Truncation budget per input, up to about 128,000 | Not sent |
| `truncate` | `LEFT`, `RIGHT` or `NONE`; `NONE` returns an error for an over-length input | Not sent |

- **Documented:** a request carries at most about 20 MB.

## The response

- **Documented:** with one embedding type, `embeddings` is a list of vectors and `response_type` is `embeddings_floats`. With several types, `embeddings` is an object keyed by type (`{"float": [[...]], "int8": [[...]]}`) and `response_type` is `embeddings_by_type` ([request and response](https://docs.aws.amazon.com/bedrock/latest/userguide/model-parameters-embed-v4.html)).
- **Documented:** there is one vector per entry of `texts`, in order, and each vector's length is `output_dimension`.

`embedding._vectors` accepts both shapes: a list, or an object whose `float` key holds the list. It fails the call unless there is one vector per text, each of 1024 numbers.

## Inference profiles

Documented on the [model card](https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-cohere-embed-v4.html):

- **Geographic profiles:** `us.cohere.embed-v4:0` and `eu.cohere.embed-v4:0`. No APAC profile is listed.
- **Global profile:** `global.cohere.embed-v4:0`, routed to commercial Regions in the Americas, EMEA and Asia Pacific.
- **In-Region:** the foundation model is offered directly only in `us-east-1`, `eu-west-1` and `ap-northeast-1`.
- **ap-northeast-2 (Seoul):** neither in-Region nor through a geographic profile. Only the `global` profile serves it.

ARIA's default model is `global.cohere.embed-v4:0` (`definition.DEFAULT_MODEL`), set by `[catalog.embedding].model`.

## Data residency

- **Documented:** a global profile routes a request to any supported commercial Region worldwide, so it may be processed outside the source Region. Organizations with residency requirements should assess whether it fits; a geographic profile keeps requests within its geography ([global cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html), [choosing a profile](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)).
- **Documented:** cross-Region traffic stays on the AWS network and is encrypted in transit. CloudTrail logs each request in the source Region, with `additionalEventData.inferenceRegion` naming where it ran ([cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/cross-region-inference.html)).

With the `global` profile, product text from the catalog and every shopper's search text can be embedded outside the stage's Region. In Seoul there is no in-Region alternative for Embed v4.

## Permissions

- **Documented:** a global profile needs `bedrock:InvokeModel` on three resources: the profile in the source Region (`arn:aws:bedrock:<region>:<account>:inference-profile/global.<model>`), the foundation model in the source Region (`arn:aws:bedrock:<region>::foundation-model/<model>`) and the Region-less foundation model (`arn:aws:bedrock:::foundation-model/<model>`) ([global cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html#global-cris-iam-setup)).
- **Documented:** for the Region-less resource, `aws:RequestedRegion` is `unspecified`. An SCP that denies Regions outside an allowlist must allow `unspecified`, or exempt the profile with `bedrock:InferenceProfileArn`, or global requests are denied.
- **Documented:** an organization can block global profiles outright, with an SCP that denies `bedrock:*` where `aws:RequestedRegion` is `unspecified` and `bedrock:InferenceProfileArn` is like `global.*` ([global cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)).
- **Documented:** global-profile quotas, "Global Cross-region model inference requests per minute" and "tokens per minute", are viewed and raised in Service Quotas in the source Region ([global cross-Region inference](https://docs.aws.amazon.com/bedrock/latest/userguide/global-cross-region-inference.html)). The indexer's throttling backoff waits on them.

- **Verified:** the profile ARN with `arn:aws:bedrock:*::foundation-model/cohere.embed-v4:0` is enough for the global profile, so the Region wildcard covers the Region-less ARN. A deny on the model and the profile makes `InvokeModel` fail with `AccessDeniedException`.

`packages/infra/components/model_permissions.py` grants the profile ARN and `arn:aws:bedrock:*::foundation-model/<model>`, a Region wildcard meant to cover both foundation-model ARNs. The indexing task's role gets that grant, and so does the Runtime role with the `opensearch` provider. Preflight doesn't read SCPs. The grant has no Region or profile conditions, so it also allows invoking the foundation model directly where a Region offers it in-Region; the documented example policy narrows each statement with `aws:RequestedRegion` and `bedrock:InferenceProfileArn`.

## Checking access

- **Documented:** `GetInferenceProfile` returns `status`, whose only value is `ACTIVE`, ready for use ([API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetInferenceProfile.html)).
- **Documented:** `GetFoundationModelAvailability` returns `authorizationStatus` (`AUTHORIZED` or `NOT_AUTHORIZED`) and `regionAvailability` (`AVAILABLE` or `NOT_AVAILABLE`) ([API reference](https://docs.aws.amazon.com/bedrock/latest/APIReference/API_GetFoundationModelAvailability.html)).

`scripts/preflight.py` `check_model` calls both for the embedding model, read-only, and invokes nothing.
