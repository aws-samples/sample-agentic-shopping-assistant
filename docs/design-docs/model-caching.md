# Model caching

Status: verified

This doc describes the checked-in implementation in `packages/agent/src/core/config.py`, `core/builder.py`, and `packages/infra/stacks/agent_runtime_stack.py`.

## Current configuration

The agent always uses Strands `BedrockModel` and Bedrock Converse. `AgentConfig.from_env()` defaults `MODEL_CACHE_ENABLED` to `false`. If enabled, it requires `anthropic.claude` in `MODEL_ID`; every other model family fails configuration validation. The default model is `global.openai.gpt-5.6-luna`.

CDK explicitly injects `MODEL_CACHE_ENABLED=false`. There is no TOML caching switch. Setting an environment variable in the shell running CDK does not override that emitted Runtime setting; enabling deployed caching requires a reviewed infrastructure change and a compatible model.

## Claude cache settings

When enabled, `SessionBuilder._build_model()` supplies all three options:

```python
options["cache_prompt"] = "default"
options["cache_tools"] = "default"
options["cache_config"] = CacheConfig(strategy="auto")
```

These configure system-prompt, tool-schema and conversation cache checkpoints. Cache eligibility and actual savings depend on the selected model and request prefix; unit tests validate emitted options, not a billed cache hit. For an isolated agent process with a compatible `MODEL_ID`, set `MODEL_CACHE_ENABLED=true` before startup.

## OpenAI behavior

For OpenAI, keep caching disabled. The agent has no Responses/Mantle provider and calls no model through an OpenAI SDK: every model, OpenAI's included, runs on the Converse path with the configured Bedrock guardrail. `_build_model()` also omits `temperature` when the model ID contains `openai.gpt-5`; other model families receive the configured temperature.

Do not remove the cache validation just to send checkpoints for another model. A future provider change must preserve content filtering, streaming, tool behavior and telemetry, and be verified against the selected model/account before deployment. No alternative caching path is implemented here.

## Verification

`packages/agent/tests/unit/test_config.py` checks cache gating; `test_builder.py` checks model construction, cache options and GPT-5 temperature omission. Run from the repository root:

```sh
(cd packages/agent && uv run --frozen pytest)
```

Live cache-token measurements require a separately authorized model invocation. Local test success is not evidence of account access, cache hits or cost savings.
