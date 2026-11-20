# Gateway에 API와 MCP 서버 등록

[English](gateway-onboarding.md)

API나 MCP 서버를 AgentCore Gateway 뒤에 두면 에이전트가 이를 도구로 호출할 수 있습니다. `[[gateway.targets]]`에 대상을 기술하면 CDK가 Gateway 대상, 아웃바운드 자격 증명 권한, Cedar 정책을 만듭니다.

쇼핑객의 신원이나 자체 자격 증명이 필요한 도구가 여기에 해당합니다. 둘 다 필요 없고 자주 쓰이는 도구는 로컬 도구로 두는 편이 낫습니다([도구 위치](../design-docs/tool-placement.md)).

API는 다음 세 가지를 전제로 할 수 있습니다.

- **호출은 Gateway에서 옵니다.** 대상에 설정한 자격 증명(OAuth 클라이언트, API 키 또는 Gateway의 IAM 역할)으로 인증됩니다.
- **모든 도구 호출은 `x-aria-actor-id` 요청 헤더로 쇼핑객을 나타냅니다.** 에이전트가 설정하고 Gateway 인터셉터가 확인하는 16진수 64자 actor ID입니다. 데이터를 이 값으로 한정하고, 값이 없는 호출은 거부하세요.
- **API는 쇼핑객의 로그인 토큰을 받지 않습니다.** 브라우저에서 온 어떤 토큰도 받지 않습니다. [신원과 토큰](../design-docs/identity-and-tokens.md)을 참고하세요.

## 대상 종류 선택

| `kind` | 용도 | 도구의 출처 | 아웃바운드 자격 증명 |
| --- | --- | --- | --- |
| `openapi` | OpenAPI 3.0 또는 3.1 문서로 정의된 HTTPS API | 각 `operationId` | OAuth 또는 API 키 |
| `apigateway` | Amazon API Gateway의 REST API 스테이지 | 직접 나열한 `operations` | Gateway의 IAM 역할(SigV4) 또는 API 키 |
| `lambda` | Lambda 함수 | 인라인 `toolSchema` | Gateway의 IAM 역할 |
| `mcp` | HTTPS로 접근하는 원격 MCP 서버 | 서버 자체의 도구 목록 | OAuth 또는 bearer 토큰으로 보내는 API 키 |

모델은 각 도구를 `<target>___<tool>`로 봅니다. 대상의 `allowedTools`에 있는 도구만 노출되므로, 서버가 새 도구를 공개해도 노출하려면 설정을 바꿔야 합니다.

## 단계

1. 대상이 Gateway의 IAM 역할을 쓰지 않는다면 [아웃바운드 자격 증명을 만듭니다](#1-아웃바운드-자격-증명-만들기).
2. 설정에 [대상을 추가합니다](#2-대상-추가).
3. [Cedar 정책을 지정합니다](#3-cedar-정책-지정). Prod에서는 필수입니다.
4. [계획, 검토, 배포합니다](#4-계획-검토-배포).
5. [스테이지에서 검증합니다](#5-스테이지에서-검증).
6. 모델이 도구를 언제 쓸지 안내가 필요하면 에이전트 프롬프트(`packages/agent/src/prompts/system.md`)에 언급합니다.

## 1. 아웃바운드 자격 증명 만들기

OAuth와 API 키 자격 증명은 Gateway와 같은 계정과 리전의 AgentCore Identity에 둡니다. 시크릿은 명령줄이 아니라 파일에 두고, 사용 후 파일을 삭제하세요.

IdP의 OAuth 클라이언트(클라이언트 자격 증명 grant)를 쓰는 경우:

```sh
aws bedrock-agentcore-control create-oauth2-credential-provider --region <region> \
  --name orders --credential-provider-vendor CustomOauth2 \
  --oauth2-provider-config-input file://orders-provider.json
```

```json
{"customOauth2ProviderConfig": {
  "oauthDiscovery": {"discoveryUrl": "https://idp.example.com/.well-known/openid-configuration"},
  "clientId": "<client id>",
  "clientSecret": "<client secret>"}}
```

API 키를 쓰는 경우:

```sh
aws bedrock-agentcore-control create-api-key-credential-provider --region <region> \
  --cli-input-json file://stores-key.json   # {"name": "stores", "apiKey": "<key>"}
```

출력의 `credentialProviderArn`을 대상의 `providerArn`으로 씁니다. CDK는 AgentCore가 그 자격 증명 하나를 가져오는 데 필요한 권한만 Gateway 역할에 부여합니다.

- Gateway 자체 워크로드 ID에 대한 `GetWorkloadAccessToken`
- 해당 공급자, 그 vault, Gateway 워크로드 ID에 대한 `GetResourceOauth2Token` 또는 `GetResourceApiKey`(AgentCore가 모두 확인)
- 해당 공급자 시크릿에 대한 `secretsmanager:GetSecretValue`

OAuth 대상은 기본값인 `grantType = "CLIENT_CREDENTIALS"`를 씁니다. Gateway가 API 자체 클라이언트용 토큰을 받고, 쇼핑객은 `x-aria-actor-id`로 식별합니다. AgentCore Gateway에는 다른 grant가 두 가지 더 있지만 아직 여기서는 쓸 수 없습니다.

- `AUTHORIZATION_CODE`(3-legged OAuth)는 쇼핑객이 브라우저에서 동의해야 하며, 그 동의를 위한 반환 URL이 필요합니다. ARIA는 반환 URL을 설정하지 않고 동의 흐름도 처리하지 않으므로 로더가 이 grant를 거부합니다.
- `TOKEN_EXCHANGE`는 Gateway가 에이전트의 on-behalf-of 토큰을 API 전용 토큰으로 교환하게 하므로, API가 쇼핑객을 암호학적으로 검증할 수 있습니다. ARIA 설정에는 아직 이 옵션이 없습니다.

## 2. 대상 추가

다음 필드는 모든 종류에 적용됩니다.

| 필드 | 의미 |
| --- | --- |
| `name` | 소문자, 숫자, 하이픈으로 이루어지고 문자로 시작하며 고유해야 합니다. 도구는 `<name>___<tool>`로 표시됩니다 |
| `kind` | `openapi`, `apigateway`, `lambda`, `mcp` |
| `allowedTools` | 에이전트가 호출할 수 있는 도구(최소 하나). 합성 단계에서 확인할 수 있는 문서나 스키마와 대조합니다 |
| `credentialType` | `GATEWAY_IAM_ROLE`(기본값), `OAUTH`, `API_KEY` |
| `providerArn` | 1단계에서 만든 자격 증명 공급자 |
| `scopes` | Gateway가 API용으로 요청하는 OAuth 스코프 |
| `grantType` | 기본값이자 허용되는 유일한 grant인 `CLIENT_CREDENTIALS`. 1단계를 참고하세요 |
| `apiKeyLocation`, `apiKeyName` | `openapi`와 `apigateway`의 `API_KEY`용: `HEADER`(기본값) 또는 `QUERY_PARAMETER`와 헤더/파라미터 이름(예: `x-api-key`) |
| `policyFile` | `packages/infra` 기준 상대 경로의 Cedar 정책 템플릿 |

### OpenAPI

```toml
[[gateway.targets]]
name = "orders"
kind = "openapi"
schemaFile = "examples/orders-openapi.json"
allowedTools = ["getOrderStatus", "listRecentOrders"]
credentialType = "OAUTH"                  # 또는 apiKeyName = "x-api-key"와 함께 "API_KEY"
providerArn = "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/oauth2credentialprovider/orders"
scopes = ["orders/read"]
policyFile = "components/policies/orders.cedar"
```

- `schemaFile`은 `packages/infra` 아래의 JSON 문서이며, 합성 단계에서 `allowedTools`를 문서의 `operationId`와 대조합니다. 대신 `schemaS3Uri = "s3://bucket/key.json"`으로 S3의 문서를 지정할 수 있습니다. Gateway 역할은 그 객체 하나만 읽을 수 있으며, 합성 단계에서는 확인하지 못합니다.
- `servers`에는 API의 고정 HTTPS URL을 씁니다. 값으로 호스트를 바꿀 수 있는 서버 변수는 피하세요. Gateway는 사설 IP 주소를 거부하므로 API에는 공개 엔드포인트가 필요합니다.
- Gateway는 단순한 파라미터와 JSON 본문을 지원합니다. `oneOf`, `anyOf`, `allOf`, 사용자 지정 파라미터 직렬화는 지원하지 않습니다([AgentCore OpenAPI 대상](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/gateway-schema-openapi.html)).
- **`x-aria-actor-id`, `x-correlation-id`, `forwardHeaders`의 이름을 파라미터로 선언하지 마세요.** Gateway가 이 헤더를 직접 추가하며, 문서가 선언한 헤더는 전달할 수 없습니다. 선언된 파라미터는 모델이 채우는 인자가 되기도 합니다. 합성 단계에서 이런 `schemaFile`은 거부되며, API는 요청에서 헤더를 읽으면 됩니다.
- API Gateway를 IAM 서명으로 호출하려면 대신 `kind = "apigateway"`를 쓰세요.

### API Gateway

```toml
[[gateway.targets]]
name = "stores"
kind = "apigateway"
restApiId = "a1b2c3d4e5"
stage = "prod"
operations = [{ name = "find_store", path = "/stores", method = "GET", description = "Nearest stores" }]
allowedTools = ["find_store"]
credentialType = "GATEWAY_IAM_ROLE"       # 또는 providerArn, apiKeyName과 함께 "API_KEY"
```

- `operations`가 각 도구의 이름을 고정하므로 이름, `allowedTools`, Cedar action이 일치합니다.
- `GATEWAY_IAM_ROLE`을 쓰면 Gateway가 SigV4로 호출에 서명하며, Gateway 역할은 `<restApiId>/<stage>/*`만 호출할 수 있습니다. API가 서명을 확인하도록 해당 메서드의 권한 부여를 `AWS_IAM`으로 설정하세요. 이 종류는 OAuth를 지원하지 않습니다.
- OpenAPI와 마찬가지로 `x-aria-actor-id`를 메서드 요청 파라미터로 선언하지 마세요.
- AgentCore는 노출하는 메서드만이 아니라 스테이지의 OpenAPI export 전체를 읽습니다. 모든 메서드에 메서드 응답을 하나 이상 지정하세요. 그렇지 않으면 대상 생성이 "responses is missing"으로 실패합니다. Lambda 프록시 메서드는 직접 선언하지 않으면 응답을 export하지 않습니다.

### Lambda

```toml
[[gateway.targets]]
name = "loyalty"
kind = "lambda"
lambdaArn = "arn:aws:lambda:<region>:<account>:function:loyalty-points"
allowedTools = ["get_points"]
toolSchema = [{ name = "get_points", description = "The shopper's loyalty points balance", inputSchema = { type = "object", properties = { months = { type = "integer", description = "History to include, in months" } } } }]
policyFile = "components/policies/loyalty.cedar"
```

- **호출.** Gateway는 IAM 역할로 함수를 호출합니다. 다른 계정의 함수에는 이 역할을 허용하는 리소스 정책이 필요합니다.
- **스키마.** 인라인 스키마는 `type`, `description`, `properties`, `required`, `items`를 지원합니다. 그 밖의 제약은 함수에서 강제하세요.
- **입력.** 함수는 도구 인자를 event로 받습니다. 도구 이름은 `context.client_context.custom["bedrockAgentCoreToolName"]`, 헤더는 `["bedrockAgentCorePropagatedHeaders"]`에 있습니다.
- **결과.** 결과 객체를 직접 반환하면 Gateway가 감쌉니다. 상품 카드에는 `product.v1`을 씁니다. 실패한 호출은 예외를 발생시키세요. API Gateway 형식의 `statusCode`/`body` 딕셔너리는 오류를 설명하더라도 일반 도구 콘텐츠가 됩니다.
- **예제.** `packages/mcp-servers/saved-items-lambda`가 완전한 예제이며, 아래 [Lambda 대상 계약](#lambda-대상-계약)이 그 계약을 설명합니다.

### MCP 서버

```toml
[[gateway.targets]]
name = "catalog"
kind = "mcp"
endpoint = "https://catalog.example.com/mcp"
allowedTools = ["search_catalog"]
credentialType = "OAUTH"                  # 또는 "Authorization: Bearer <key>"로 보내는 "API_KEY"
providerArn = "arn:aws:bedrock-agentcore:<region>:<account>:token-vault/default/oauth2credentialprovider/catalog"
scopes = ["catalog:read"]
policyFile = "components/policies/catalog.cedar"
```

- 서버는 Gateway에서 HTTPS로 접근할 수 있어야 합니다.
- `allowedTools`에는 서버의 `tools/list`에 나오는 이름을 씁니다.
- `x-aria-actor-id`, `x-correlation-id`, `forwardHeaders`는 각 MCP 요청의 HTTP 헤더로 도착합니다.

로컬 도구 대신 자체 카탈로그 서버로 검색하려면 `[tools.productSearch] provider = "disabled"`로 설정하고 그 서버를 대상으로 등록합니다. 이 경우 모든 검색이 Gateway를 거칩니다.

## 3. Cedar 정책 지정

`policyFile`은 Cedar 템플릿, 즉 `packages/infra/components/policies/` 아래의 `.cedar` 파일입니다. 저장 상품 샘플 전용 정책이 있는 `sample/`을 뺀 어느 폴더에나 둘 수 있습니다. 로더는 이 폴더 밖의 경로나 존재하지 않는 파일을 거부합니다. `{{GATEWAY_ARN}}`과 `{{TOOL}}`은 허용된 도구마다 한 번씩 채워지며, `{{TOOL}}`은 `<target>___<tool>`입니다. Cedar는 기본적으로 거부하므로, 정책 엔진이 강제 모드가 되면 허용 정책이 없는 도구는 거부됩니다. 샘플의 `components/policies/sample/01-allow-shopper-tools.cedar`에서 시작하세요. 줄여 쓰면 다음과 같습니다(파일은 `limit`도 1–20으로 제한합니다).

```cedar
permit(
  principal is AgentCore::OAuthUser,
  action == AgentCore::Action::"{{TOOL}}",
  resource == AgentCore::Gateway::"{{GATEWAY_ARN}}"
)
when { context has input };
```

- **`policy.mode = "ENFORCE"`**에서는 모든 단계에서 모든 고객 대상에 `policyFile`이 필요합니다. Prod의 관리형 Gateway에는 Cedar를 켜고 `ENFORCE`로 두어야 합니다.
- **강제하기 전에** Dev에서 `policy.mode = "LOG_ONLY"`로 허용과 거부를 테스트하세요.
- **principal**은 on-behalf-of 토큰에서는 쇼핑객입니다. 정책은 `context.input`의 도구 인자와 비교할 수 있습니다. [신원과 토큰](../design-docs/identity-and-tokens.md#cedar-and-data-authorization)을 참고하세요.
- **Cedar는 호출을 승인할 뿐 데이터를 승인하지 않습니다.** API가 직접 데이터를 actor ID로 한정해야 합니다.

## 4. 계획, 검토, 배포

`pnpm check`는 스택을 합성하고 다음과 같은 잘못된 대상을 거부합니다.

- 알 수 없는 `operationId`
- 누락된 `providerArn`이나 `apiKeyName`
- 전달되는 헤더를 선언한 문서
- 10개를 넘는 전달 헤더

그다음 [릴리스](release.ko.md)의 검토된 계획으로 배포합니다. diff에서 새 대상, IAM 권한, 공개로 바뀌는 리소스가 없는지 확인하세요.

## 5. 스테이지에서 검증

- 도구가 필요한 질문을 에이전트에게 하고, 트레이스나 웹 앱에서 도구 호출과 결과를 확인합니다.
- API 로그에는 Gateway의 자격 증명과 `x-aria-actor-id`가 보여야 하며, 브라우저에서 온 토큰은 없어야 합니다.
- `x-aria-actor-id`가 없는 `tools/call`은 실패해야 합니다. 인터셉터가 거부하며, API도 거부해야 합니다.
- 모델에게 도구가 제공되지 않으면 `allowedTools`를 Gateway의 `tools/list`와 비교합니다.

## 기존 Gateway 사용

다른 곳에서 관리하는 Gateway를 호출하려면 `gateway.mode = "external"`로 설정합니다.

```toml
[gateway]
mode = "external"
endpoint = "https://<gateway-id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp"
allowedTools = ["orders___getOrderStatus"]

[gateway.agentAuth]
flow = "on_behalf_of"
credentialProvider = "shopping-gateway"
scopes = ["shopping:tools"]
```

- `endpoint`는 AgentCore Gateway URL(`https://<id>.gateway.bedrock-agentcore.<region>.amazonaws.com/mcp`)이어야 합니다. 에이전트는 다른 호스트에는 토큰을 보내지 않으며, 다른 값이면 합성이 실패합니다.
- `allowedTools`에는 그 Gateway가 노출하는 전체 도구 이름을 씁니다.
- 그 Gateway의 인가자는 `credentialProvider`가 에이전트용으로 발급한 토큰을 허용해야 합니다. [신원과 토큰](../design-docs/identity-and-tokens.md#the-agents-gateway-token)을 참고하세요.
- 이 모드에서 ARIA는 대상, 인터셉터, 정책을 만들지 않습니다. `x-aria-actor-id` 확인을 포함해 해당 Gateway의 소유자가 제공합니다.
- Prod에서는 거부를 테스트한 뒤 `authorizationVerified = true`가 필요합니다.

## Gateway 없이 실행

`gateway.mode = "disabled"`는 Gateway를 만들지 않으며, 에이전트는 상품 검색 같은 로컬 도구를 계속 씁니다. 이 모드에는 `policy.enabled = false`가 필요하고 `gateway.targets`는 비어 있어야 합니다. Runtime은 `GATEWAY_ENABLED=false`를 받고 엔드포인트 조회를 건너뜁니다.

## 문제 해결

| 증상 | 확인할 것 |
| --- | --- |
| 합성 실패 | 오류 메시지가 필드를 알려 줍니다: 알 수 없는 `operationId`, 누락된 `providerArn`이나 `apiKeyName`, 선언된 전달 헤더, 너무 많은 헤더 |
| 모델에게 도구가 제공되지 않음 | `allowedTools`와 Gateway의 `tools/list` 비교. 에이전트는 이 이름으로 필터링합니다 |
| 에이전트가 Gateway에서 401 또는 403을 받음 | 에이전트 토큰. [운영](operations.ko.md)의 문제 해결 표를 참고하세요 |
| 도구 호출이 "Access denied by the Gateway's access check"로 실패 | 인터셉터가 호출을 거부했습니다. 사유는 인터셉터 로그에 있고 `InterceptorDenials` 경보가 울립니다 |
| 도구 호출이 "An internal error occurred"로 실패 | 대상이 실패했거나, 대상의 자격 증명을 가져오지 못했거나, 인터셉터 자체가 실패했습니다. 대상과 인터셉터의 로그에 이유가 있습니다 |
| API가 401 또는 403을 반환 | 자격 증명 공급자, 스코프, `apiKeyName`과 `apiKeyLocation` |

## 검증 현황

에이전트를 통해 처음부터 끝까지 실행해 본 대상 종류, 자격 증명, Cedar 모드는 [품질 점수](../QUALITY_SCORE.md#gateway-and-identity)에 기록되어 있습니다.

## Lambda 대상 계약

### 대상 호출 계약

Gateway는 도구 인자를 `event`에 직접 전달합니다:

```json
{"limit":5}
```

도구 이름과 전달된 헤더는 이벤트 필드가 아니라 컨텍스트 메타데이터입니다:

```python
context.client_context.custom.bedrockAgentCoreToolName
# "saved-items___get_saved_items"
context.client_context.custom.bedrockAgentCorePropagatedHeaders
# {"x-aria-actor-id": "<16진수 64자>", "x-correlation-id": "<16진수 32자>"}, 그리고 gateway.forwardHeaders
```

`_tool_name()`은 `___` 접두부를 제거합니다. 핸들러는 결과 이름이 `get_saved_items`와 같아야 하며, 이름이 없거나 알 수 없으면 `ValueError("Unknown saved-items tool")`를 발생시킵니다. `limit` 인자만 허용합니다.

쇼핑객의 ID는 `x-aria-actor-id`에서 가져옵니다. 이 헤더는 에이전트 코드가 설정하며(모델은 설정할 수 없음), Gateway가 대상을 호출하기 전에 인터셉터가 검사합니다. 핸들러는 헤더 이름을 대소문자 구분 없이 비교하고 64자 소문자 16진수 actor ID를 요구합니다. ID는 모델이 작성할 수 있는 도구 파라미터가 되어서는 안 되며, 대상도 ID를 인자로 받으면 안 됩니다.

### 결과와 오류 계약

결과 객체를 직접 반환합니다:

```python
return {"schemaVersion": "product.v1", "count": len(items), "products": items}
```

Gateway가 MCP 콘텐츠 봉투를 생성합니다. API Gateway의 `statusCode`/`body` 래퍼를 반환하거나 중첩 MCP 봉투를 만들지 마세요. 이러한 값은 일반 도구 콘텐츠가 됩니다. `products`는 카탈로그 객체 목록이고 `count`는 목록 길이입니다. 웹 클라이언트는 `schemaVersion == "product.v1"`을 요구하며 카드를 표시하기 전에 상품 필드를 검증합니다.

| 조건 | Lambda 동작 |
| --- | --- |
| 올바른 호출 | 직접 `product.v1` 객체 반환: 해당 actor에 대한 일정한 예제 카탈로그 표본, `limit` 이하(기본 5개, 최대 20개) |
| actor ID 누락 또는 잘못된 형식 | `PermissionError("Saved items need the shopper's identity")` 발생 |
| 알 수 없거나 누락된 도구 | `ValueError` 발생 |
| 지원하지 않는 인자 | `ValueError` 발생 |

이는 HTTP 400/500 응답 딕셔너리가 아니라 Lambda 도구 실패입니다. 웹은 실패한 도구 호출을 대화 기록에서 숨기고, 에이전트는 호출을 고치거나 실패를 설명합니다.

샘플의 예제 카탈로그 데이터는 고객 저장소를 대신합니다. 실제 대상은 actor ID로 범위를 제한해 해당 쇼핑객의 기록을 읽습니다.
