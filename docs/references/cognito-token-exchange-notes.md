# Cognito token exchange: verified behavior

What ARIA relies on about Amazon Cognito's RFC 8693 token exchange, which mints the agent's Gateway token. "Verified" means observed on a deployed stage; "documented" means stated in the [Cognito guide](https://docs.aws.amazon.com/cognito/latest/developerguide/cognito-user-pools-token-exchange.html).

## Prerequisites

- **Documented:** the user pool needs the Essentials or Plus feature plan, and token exchange enabled for the account. Without it, the call fails with `OperationNotEnabledException`.
- **Documented:** the client performing the exchange needs `ALLOW_TOKEN_EXCHANGE_DELEGATION` for delegation (with an actor token), or `ALLOW_TOKEN_EXCHANGE_IMPERSONATION` without one.
- **Documented:** every `audience` or `resource` value must be a resource server registered in the pool.
- **Verified:** the token endpoint refuses token exchange on a classic hosted-UI domain (`token_exchange_requires_managed_login`). The domain needs managed login, `ManagedLoginVersion: 2`.

## The request

The token endpoint takes `POST /oauth2/token` with the exchanging client's credentials (`client_secret_basic` or `client_secret_post`):

| Parameter | Value ARIA sends |
| --- | --- |
| `grant_type` | `urn:ietf:params:oauth:grant-type:token-exchange` |
| `subject_token` | The shopper's access token |
| `subject_token_type` | `urn:ietf:params:oauth:token-type:access_token` |
| `actor_token` | A client-credentials token for the agent's client |
| `actor_token_type` | `urn:ietf:params:oauth:token-type:access_token` |
| `audience` | `aria-tools` |
| `scope` | `aria-tools/invoke` |

- **Verified:** `subject_token_type=urn:ietf:params:oauth:token-type:jwt` fails with HTTP 400, `invalid_request`, "Unsupported token type". AgentCore Identity sends that type unless `GetResourceOauth2Token` passes `customParameters={"subject_token_type": "urn:ietf:params:oauth:token-type:access_token"}`. ARIA's agent passes it (`packages/agent/src/core/downstream.py`).

## The issued token

Verified:

- `sub` is the shopper, `act.sub` is the agent's client ID, `aud` is `aria-tools`, `token_use` is `access`.
- `scope` is the intersection of the requested scopes, the subject token's scopes, and the client's allowed scopes. The exchange can only narrow, so the web client must request `aria-tools/invoke` for it to survive.
- It never outlives the subject token: a token exchanged right after sign-in lasted about 3,560 s.
- No refresh token is issued.

## AgentCore Identity's part

- **Verified:** with `actorTokenContent: M2M`, AgentCore first makes a client-credentials request with the provider's `actorTokenScopes`, then the exchange.
- **Verified:** AgentCore returns only "Token exchange failed with HTTP status 400". CloudTrail records each token-endpoint call as `Token_POST` from event source `cognito-idp.amazonaws.com`, with the request parameters (tokens hidden) and the status. That shows the exact request AgentCore sent.
- **Verified:** `GetWorkloadAccessTokenForJWT` refuses outside callers for a Runtime-linked workload identity ("WorkloadIdentity is linked to a service and cannot retrieve an access token by the caller"). To test the exchange outside the Runtime, call Cognito's token endpoint directly with the agent client's credentials.
