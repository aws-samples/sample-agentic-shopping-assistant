# 통합 계약

[English](integrations.md)

## 설정과 소유권

`ARIA_CONFIG`에 명시적인 TOML 파일을 지정합니다. 지정하지 않으면 CDK는 Git에서 제외된 `packages/infra/.toml`을 읽습니다. 설정은 합성 시 검증하며 CDK가 해당 환경 변수를 API와 Runtime에 주입합니다. 알 수 없는 키와 모순된 공급자 선택은 검증에 실패합니다. 모든 키와 기본값, 허용 범위는 [설정 참조](../generated/config-reference.md)에 있습니다.

고정 에이전트와 AgentCore Memory는 이 프로젝트에서 유지 관리합니다. 관리형 Cognito, Gateway, 선택적 OpenSearch 리소스는 선택한 통합에만 생성합니다. 기존 Gateway/IdP/검색 엔드포인트와 자격 증명 공급자는 고객 소유입니다. 대상 생성은 연결 등록이며 원격 MCP 서버 배포나 IdP 변경을 수행하지 않습니다. 모든 스택과 리소스 이름은 `app.ns`와 `app.stage`에서 만들어지므로, 둘 중 하나를 바꾸면 새 스택 묶음이 생기고 기존 스택과 데이터는 그대로 배포되어 있습니다. 통합을 끄거나 관리형 통합을 직접 운영하는 것으로 바꾸면 해당 스택(`<ns>Auth`, `<ns>Opensearch`, `<ns>Gateway`, `<ns>Evaluations`)이 CDK 앱에서 빠지지만, 배포는 앱에 없는 스택을 삭제하지 않습니다. 그 스택과 리소스는 보존, 가져오기, 삭제 중 하나를 명시적으로 결정하세요.

## 신원

관리형 Cognito는 PKCE를 사용하는 인증 코드 클라이언트, `aria/chat`과 `aria-tools/invoke` 액세스 스코프, 관리형 로그인을 사용합니다. 두 번째 기밀 클라이언트는 에이전트용입니다. 에이전트가 Gateway 토큰을 받는 토큰 교환에만 쓰입니다([신원과 토큰](../design-docs/identity-and-tokens.md)). 인증 앱(TOTP)을 쓰는 다단계 로그인은 모든 스테이지에서 `auth.mfa`를 따릅니다. 기본값인 `optional`에서는 인증 앱을 등록한 쇼핑객에게만 코드를 묻습니다. 관리형 로그인은 등록 화면을 제공하지 않고, 웹 앱에도 등록 화면이 없습니다. `required`는 모든 쇼핑객이 다음 로그인 때 인증 앱을 등록하고 매번 코드를 입력하게 합니다. `off`는 MFA를 끕니다. 배포된 사용자 풀도 이 사이에서 바꿀 수 있습니다. Prod는 Cognito Plus 위협 보호도 켜며, Cognito는 이때 `optional`을 권장합니다. 계정 복구는 이메일로만 합니다. 로그인 도메인은 `auth.domainPrefix`로 다른 접두사를 지정하지 않으면 `<ns>-<account>.auth.<region>.amazoncognito.com`입니다. 사용자 풀의 접두사는 배포 후 바꿀 수 없으므로 첫 배포 전에 정하세요. 웹 코드에는 클라이언트 시크릿이 없으며 API에는 ID 토큰이 아닌 액세스 토큰을 사용합니다. 브라우저 토큰/상태는 sessionStorage에 저장하며 로그아웃은 IdP로 리디렉션합니다. 앱에는 이미지, 플러그인, `<base>`를 제한하는 Content-Security-Policy meta 태그가 들어 있습니다. `frame-ancestors`와 더 엄격한 정책은 웹 호스트에서 설정하세요([FRONTEND.md](../FRONTEND.md#content-security-policy)). `NUXT_PUBLIC_CSP_IMG_SRC`, `NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`를 포함한 웹 설정은 [FRONTEND.md](../FRONTEND.md#configuration)에 있습니다.

기존 OIDC 공급자를 사용하는 경우:

```toml
[auth]
provider = "oidc"
issuer = "https://identity.example.com/"
clientId = "shopping-web"
audiences = ["shopping-api"]
scopes = ["shopping:chat"]
callbackUrls = ["https://shop.example.com/auth/callback"]
logoutUrls = ["https://shop.example.com/"]
# tenantClaim = "tenant_id"   # optional; must be issued by the trusted IdP
```

끝의 슬래시를 포함해 정확한 issuer를 유지합니다. IdP에 공개 인증 코드/PKCE 클라이언트와 정확한 리디렉션/로그아웃 URL을 등록합니다. JWT 액세스 토큰, HTTPS OIDC discovery/JWKS, RS256 또는 ES256 서명, API 스코프/audience를 설정합니다. `aud` 대신 `client_id`를 사용하는 공급자(Cognito 액세스 토큰 포함)는 `allowedClients`를 설정하고 `audiences`를 비웁니다. 둘 다 설정하면 API는 두 조건을 모두 요구합니다. 배포당 issuer 하나를 지원합니다. SAML 전용/불투명 토큰 시스템에는 상위 연동이나 호환 JWT 브로커가 필요합니다.

Gateway 도구를 쓰면 `[gateway.agentAuth]`도 설정합니다. 에이전트가 쇼핑객의 토큰을 자체 Gateway 토큰으로 교환하므로 IdP가 RFC 8693 토큰 교환을 지원해야 하며, 그렇지 않으면 `m2m`을 씁니다. 웹 앱도 도구 스코프를 요청해야 합니다([신원과 토큰](../design-docs/identity-and-tokens.md#the-agents-gateway-token)). 관리형 Gateway에서는 두 흐름 모두 `agentAuth.clientId`가 필요합니다. 인터셉터는 이 클라이언트에 발급되지 않은 토큰의 도구 호출을 모두 거부합니다. 이 값은 쇼핑객용 클라이언트일 수 없고, on-behalf-of의 `audience`도 `auth.audiences`에 있는 값일 수 없습니다. 그렇지 않으면 쇼핑객 자신의 토큰이 Gateway를 통과합니다.

웹 환경 변수(공개 설정만):

```dotenv
API_URL=https://api.example.com
AUTH_PROVIDER=oidc
OIDC_ISSUER=https://identity.example.com/
OIDC_CLIENT_ID=shopping-web
OIDC_SCOPES=openid profile email shopping:chat
# Gateway 도구(on_behalf_of)를 쓰면 도구 스코프를 추가합니다: ... shopping:chat shopping:tools
OIDC_REDIRECT_URI=https://shop.example.com/auth/callback
OIDC_LOGOUT_URI=https://shop.example.com/
BRAND_NAME=NORDLY
```

정적으로 생성한 Nuxt 사이트는 빌드 시 이 값을 적용하므로 변경 시 다시 빌드합니다. Issuer/client/scope를 CDK와 일치시키세요. 브라우저는 `openid profile email`을 추가 요청할 수 있습니다. API와 Runtime은 쇼핑객의 액세스 토큰을 받으며, Gateway는 받지 않습니다. 서명, 만료, issuer, 수신 대상, 스코프 검사 실패 시 요청을 거부합니다. Runtime은 직접 호출되어도 세션 소유권을 독립적으로 확인합니다.

## 카탈로그 공급자

```toml
[tools.productSearch]
provider = "fixture"    # fixture | opensearch | http | disabled
# catalogFile = "catalog.toml"   # 카탈로그 정의 파일. 비워 두면 패키지에 포함된 예제를 씁니다
```

상품 검색은 에이전트의 로컬 도구이므로([도구 위치](../design-docs/tool-placement.md)) 어떤 공급자를 고르든 Runtime에서 실행되며, Runtime은 항상 VPC의 프라이빗 서브넷에서 실행됩니다.

- **`fixture`:** 패키지에 포함된 예제 카탈로그입니다. 합성 상품 150개를 메모리에서 검색합니다. 고객 재고가 아니므로 Prod에서는 거부되며 `catalogFile`을 받지 않습니다. Dev 템플릿이 이 공급자를 선택합니다.
- **`opensearch`:** 이 앱이 같은 VPC에 생성하는 OpenSearch 3.5 도메인에 고객 카탈로그를 둡니다. 도메인은 VPC 안에서만 접근할 수 있으며, 비공개 카탈로그 버킷과 인덱싱 작업이 함께 생성됩니다. Runtime 보안 그룹은 443 포트로 도메인에 접근할 수 있습니다. 카탈로그는 `scripts/index_catalog.sh`로 적재합니다([운영](operations.ko.md#카탈로그-런북)).
- **`http`:** 고객의 HTTPS 카탈로그 API입니다. Prod 템플릿은 자리표시자 `endpoint`와 함께 이 공급자를 선택합니다.
- **`disabled`:** 검색 도구가 없습니다.

HTTP와 fixture 모드는 도메인을 생성하지 않습니다.

## 자체 카탈로그 사용

`opensearch`에서는 파일 두 개를 준비합니다. 하나는 카탈로그로, JSON 배열이나 한 줄에 상품 하나씩 담은 JSONL입니다. 다른 하나는 TOML로 작성한 카탈로그 정의입니다. `http`에서는 정의만 준비하고 상품은 고객 API가 제공합니다. 정의는 어떤 필드가 있는지, 어떤 필드가 검색 도구의 필터가 되는지, 어떤 필드를 텍스트로 검색하는지, 어떤 텍스트를 임베딩하는지, 상품을 카드에 어떻게 매핑하는지 정합니다. 검색 도구의 매개변수, OpenSearch 인덱스, 하이브리드 쿼리, HTTP 카탈로그로 보내는 요청이 모두 정의에서 만들어집니다([카탈로그 검색](../design-docs/catalog-search.md)).

1. 예제 정의 `packages/catalog/src/aria_catalog/example/catalog.toml`을 스테이지의 `.toml` 옆에 복사하고 고객 필드에 맞게 수정합니다.
2. `tools.productSearch.catalogFile`에 그 파일을 `.toml` 기준 상대 경로로 지정하고 `opensearch`나 `http`를 선택합니다. 로더가 합성 시 정의를 검증하므로, 잘못된 부분이 있으면 `scripts/plan_deployment.sh`가 키와 규칙을 알려 주며 실패합니다.
3. 배포합니다. `opensearch`는 카탈로그를 인덱싱하고([운영](operations.ko.md#카탈로그-인덱싱)), `http`는 `endpoint`를 고객 API로 지정합니다.

예제와 상품 150개, 그리고 쇼핑객이 이를 통해 받는 결과는 [상품 검색 명세](../product-specs/product-search.md#the-example-catalog)에 있습니다.

### 정의 파일

가격 범위 필터가 있는 카탈로그의 전체 정의입니다.

```toml
[catalog]
name = "Acme"
description = "Search products in the Acme catalog. Use this tool only for searching products."
idField = "sku"
analyzer = "nori"

[[catalog.fields]]
name = "sku"
type = "keyword"

[[catalog.fields]]
name = "title"
type = "text"
search = true
boost = 3

[[catalog.fields]]
name = "summary"
type = "text"
search = true

[[catalog.fields]]
name = "department"
parameter = "category"
type = "keyword"
filter = true
values = ["Apparel", "Footwear", "Accessories"]
description = "Top-level category"

[[catalog.fields]]
name = "audience"
type = "keyword"
filter = true
values = ["Men", "Women", "Kids"]
description = "Who the product is for"

[[catalog.fields]]
name = "price"
type = "float"
filter = "range"
description = "Price in KRW"

[[catalog.fields]]
name = "line"
type = "keyword"

[[catalog.fields]]
name = "kind"
type = "keyword"

[[catalog.fields]]
name = "colour"
type = "keyword"

[[catalog.fields]]
name = "season"
type = "keyword"

[[catalog.fields]]
name = "year"
type = "integer"

[[catalog.fields]]
name = "occasion"
type = "keyword"

[catalog.embedding]
text = "{title}. {summary}"

[catalog.card]
id = "sku"
productDisplayName = "title"
gender = "audience"
masterCategory = "department"
subCategory = "line"
articleType = "kind"
baseColour = "colour"
season = "season"
year = "year"
usage = "occasion"
```

이 정의의 검색 도구는 `query`, `category`, `audience`, `price`(`min`, `max` 또는 둘 다를 담은 객체), `limit`를 받습니다. 모든 키와, `parse_definition()`(`packages/catalog/src/aria_catalog/definition.py`)이 적용하는 규칙은 다음과 같습니다.

| 키 | 필수 | 규칙 |
| --- | --- | --- |
| `catalog.name` | 예 | 공백이 아닌 텍스트. 인덱싱 매니페스트에서 카탈로그를 가리킵니다 |
| `catalog.description` | 예 | 공백이 아닌 텍스트. 검색 도구 설명이 이 문장으로 시작합니다 |
| `catalog.idField` | 예 | 정의된 `keyword` 또는 `integer` 필드. 상품 ID입니다 |
| `catalog.analyzer` | 아니요 | `standard`(기본값) 또는 한국어용 `nori`. 모든 `text` 필드에 적용됩니다 |
| `fields[].name` | 예 | `[A-Za-z_][A-Za-z0-9_]*`, 최대 64자, 고유. `_`로 시작할 수 없고, 인덱스의 벡터 필드인 `embedding`도 쓸 수 없습니다 |
| `fields[].type` | 예 | `keyword`, `text`, `integer`, `float`, `boolean` 중 하나 |
| `fields[].filter` | 아니요 | `true`는 정확히 일치하는 필터로, `text`를 제외한 모든 타입에 씁니다. `"range"`는 최솟값과 최댓값 필터로, `integer`나 `float`에 씁니다. 필터는 최대 12개 |
| `fields[].values` | keyword 필터에 필요 | `filter = true`인 `keyword` 필드에만, 그리고 반드시 씁니다. 값 1~100개, 각 값은 공백이 아니고 앞뒤 공백이 없으며 최대 100자이고, 대소문자를 무시해도 서로 달라야 합니다 |
| `fields[].search` | 하나 이상의 필드에 필요 | `text` 또는 `keyword` 필드에 `true`를 지정하면 텍스트로 검색합니다 |
| `fields[].boost` | 아니요 | 0보다 크고 10 이하, 기본값 1. `search = true`일 때만 씁니다 |
| `fields[].description` | 아니요 | 도구 매개변수의 설명 |
| `fields[].parameter` | 아니요 | 필터에만 씁니다. 도구 매개변수 이름이며 기본값은 필드 이름입니다. `fields[].name`과 같은 패턴을 따르고, 필터 사이에서 고유해야 하며, `query`나 `limit`일 수 없습니다 |
| `embedding.text` | 예 | `{field}` 자리표시자가 하나 이상 있는 템플릿. 각 자리표시자는 정의된 필드를 가리키며 형식 지정자나 변환을 쓸 수 없습니다. 중괄호 문자 자체는 `{{`나 `}}`로 씁니다 |
| `embedding.model` | 아니요 | `global.cohere.embed-v4:0`(기본값), `us.cohere.embed-v4:0` 같은 다른 Embed v4 추론 프로필, 또는 `cohere.embed-v4:0` |
| `card.*` | 예 | product.v1의 모든 필드. 각각 정의된 필드를 가리킵니다(아래 참조) |
| `search.k` | 아니요 | 검색마다 찾는 벡터 이웃 수. 1~100, 기본값 50 |

`fields[]`는 `[[catalog.fields]]` 항목이고, `embedding`, `card`, `search`는 각각 `[catalog.embedding]`, `[catalog.card]`, `[catalog.search]`입니다. 어디서든 알 수 없는 키는 오류입니다.

모든 필드가 인덱싱되지만 검색 결과로는 카드 필드만 돌아옵니다. 예를 들어 `price`는 필터로 쓸 수 있지만 쇼핑객에게는 보이지 않습니다. 각 상품은 인덱싱할 때 검사합니다. 값은 필드 타입과 맞아야 하고, keyword 필터의 값은 그 `values` 중 하나여야 하며, ID는 ASCII 문자, 숫자, `._:-`로 이루어진 최대 128자의 고유한 값이어야 하고, 모든 카드 필드가 있어야 하며, 임베딩 텍스트가 비어 있으면 안 됩니다. 카드에 쓰지 않는 필드는 없어도 됩니다.

### 카드

`[catalog.card]`는 웹이 카드로 렌더링하는 product.v1 필드 10개([상품 카드](../product-specs/product-cards.md))를 각각 고객 필드 하나에 매핑합니다.

| 카드 필드 | 카드에 표시되는 곳 | 고객 필드 |
| --- | --- | --- |
| `id` | 인용 ID | `idField` |
| `productDisplayName` | 상품 이름. 비어 있으면 안 됩니다 | `keyword` 또는 `text` |
| `masterCategory`, `season` | 이름 위의 줄 | `keyword` 또는 `text` |
| `subCategory`, `articleType` | 분류 경로 `subCategory › articleType`(둘이 같으면 하나) | `keyword` 또는 `text` |
| `baseColour` | 색상과 색상 견본 | `keyword` 또는 `text` |
| `gender`, `usage` | 색상 옆의 라벨 두 개 | `keyword` 또는 `text` |
| `year` | 색상 견본 위의 연도 | `integer`, 또는 정수를 담은 `keyword` |

모든 카드 필드가 필수이며, 여러 카드 필드가 같은 필드를 가리켜도 됩니다. 텍스트 값은 최대 2,000자입니다. 카드 필드가 빠진 상품은 인덱싱할 때 거부되고, 검색 결과로 돌아오면 제외됩니다. 카드 필드는 패션 상품에 맞춰져 있으므로, 다른 상품을 다루는 카탈로그도 필드마다 고객 필드를 하나씩 지정해야 합니다([기술 부채 목록](../exec-plans/tech-debt-tracker.md)).

### 공급자 계약

- **`fixture`:** 예제 정의와 예제 상품만 다룹니다. 모든 필터를 만족해야 하며, `query`의 각 단어가 검색 필드에 있으면(대소문자 무시) 그 필드의 boost만큼 점수를 얻습니다. 임베딩은 쓰지 않습니다.
- **`opensearch`:** 인덱싱 작업이 고객 카탈로그를 별칭 `openSearchIndex`(기본값 `products`) 뒤의 `<alias>-<version>`에 적재합니다. `query`가 있는 검색은 하이브리드 쿼리를 실행합니다. 검색 필드를 boost와 함께 키워드로 매칭한 결과와, 임베딩이 가장 가까운 상품 `k`개를 각각 필터와 함께 구한 뒤 역순위 융합(RRF)으로 합칩니다. `query`가 없는 검색은 필터만 씁니다. 쿼리를 임베딩하지 못하면 키워드로만 검색하고 `product_search_embedding_failed`를 기록합니다.
- **`http`:** 고객 API입니다. 아래를 참조하세요.

HTTP 어댑터:

```toml
[tools.productSearch]
provider = "http"
endpoint = "https://catalog.example.com/search"
catalogFile = "catalog.toml"
resultPath = "products"
# secretArn = "arn:aws:secretsmanager:REGION:ACCOUNT:secret:catalog-..."
```

어댑터는 도구 매개변수 이름을 키로 쓰는 평평한 JSON 객체를 POST합니다. `query`, 모든 필터 매개변수, `limit`(1~20)이 들어갑니다. 모델이 주지 않은 조건은 `null`이고, 범위는 `{"min": …, "max": …}`이며 열린 쪽은 `null`입니다. keyword 값은 정의에 선언된 철자로 전달됩니다. 위 정의의 예:

```json
{"query": "가벼운 러닝화", "category": "Footwear", "audience": null, "price": {"min": null, "max": 100000}, "limit": 3}
```

응답은 JSON이어야 하며, `resultPath`(점으로 구분한 경로, 예: `data.items`, 기본값 `products`)에 상품 20개 이하의 목록이 있어야 합니다. 각 상품은 인덱싱할 때와 같이 정의의 필드 이름을 쓰는 객체입니다.

```json
{"products": [{"sku": "A-1001", "title": "경량 러닝화 Lightweight Running Shoes", "audience": "Men", "department": "Footwear", "line": "Shoes", "kind": "Sports Shoes", "colour": "Grey", "season": "Spring", "year": 2025, "occasion": "Sports"}]}
```

도구는 각 상품을 `[catalog.card]`로 매핑하고, 카드를 `schemaVersion: product.v1`, `count`, `products`로 감쌉니다. 카드가 되지 못한 상품은 제외하고 개수를 셉니다. 상품은 있지만 유효한 카드가 하나도 없는 응답, JSON이 아닌 응답, 목록이 없는 응답은 성공한 빈 검색이 아니라 오류입니다. 필터 적용과 순위는 고객 API가 정합니다.

선택적 Secrets Manager 값은 `{"header":"x-api-key","value":"..."}` 또는 명시적인 `Authorization` 헤더 값입니다. Runtime 역할은 해당 시크릿만 읽을 수 있으며, 호출자 토큰이나 도구가 제공한 URL은 이 HTTP 요청에 사용하지 않습니다. 리디렉션은 거부하며 제한 시간은 10초, 응답 크기는 256 KiB/상품 20개입니다. Runtime은 VPC에서 엔드포인트를 호출합니다. 공개 HTTPS 엔드포인트는 VPC의 NAT 게이트웨이를 거치고, 비공개 API는 VPC 프라이빗 서브넷에서 가는 경로가 필요합니다(이 경우 `vpc.vpcId`로 기존 VPC를 사용하세요). 그렇지 않으면 카탈로그를 Gateway 고객 대상으로 두고 로컬 검색을 끄세요.

### 한국어 텍스트, 임베딩, 데이터 위치

- **Nori:** `analyzer = "nori"`는 모든 `text` 필드를 인덱스와 쿼리 모두에서 OpenSearch의 Nori 한국어 분석기로 분석합니다. Nori는 Amazon OpenSearch Service의 선택적 패키지이며 리전과 엔진 버전마다 제공 여부가 다릅니다. 프리플라이트는 해당 리전에서 OpenSearch 3.5용 Nori를 제공하는지 확인하고, 스택이 도메인에 연결합니다. 제공되지 않는 곳에서는 `standard`를 쓰세요.
- **Embed v4:** `opensearch`에서는 인덱싱 작업이 각 상품의 `[catalog.embedding].text`를 Cohere Embed v4로 임베딩하고(`input_type` `search_document`), 검색 도구가 쇼핑객의 쿼리를 임베딩합니다(`search_query`). 둘 다 1024차원 float입니다. 먼저 계정에서 모델 액세스를 활성화하세요. 프리플라이트는 추론 프로필이 `ACTIVE`이고 모델이 승인되었는지 확인합니다. `fixture`와 `http` 공급자는 임베딩을 호출하지 않습니다.
- **`global` 프로필과 데이터 위치:** 기본 모델 `global.cohere.embed-v4:0`은 전역 리전 간 추론 프로필로, 모든 상용 리전에서 요청을 처리할 수 있습니다. ap-northeast-2에서는 Embed v4를 `global` 프로필로만 제공하며 리전 내 모델이나 지역 프로필이 없으므로, 카탈로그의 임베딩 텍스트와 모든 쇼핑객의 쿼리가 한국 밖에서 처리될 수 있습니다. 그 리전에서 `opensearch`를 선택하기 전에 데이터 위치 담당자와 결정하세요. `us.cohere.embed-v4:0` 같은 지역 프로필을 제공하는 곳에서는 `embedding.model`을 지정해 요청을 그 지역 안에 둘 수 있습니다([Embed v4 참고](../references/bedrock-embed-v4-notes.md#data-residency)).

### 제한

| 제한 | 값 |
| --- | --- |
| 필터 | 12개. 각각 최대 100자인 값 최대 100개 |
| `query` | 200자 |
| `limit` | 기본 3, 최대 20 |
| `search.k` | 100 |
| Runtime이 `CATALOG_DEFINITION`과 그 뒤의 값으로 받는 압축 JSON 형태의 정의 | 16KB: AgentCore Runtime의 CloudFormation 리소스가 값 하나에 받는 최대 2,048바이트짜리 환경 변수 값 8개이며, 넘으면 합성이 실패합니다. 예제는 약 2,400자이며, 긴 값 목록과 설명이 가장 많이 차지합니다 |
| 카드 텍스트와 ID | 텍스트 필드마다 2,000자. ID는 ASCII 문자, 숫자, `._:-`로 이루어진 최대 128자 |

도구 스키마에는 `enum`, `maxLength`, `minimum`, `maximum`, `default`가 들어갑니다. 설명은 모델을 안내할 뿐이며, 모든 규칙은 `aria_catalog`가 코드로 적용합니다([도구 스키마](../design-docs/tool-schemas.md)).

## Gateway 대상과 모드

고객 API, MCP 서버, Lambda 함수는 `[[gateway.targets]]`에 Gateway 대상으로 등록합니다. [Gateway 등록](gateway-onboarding.ko.md)이 다음을 다룹니다.

- 대상 종류별 자격 증명과 Cedar 정책
- 기존 Gateway 사용(`gateway.mode = "external"`)
- Gateway 없이 실행(`"disabled"`)

대상은 쇼핑객의 토큰을 받지 않습니다. `x-aria-actor-id`를 받으며, Gateway는 대상 자체의 자격 증명으로 인증합니다.

- **정책.** Prod 관리형 Gateway에는 `policy.enabled = true`와 `policy.mode = "ENFORCE"`가 필요하며, 모든 고객 대상에는 `policyFile`이 있어야 합니다. 정책은 Dev에서 차단 없이 먼저 검증하세요. 빈 정책이나 검증하지 않은 정책은 운영 권한 관리가 아닙니다.
- **노출되는 도구.** 명시적으로 나열한 `allowedTools`만 노출되므로, 새 도구에는 설정과 정책 변경이 필요합니다.
- **출력.** 쇼핑객에게 보이는 도구 출력은 상품 카드뿐이며, 상품 카드에는 유효한 `product.v1` 결과가 필요합니다. 그 밖의 도구 출력은 브라우저에 전달되지 않습니다.
- **범위.** 이 예제는 결제나 다른 쓰기 흐름을 구현하지 않습니다.

## Memory

AgentCore Memory만 지원하며 모든 배포에 Memory가 있습니다. 각 턴은 대화 이벤트(단기 기억)로 기록되므로, Runtime 세션이 유휴 시간 초과나 최대 수명으로 끝나도 대화가 이어지고 세션 목록과 기록은 항상 동작합니다. 네임스페이스 ID는 정규화된 `[issuer, subject, trusted tenant]`의 SHA-256입니다. issuer(예: IdP 전환)나 `auth.tenantClaim`을 바꾸면 모든 쇼핑객의 actor ID가 바뀌므로 이전 대화와 기억 기록을 더 이상 찾을 수 없습니다. API와 Runtime은 같은 구현을 사용하며 클라이언트는 actor ID를 지정할 수 없습니다. 원시 이벤트는 `eventExpiryDays`(기본 90일, 7–365일)가 지나면 만료되며, 이는 파생 기억 보존과 별개입니다. 개인정보/삭제 절차는 [운영](operations.ko.md#memory와-개인-식별-정보)을 참고하세요.

`longTerm=true`는 장기 기억을 추가합니다. `[memory.strategies]`의 추출 전략을 실행하고 그 파생 기록을 각 턴에 검색합니다. 쇼핑객마다 파생 데이터를 보관하므로 기본값과 두 템플릿 모두 꺼져 있으며, Prod에서는 개인정보 검토를 거친 뒤에만 켜세요. 각 전략은 독립적이며 검색 네임스페이스 하나에 매핑됩니다. `semantic`은 사실, `summarization`은 세션별 요약, `userPreference`는 세션 간 선호도입니다. `longTerm=true`이면 최소 전략 하나가 활성화되어야 합니다. `longTerm`이나 전략을 켜고 끄면 Memory 리소스를 교체하지 않고 업데이트하므로 대화 기록이 유지됩니다. 생성 후 바꿀 수 없는 속성은 리소스 이름과 암호화 키뿐입니다. 끈 전략은 합성되는 추출/검색 설정에서 제거되지만, 코드는 기존 파생 기록을 삭제하지 않으며 원시 이벤트 만료도 파생 기록 삭제를 보장하지 않습니다. [운영](operations.ko.md#memory와-개인-식별-정보)의 삭제 검증 절차를 따르세요.

단기 기억에는 켜고 끄는 설정이 없습니다.

```toml
[memory]
longTerm = true
[memory.strategies]
semantic = true
summarization = false
userPreference = true
```

## Guardrail

기본적으로 에이전트 모델 호출에 Bedrock Guardrail을 적용합니다. 콘텐츠 필터는 성적 콘텐츠, 폭력, 혐오, 모욕, 부정행위, 프롬프트 공격을 다룹니다. 민감 정보 정책은 카드 번호, CVV와 유효 기간, PIN, 비밀번호, AWS 액세스 키와 시크릿 키, `YYMMDD-NNNNNNN` 형식의 주민등록번호를 입력과 출력에서 차단합니다(`packages/infra/components/guardrail.py`). `[guardrail] mode="ENFORCE"`는 일치하는 콘텐츠를 차단합니다. `mode="AUDIT"`는 필터를 유지해 일치 항목을 트레이스에 표시하지만 차단하지 않으므로 강제 적용 전에 영향을 측정할 수 있습니다. `enabled=false`는 Guardrail을 완전히 제거합니다. Prod는 `enabled=false`를 거부하며 `mode="ENFORCE"`가 필요합니다. `streamProcessing`은 응답이 Guardrail을 거쳐 스트리밍되는 방식을 정합니다. `"sync"`(기본값이자 Prod 템플릿의 값)는 응답을 쇼핑객에게 보내기 전에 모두 검사합니다. Dev에서 첫 텍스트까지 `async`는 1.56초, Guardrail 없이는 0.76초가 걸렸고, Converse 기본값처럼 대화 전체를 검사할 때는 `sync`가 2.61초, `async`가 1.71초였습니다([스트리밍과 지연 시간](../design-docs/streaming-and-latency.md#the-guardrails-stream-mode)). `"async"`(Dev 템플릿)는 바로 스트리밍하고 백그라운드에서 검사하므로 필터에 걸리는 응답이 스트림이 끊기기 전에 잠깐 보일 수 있고, 민감 정보 마스킹은 지원되지 않습니다([Bedrock 스트리밍 Guardrail](https://docs.aws.amazon.com/bedrock/latest/userguide/guardrails-streaming.html)). 함께 제공되는 필터는 마스킹하지 않고 차단합니다.

모델을 호출할 때마다 Guardrail은 대화 전체를 다시 검사하지 않고 쇼핑객의 최신 메시지와 생성 중인 응답만 검사합니다. 차단된 메시지는 쇼핑객의 것이든 Aria의 것이든 대화에서 짧은 안내 문구로 바뀌므로, 한 턴이 차단되어도 다음 턴은 영향을 받지 않습니다.

`screenToolResults`(기본값 켜짐)는 모델이 읽기 전에 각 Gateway 도구의 결과도 입력으로서 Guardrail에 통과시킵니다. Converse Guardrail은 도구 결과를 검사하지 않으며, 대상의 데이터에 지시문이 심겨 있을 수 있기 때문입니다. Guardrail이 차단했거나 검사할 수 없는 결과(이미지나 문서, 실패한 호출)는 모델에 오류로 전달되고 `tool_result_blocked` 또는 `tool_result_screen_failed`로 기록됩니다. 로컬 검색 결과는 검사하지 않습니다. 민감 정보 필터도 도구 결과에 적용되므로, 강제하기 전에 `AUDIT`에서 대상의 실제 데이터를 확인하세요. 날짜 같은 필드는 카드 유효 기간처럼, 짧은 코드는 PIN처럼 보일 수 있습니다.

시스템 프롬프트는 행동 지침을 추가하고, 도구 허용 목록, 크기/예산 검사, 백엔드 권한, Guardrail이 별도 제어를 제공합니다.

## 사용자 지정 헤더

`gateway.forwardHeaders`는 Runtime 헤더 노출과 에이전트/인터셉터의 `FORWARD_HEADERS`를 설정합니다. 자격 증명이 아닌 컨텍스트 이름만 허용하며, `Authorization`/`auth`와 대소문자와 관계없이 `x-aria-*` 이름은 허용하지 않습니다. 이 네임스페이스는 에이전트 자체의 것이고, 전달된 헤더에는 호출자가 보낸 값이 그대로 담기기 때문입니다. 인터셉터가 호출 트레이스로 설정하는 `x-correlation-id`도 허용하지 않습니다. 대상마다 헤더는 actor와 상관관계 헤더를 포함해 최대 10개이므로 `forwardHeaders`에는 최대 8개를 지정할 수 있습니다. 로더가 이런 이름을 거부하며, `FORWARD_HEADERS`에 이런 이름이 있으면 에이전트가 시작하지 않습니다. 로컬 도구는 `invocation_state`로 현재 값을 받고, 원격 MCP 사용자 지정 헤더는 세션 생성 시 고정됩니다. 에이전트의 Gateway 토큰은 요청마다 읽습니다.

기본 브라우저/API는 사용자 지정 헤더를 Runtime에 전달하지 않습니다. Runtime을 직접 호출하거나 해당 API 경로를 명시적으로 구현하고 테스트하세요. 헤더는 검증된 호출자의 데이터 범위를 넓힐 수 없습니다. [신원과 토큰](../design-docs/identity-and-tokens.md#custom-headers)을 참고하세요.
