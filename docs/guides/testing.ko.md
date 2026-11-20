# 테스트

[English](testing.md)

테스트는 코드를 직접 호출합니다. 에이전트의 검색 도구는 Strands 자체 호출 경로(`tool.stream(tool_use, {})`)로, MCP 클라이언트는 프로세스 안의 가짜 Gateway(`packages/agent/tests/unit/fake_gateway.py`, `httpx.MockTransport` 기반)를 상대로, 전체 턴은 스크립트 모델을 쓰는 실제 Strands 에이전트로 실행합니다. API는 가짜 Runtime을 둔 FastAPI `TestClient`에서 실행하고, Gateway Lambda는 합성 이벤트와 컨텍스트로 `lambda_handler(event, context)`를 호출하며, CDK 스택은 오프라인 합성으로 테스트합니다. 어떤 테스트도 실행 중인 MCP 서버, 모델, 실제 OpenSearch 도메인, AWS 계정이 필요하지 않습니다. 저장소 루트의 `conftest.py`는 테스트 대역을 벗어난 AWS 요청을 실패시키며, 각 Python 패키지가 테스트 루트에 똑같은 사본을 두므로 pytest를 `pnpm check`로 실행하든 패키지 안에서 실행하든 모든 테스트에 이 보호 장치가 적용됩니다.

## 실행 명령

저장소 루트에서 전체 검사를 실행합니다:

```sh
pnpm check
```

`scripts/check.py`를 실행하며, 이 스크립트는 가짜 AWS 자격 증명으로 `contracts`, `api`, `agent`, `catalog`, `indexer`, `saved-items-lambda`, `gateway-interceptor`, `infra`를 동기화하고 테스트한 뒤 scripts 테스트(문서 검사기, 이미지 빌드용 컨테이너 엔진, 릴리스 태그, AWS 보호 장치 사본, 사전 점검의 영역/모델/Nori 검사), 문서 검사(`scripts/check_docs.py`), 웹 테스트, 운영 웹 빌드를 실행합니다.

선택 실행 시 디렉터리 변경이 누적되지 않도록 패키지마다 서브셸을 사용합니다:

```sh
(cd packages/contracts && uv run --frozen pytest)
(cd packages/api && uv run --frozen pytest tests/test_events.py tests/test_auth.py)
(cd packages/agent && uv run --frozen pytest tests/unit/test_stream_contract.py tests/unit/test_transport.py)
(cd packages/catalog && uv run --frozen pytest)
(cd packages/indexer && uv run --frozen pytest)
(cd packages/mcp-servers/saved-items-lambda && uv run --frozen pytest)
(cd packages/mcp-servers/gateway-interceptor && uv run --frozen pytest)
(cd packages/infra && uv run --frozen pytest tests/test_gateway_identity.py tests/test_operations_stack.py)
python3 -m unittest discover -s scripts/tests
pnpm --filter nordly-web test
```

`python3 scripts/check_docs.py --skip-generated`는 `docs/generated/` 최신 여부 검사를 빼고 문서 검사를 실행합니다.

각 패키지의 `pyproject.toml`은 `testpaths = ["tests"]`를 지정하며, 에이전트 테스트는 `tests/unit/`에 있습니다. 두 Lambda, API, 에이전트는 `pythonpath = [".", "src"]`를 추가하므로 테스트에서 `handler`, `main`, `app`을 직접 임포트하며, `contracts`, 카탈로그, 인덱서는 `pythonpath = ["src"]`, infra는 `["."]`를 사용합니다.

각 패키지의 AWS 보호 장치 사본은 루트 `conftest.py`와 똑같은 `tests/conftest.py`입니다. 보호 장치를 바꾸려면 루트 파일을 수정한 뒤 모든 패키지에 복사합니다. 테스트가 있는 새 Python 패키지는 `testpaths`의 각 디렉터리에 사본을 두고 `scripts/check.py`의 `PACKAGES`에 추가해야 합니다. `scripts/tests/test_aws_guard.py`는 사본이 없거나 루트와 다를 때, 테스트 파일이 `testpaths` 밖에 있거나 자체 `block_aws_network`를 정의할 때, 테스트가 있는 패키지가 `PACKAGES`와 맞지 않을 때 실패합니다. 인터셉터처럼 botocore가 없는 패키지에서는 보호 장치가 아무것도 하지 않습니다. `packages/agent/tests/unit/test_aws_guard.py`가 그 동작을 보여 줍니다. 가짜 자격 증명을 쓴 botocore 호출은 요청을 보내기 전에 테스트를 실패시킵니다.

## 검색 검증 범위

| 테스트 | 검증 내용 |
| --- | --- |
| catalog `test_definition.py` | 카탈로그 정의. 규칙을 어기면 필드와 규칙을 밝힙니다: 알 수 없는 키, 타입, 필터와 그 값, boost, 매개변수 이름, 임베딩 템플릿과 모델, 카드. 필터 12개를 허용하고, 예제를 불러와 `CATALOG_DEFINITION`의 압축 JSON으로 왕복할 수 있으며, 잘못된 JSON이나 TOML은 `DefinitionError`입니다. 예제의 도구 스펙, 인덱스 본문, 파이프라인은 스냅숏과 일치하고, 범위/숫자/불리언 필터는 각자의 형태를 갖습니다. 인자는 필드 이름으로 정규화됩니다: 대소문자를 무시한 enum, 텍스트나 float로 온 정수, 범위, 기본 3이고 20으로 제한되는 `limit`, 조건이 아닌 빈 값. 모든 실수는 짧은 메시지의 `CatalogQueryError`입니다. `search_body()`는 키워드, 필터 전용, 하이브리드 본문을 만들며, `knn` 부분에 같은 필터를 걸고 한 페이지를 덮는 `pagination_depth`를 씁니다. 카드, 임베딩 텍스트, `check_product()`(문제는 값이 아니라 필드를 밝힘)도 확인합니다. |
| catalog `test_example.py` | 예제가 패키지 안에 있음: 고유 ID를 가진 약 150개 상품, `sample-1`~`sample-3` 다음 `nordly-` 번호, 모든 상품이 `check_product()`를 통과하고 모든 필드를 가짐, 가격 없음, 다양한 값, 정의 왕복 |
| catalog `test_contract.py` | 모든 공급자의 상품이 거치는 `product.v1` 검사: 모든 예제 카드는 그대로 통과하고 추가 필드는 제외, 숫자 ID와 정수 연도는 정규화, 필드 누락/잘못된 타입/빈 텍스트/2,000자를 넘는 텍스트/정수가 아닌 연도/객체가 아닌 값은 실패, ID는 웹이 인용할 수 있는 형식(`^[A-Za-z0-9._:-]{1,128}$`)일 때만 통과 |
| catalog `test_search.py`: `TestNothingToSearch` | 빈 값을 포함해 조건이 없으면 백엔드도 임베딩도 호출하지 않음, 빈 쿼리는 fixture를 나열하지 않음, 잘못된 인자는 백엔드 호출 전에 실패. 인자를 탓하는 오류는 `CatalogQueryError`뿐입니다. |
| `TestOpenSearch` | 쿼리는 `<alias>-hybrid`를 거치는 하이브리드 검색을 실행하며 별칭은 `OPENSEARCH_PRODUCT_INDEX`(기본 `products`)에서 옴, 필터만 있으면 임베딩 없이 키워드 검색, 임베딩이 실패하거나 형식이 잘못되면 키워드 검색으로 대체하고 값 없이 기록, hit은 카드로 매핑 |
| `TestDispatch`, `TestCards` | 기본 공급자는 fixture, fixture와 HTTP 공급자는 정규화된 인자를 받음, 알 수 없는 공급자는 백엔드 오류. 모든 공급자가 `product.v1`에 맞지 않는 상품을 제외하고 값 없이 개수만 기록, 올바른 상품이 하나도 없으면 빈 결과가 아닌 `CatalogBackendError`, 결과는 limit을 넘지 않음 |
| `TestRegion` | OpenSearch 요청은 `AWS_REGION`, 없으면 `AWS_DEFAULT_REGION` 리전으로 서명합니다. 리전이나 `OPENSEARCH_HOST`가 없으면 검색이 `CatalogBackendError`로 실패하고, 리전이 없으면 AWS 세션을 만들지 않습니다. |
| catalog `test_embedding.py` | Embed v4 요청(`texts`, `input_type`, float `embedding_types`, `output_dimension` 1024), 호출당 텍스트 96개와 순서를 지킨 벡터, 텍스트가 없으면 호출 없음, 일반 목록과 타입별 응답을 모두 허용하고 벡터 개수나 길이가 틀리면 `CatalogBackendError`, 알 수 없는 입력 유형은 호출 전에 거부, 클라이언트 오류는 그대로 전파, 쿼리 클라이언트는 연결 1초와 읽기 3초 제한 시간, 시도 한 번, 리전 필요 |
| catalog `test_providers.py`: `TestFixture` | 제목 단어는 설명 단어의 세 배 가중치, 동점은 카탈로그 순서 유지, 단어는 대소문자를 무시하고 한 번만 셈, 아무것도 맞지 않는 쿼리는 결과 없음, 필터와 범위는 모두 만족해야 함, 쿼리가 없으면 필터를 통과한 상품을 카탈로그 순서로 limit까지, fixture는 예제 정의만 받으며 `search_products()`를 거쳐 카드를 반환 |
| `TestHttpAdapter` | 네트워크를 대역으로 둠: 본문은 매개변수 이름을 키로 쓰는 평평한 객체이며 지정하지 않은 조건은 `null`, 범위는 `min`과 `max`(열린 쪽은 `null`), POST에 본문과 자격 증명 헤더를 싣고 리디렉션은 거부, 상품은 카탈로그가 보낸 그대로 돌아와 정의를 거쳐 카드로 매핑되며 카드 필드가 없는 상품은 전체를 실패시키지 않고 제외, `SEARCH_RESULT_PATH`를 따르고 상품 20개는 허용, JSON이 아니거나 너무 크거나 상품이 20개를 넘는 응답/상품 목록 없음/평문 HTTP 엔드포인트/오류 상태(자격 증명 재조회 없음)는 `ValueError`가 아닌 `CatalogBackendError`입니다. |
| `TestCredential` | 잘못된 시크릿은 아무것도 보내기 전에 실패, Secrets Manager 클라이언트는 환경의 리전을 사용하고 리전이 없으면 조회 전에 실패, 자격 증명은 TTL 동안 캐시, 401이나 403을 받으면 다시 조회해 요청을 한 번 재시도하고 또 거부되면 실패, 시크릿이 없으면 거부를 재시도하지 않음 |
| indexer `test_indexer.py` | 대역 OpenSearch, Bedrock, S3 대상: JSON 배열과 JSONL을 똑같이 읽음, 깨진 배열은 작업을 멈추고 깨진 JSONL 줄은 거부, 모든 예제 상품이 임베딩 텍스트와 함께 승인됨, 거부는 값이 아니라 필드별로 셈, 빈 임베딩 텍스트는 거부, 드라이 런은 클라이언트를 쓰지 않음, 카탈로그는 S3에서, 정의는 `CATALOG_DEFINITION`에서 읽으며 잘못된 정의는 읽기 전에 실패, 별칭과 버전 이름 검사, 빈 카탈로그는 실패. 적용하면 인덱스를 만들고 파이프라인을 넣고 별칭을 한 번의 호출로 옮기며, 모든 요청이 태스크 역할이 허용한 경로 안에 머뭅니다. 이미 있는 버전이나 별칭과 같은 이름의 실제 인덱스는 쓰기나 임베딩 전에 거부하고, 개수 불일치나 거부된 문서(오류 유형만)는 별칭을 그대로 둡니다. throttling은 마지막 시도까지 지수 백오프로 재시도하고 다른 Bedrock 오류는 재시도하지 않음, 오류는 메시지 없이 설명, `--apply`에는 도메인 호스트가 필요합니다. |
| agent `test_product_search_tool.py` | Strands를 통한 호출: 정의(`CATALOG_DEFINITION`, 비어 있으면 예제)에서 만든 스펙, 선언된 모든 값을 enum으로 나열, 잘못된 정의는 도구를 만들 때 실패, JSON 텍스트 형태의 `product.v1` 봉투와 이스케이프되지 않은 한글(Strands 에이전트를 거쳐서도), 필터만으로도 검색, limit 제한, 조건이나 입력이 없거나 쿼리가 비어 있을 때 빈 결과, 잘못된 값/알 수 없는 매개변수/너무 긴 쿼리/`limit=0`/숫자가 아닌 연도는 수정 가능한 오류, 예상 밖 실패와 백엔드의 `ValueError`는 세부 정보 없이 "Product search failed", 도구를 거친 하이브리드 검색/기록되는 임베딩 대체 실행/제외되는 상품, 리전 누락은 백엔드 장애. 로그에는 공급자, 개수, 소요 시간만 남고 자격 증명, 엔드포인트, 인자, 상품 값은 남지 않습니다. |
| `TestHttpCatalog` | 대역 HTTPS 카탈로그 대상: 정규화된 값과, 범위가 있는 정의의 범위 전달, 카탈로그 장애(잘못되거나 너무 큰 응답, 너무 많은 상품, 상품 목록 없음, 오류 상태, 평문 HTTP 엔드포인트, 지원하지 않는 자격 증명 헤더)와 올바른 상품이 없는 응답은 인자 오류가 아닌 백엔드 장애로 실패, 자격 증명은 재사용하고 401이면 한 번 다시 조회하며 또 거부되면 실패 |

## 에이전트 안전성과 설정 검증 범위

| 테스트 | 검증 내용 |
| --- | --- |
| agent `test_safety.py`: `TestBefore`, `TestAfter` | `ToolSafety`가 허용 목록 밖의 도구, 예산을 넘은 호출, 크기 초과 인자를 취소하고 각각 인자 없이 도구와 사유만 담아 `tool_denied`로 기록, 다음 호출의 예산 초기화, 크기 제한을 넘은 결과는 오류로 대체하고 기록 |
| `TestScreening` | 가드레일이 있으면 Gateway 도구의 텍스트와 JSON 출력을 `ApplyGuardrail`에 `INPUT`으로 보내 검사하고, 가드레일이 개입하거나 검사가 실패하거나 가드레일이 읽을 수 없는 콘텐츠면 결과를 차단합니다. 로컬 도구, 거부된 호출, 빈 결과는 검사하지 않으며, 검사 클라이언트는 제한 시간과 재시도 횟수가 정해져 있습니다. |
| `TestInTheAgentLoop` | 스크립트 모델을 쓰는 실제 Strands 에이전트: 예산이 소진되면 모델이 답할 기회를 한 번 더 받고, 그래도 도구를 요청하면 고정 응답(`BUDGET_TURN_END`)으로 턴 종료, 턴마다 새 예산, 차단된 Gateway 결과는 모델에 도달하지 않음 |
| agent `test_builder.py`: `TestSessionBuild`, `TestLocalTools` | 에이전트 상태의 actor ID, Gateway 없이 동작하는 로컬 검색과 검색을 끄면 도구 없음, Gateway 도구나 Gateway URL이 없으면 Gateway 연결 없음, 허용 목록에 없는 로컬 도구는 제공하지 않음, 형식화되지 않은 프롬프트 자리표시자 없음, Memory는 ID가 있으면 항상 사용하고 이벤트를 즉시 쓰며(`batch_size` 1) 선택한 전략의 네임스페이스만 조회 |
| `TestModel`, `TestHooks` | 가드레일과 스트림 처리 모드를 모델에 전달, 가드레일은 최신 메시지만 검사하고 차단된 메시지는 안내 문구로 대체하며 가드레일이 없으면 관련 옵션을 설정하지 않음, 프롬프트 캐싱은 기본적으로 꺼져 있고 켜면 시스템/도구/메시지에 적용, GPT-5에는 temperature를 보내지 않음, botocore는 연결 5초/읽기 60초 제한 시간으로 한 번 재시도, 에이전트는 스로틀링을 Strands 기본값(약 2분)이 아닌 몇 초 안에 재시도(3회 시도), 모든 세션이 끊긴 턴을 닫음, 도구 결과 검사는 가드레일과 함께 켰을 때만, 예산과 허용 목록은 설정에서 가져옴 |
| agent `test_config.py` | Gateway URL은 `GATEWAY_URL`이나 제한 시간이 있는 SSM 조회에서 가져오고, Gateway 도구가 없거나 Gateway를 끄면 조회하지 않으며, Gateway 도구가 있는데 둘 다 없으면 오류이고, AgentCore Gateway 호스트(`<id>.gateway.bedrock-agentcore.<region>.amazonaws.com`)의 HTTPS URL이 아니면 `execute-api`나 S3 같은 다른 `amazonaws.com` 호스트도 거부하고 기록합니다. 알 수 없는 `SEARCH_PROVIDER`/`GATEWAY_AUTH_FLOW`/`GUARDRAIL_STREAM_PROCESSING` 값, 일부만 설정한 가드레일, 대소문자와 관계없이 `Authorization`, `auth`, `x-aria-*` 헤더를 지정한 `FORWARD_HEADERS`는 실패 처리하며, Gateway 토큰 설정과 on-behalf-of 기본값, Claude에만 허용되는 프롬프트 캐싱, Memory/전달 헤더/검사 설정도 확인합니다. |
| agent `test_evals.py` | 데이터셋 평가 러너의 오프라인 검증: 예제/안전성 데이터셋, 시나리오마다 소유자에 묶인 Runtime 세션 하나, 에이전트 `error`나 빈 응답이면 시나리오 실패, 응답은 계약 이벤트에서만 읽음, span을 러너의 세션으로 다시 표시하고 Strands 도구 span만 평가 대상, 실패한 시나리오/평가기 오류/`--min` 미만 평균이면 실행 실패, `--invoke` 필수, HTTPS만 허용, PKCE 로그인은 루프백 콜백만 받고 소유자만 읽을 수 있는 토큰 파일을 씀 |

## Gateway 토큰과 전송 검증 범위

| 테스트 | 검증 내용 |
| --- | --- |
| agent `test_downstream.py` | 워크로드 토큰과 설정한 흐름으로 발급(Cognito가 요구하는 액세스 토큰 subject 유형 포함), 대상(audience) 없는 M2M, 만료 직전까지 재사용, 워크로드 토큰 없으면 거부, 액세스 토큰이 없으면 실패 처리, 잘못된 흐름/설정 거부, 불투명 토큰의 짧은 수명, 제한 시간과 재시도 횟수가 정해진 ID 클라이언트 |
| agent `test_transport.py`: `TestSessionSurvivesGatewayErrors` | 가짜 Gateway를 상대로 한 Strands의 실제 `MCPClient`: HTTP 오류, 토큰 발급 실패, 시간 초과, 끊긴 연결은 해당 도구 호출만 실패시키고 다음 호출은 성공, 토큰 없는 호출은 Gateway에 도달하지 않음, 알림의 HTTP 오류로 세션이 닫히지 않음, 인터셉터의 거부(HTTP 200의 JSON-RPC 오류)는 거부로 모델에 전달, 모든 요청에 새로 발급한 토큰과 사용자 지정 헤더 포함 |
| `TestGatewayTransport` | 요청마다 토큰 소스를 조회하고 토큰은 Gateway에만 전송, 실패한 요청은 해당 ID의 JSON-RPC 오류로, 실패한 알림은 수신 확인으로 바꿈, 그 밖의 요청은 상태 코드 유지 |
| agent `test_builder.py`: `TestGatewayTransport` | 사용자 지정 헤더 유무와 관계없이 `x-aria-actor-id`가 Gateway에 도달하고 전달 헤더로 예약된 헤더(대소문자와 관계없이 `x-aria-actor-id`, `Authorization`, `auth`, `x-aria-tenant`)를 설정할 수 없음, MCP 클라이언트는 10초 시작 제한 시간과 허용 목록 도구 필터 사용, 가짜 Gateway에 연결하는 실제 세션 빌드에서 모든 요청이 쇼핑객 JWT가 아닌 발급된 토큰을 쓰고 발급은 한 번, 자격 증명 공급자 없는 Gateway 도구는 실패 처리 |
| `TestRefreshIdentity` | 요청마다 워크로드 토큰을 토큰 소스에 전달, Gateway 도구가 없는 세션에는 토큰 소스 없음 |
| agent `test_forward_headers.py` | 엔트리포인트를 통한 호출: 허용 목록의 헤더는 대소문자 구분 없이 비교해 세션 빌드(Gateway 경로)와 호출 상태(로컬 도구)로 전달하고 나머지는 제거, Gateway 토큰은 Runtime의 워크로드 토큰으로 발급 |

## 턴 계약 검증 범위

에이전트는 `aria_contracts.events`(`packages/contracts`)에 정의된 이벤트를 스트리밍하고, API는 이를 브라우저로 중계합니다([스트리밍과 지연 시간](../design-docs/streaming-and-latency.md)). 구간마다 자체 테스트가 있고, `test_stream_contract.py`는 실제 턴 하나를 두 구간에 걸쳐 실행합니다.

| 테스트 | 검증 내용 |
| --- | --- |
| contracts `test_events.py` | 웹과 CloudWatch 지표 필터가 읽으므로 오류 코드와 재시도 힌트는 고정되어 있고, 알 수 없는 코드는 버그이며, 생성자는 버전이 붙은 봉투를 만듭니다. 도구 결과는 `product.v1`일 때만 콘텐츠를 스키마의 필드로만 다시 만든 압축된 텍스트 블록 하나로 유지하고, 실패한 결과는 아무것도 표시하지 않습니다. `decode`는 모든 이벤트를 그대로 왕복시키고, 계약 밖 필드는 제거하며, 알 수 없는 코드와 오류 형태의 객체는 원문 없이 `internal`로 바꾸고, 그 밖의 것은 버립니다. |
| agent `test_client_stream.py` | `for_client`는 계약 이벤트만 보냅니다. 텍스트 조각은 한 번씩, 도구 호출은 ID와 이름만, 도구 결과의 콘텐츠는 `product.v1`일 때만, `done`은 중단 사유와 함께 보내며, 가드레일의 대체 문구는 sync 모드에서만 붙이며, 도구 예산 안내는 `done` 앞에 텍스트로 보냅니다. 추론, 도구 입력, 호출 상태, 그 밖의 모든 Strands 이벤트는 컨테이너 밖으로 나가지 않고, 에이전트 자신의 메시지는 바뀌지 않습니다. |
| agent `test_entrypoint.py` | 가짜 빌더와 세션을 사용합니다. 턴은 계약 이벤트를 스트리밍하고 해당 턴의 사용량으로 결과 로그를 하나 남기며, 메시지 내용이나 토큰은 기록하지 않습니다. 잘못된 프롬프트는 아무것도 만들기 전에 `invalid_request`, 세션은 작업자 스레드에서 한 번만 빌드하고 빌드가 실패하면 `init_failed`이며 다음 요청에서 다시 빌드, API 제한 시간보다 10초 짧은 마감 시간은 빌드와 턴을 모두 포함하고 에이전트를 해제, 스로틀링은 `throttled`, 제때 에이전트를 얻지 못한 동시 요청은 턴으로 세지 않는 `busy`, 턴 예산 초과는 `turn_limit`, 그 밖의 오류는 트레이스백을 로그에만 남기는 `internal`입니다. `max_tokens`는 부분 답변을 유지하고, `done` 뒤에는 아무것도 보내지 않으며, 결과 없이 끝난 스트림도 오류로 끝나고, 호출자가 떠나면 에이전트 스트림을 닫으며, 모든 로그 줄에 세션 ID가 있습니다. |
| agent `test_stream_contract.py` | 스크립트 모델을 쓰는 실제 Strands 턴 하나가 `for_client`, 엔트리포인트, AgentCore SDK의 SSE 직렬화를 거쳐 API의 디코더와 중계기를 통과합니다. 브라우저는 예상한 프레임만 정확히 받고 텍스트 토큰은 한 번씩 받으며, 시스템 프롬프트/프롬프트/도구 입력/전달 헤더/추론/호출 상태는 전송되지 않고 턴은 2KB 미만이며, 결과 로그에는 해당 턴의 토큰, 도구 호출, 모델, 프롬프트 해시가 남습니다. 턴 진행 중 요청은 그 턴을 대체합니다. 이전 턴은 `cancelled`로 끝나고 새 턴이 실행되며, 제때 멈추지 못한 턴이 있으면 새 요청은 `busy`이고, 마감 시간에 끊긴 턴은 에이전트를 해제합니다. |
| agent `test_interrupted_turns.py` | 답변 도중이나 도구 결과 뒤에 마감 시간으로 끊긴 턴은 다음 턴 전에 짧은 어시스턴트 응답으로 닫아 모델이 사용자 메시지를 연달아 받지 않게 함, 완료된 턴은 그대로 둠 |
| api `test_events.py` | 중계: 에이전트 이벤트는 그대로 브라우저에 도달하고, 도구 프레임에서는 입력과 표시할 수 없는 출력을 제거하며, 오류 형태의 객체와 알 수 없는 코드는 원문 없이 `internal`로 바꾸고, 원시 Strands 이벤트를 포함해 잘못된 데이터는 버립니다. 종료 프레임 뒤에는 아무것도 보내지 않고, 결과 없이 끝난 스트림은 `upstream`으로 끝내며, API 자체 실패는 한 번만 보냅니다. 프레임은 한 줄이고 한글을 이스케이프하지 않으며, 디코더는 어디서 나뉜 바이트든 처리하고 너무 큰 프레임은 거부합니다. |
| api `test_api.py`: 호출 | 가짜 Runtime을 둔 FastAPI `TestClient`: Runtime에는 토큰, 세션, 프롬프트만 전달, 호출마다 상태와 첫 바이트까지의 시간을 담은 `invocation_finished`, 오류 프레임마다 `agent_error` 기록, 실패한 Runtime 호출은 `upstream`이나 `deadline` 오류, 답변 뒤의 실패는 두 번째 종료 프레임을 추가하지 않음, 스트리밍하지 않는 응답은 텍스트와 중단 사유를 담고 가드레일의 대체 문구만 반환하며 개입 전에 스트리밍된 텍스트는 담지 않음, 실패한 턴은 오류 코드에 맞는 HTTP 상태와 코드 본문으로 반환하고 내부 세부 정보는 담지 않음, 브라우저가 떠나면 Runtime 스트림을 닫음, 모든 로그 줄에 세션 ID 포함 |
| api `test_runtime_client.py` | bearer 인증, 이스케이프한 Runtime ARN과 세션 헤더, 실패한 호출은 재시도하지 않음, 마감 시간은 Runtime을 기다리는 각 대기에 적용하되 청크 사이의 호출자는 취소하지 않음 |

## 신원, 세션, API 검증 범위

| 테스트 | 검증 내용 |
| --- | --- |
| contracts `test_identity.py` | `actor_id`의 고정 기준값을 확인합니다. actor ID는 발급자, 주체, 테넌트를 정규화한 JSON 배열의 SHA-256이므로 필드 경계가 섞이지 않고, 다른 클레임은 영향을 주지 않으며, 테넌트는 설정했을 때만 포함되고 그때는 필수이며, HTTPS 발급자와 주체가 필요합니다. 세션 ID는 97자이고 무작위이며 소유자에 묶이고, 형식이 잘못되었거나 소유자에 묶이지 않은 ID는 실패 처리합니다. `runtime_actor`는 예상한 발급자만 허용합니다. |
| agent `test_builder.py`: `TestResolveActorId` | `Bearer` 접두사 유무와 관계없이 토큰의 발급자와 주체로 actor ID 계산, 주체 없는 토큰은 actor를 지어내지 않고 거부 |
| agent `test_runtime_ownership.py` | 세션이 다른 사람의 것이거나, 호출자가 검증되지 않았거나, 컨테이너가 이미 다른 쇼핑객을 처리 중이면 엔트리포인트가 에이전트 빌드나 Memory 읽기 전에 `owner_mismatch`로 응답 |
| api `test_auth.py` | 실제 RSA 키로 액세스 토큰을 검증합니다. 잘못된 발급자, 만료된 토큰, 다른 클라이언트, ID 토큰, 범위 누락, 빈 주체, 위조 서명은 거부하고, BYO 발급자는 끝의 슬래시까지 정확히 유지하며, 알 수 없는 키 ID면 JWKS를 다시 가져오고 그래도 모르는 ID는 401입니다. JWKS 엔드포인트에 연결할 수 없거나 ID 공급자의 discovery가 올바르지 않으면 401이 아닌 503입니다. |
| api `test_api.py`: 세션 | 다른 쇼핑객의 세션은 호출, 기록 조회, 삭제에서 403이고 Runtime을 호출하지 않음, 세션 생성에는 호출자가 필요하며 그 호출자에 묶인 ID를 반환, 세션 목록은 각 세션을 `session_id` 하나로 생성 시각과 함께 나열, 세션 기록에 쓰는 Memory ID는 `AGENTCORE_MEMORY_ID`에서 읽음, 8,000자를 넘는 프롬프트는 422 |
| api `test_sessions.py`, `test_ownership.py` | Memory 기록은 여러 페이지에 걸쳐 읽어 정렬하고, 삭제 실패는 그대로 전파하며, Memory ID가 없으면 AWS 클라이언트를 만들지 않고, Memory 클라이언트는 제한 시간과 재시도 횟수가 정해져 있으며, 세션 목록은 생성 시각 기준 최신순이고 호출자에 묶이지 않은 ID는 제외합니다. 소유권은 상태 없이 발급자 범위로 판단합니다. |
| api `test_server.py` | uvicorn은 로드 밸런서의 유휴 제한 시간(150초)보다 오래 유휴 연결을 유지하고, ECS가 SIGTERM 뒤에 허용하는 30초 안에 연결을 정리 |

## Saved-items 검증 범위

`saved-items-lambda/tests/test_handler.py`는 핸들러가 다음을 지키는지 확인합니다:

- 예제 정의를 거쳐 예제 카탈로그 전체에서 고른 `product.v1` 카드를 쇼핑객에게 반환합니다.
- 헤더 대소문자와 관계없이 쇼핑객마다 일정한 예제를 반환하고, 쇼핑객마다 다른 예제를 actor ID에 따른 개수로 반환합니다.
- 표준 라이브러리와 `aria_catalog`만 필요합니다.
- actor ID가 없거나, 비어 있거나, 형식이 잘못되었거나, 다른 헤더 이름으로 전달되면 `PermissionError`를 발생시킵니다.
- `limit`을 따르고 상한을 적용합니다.
- 알 수 없는 도구와 인자를 거부합니다.
- `x-correlation-id`의 에이전트 트레이스 ID를 로그에 남기며, 형식이 올바른 값만 남깁니다.

## 인터셉터 검증 범위

`gateway-interceptor/tests/test_handler.py`:

- `TestInterceptorContract`: 출력 버전 `1.0`, 본문 유지. 본문이 없는 이벤트나 단일 JSON-RPC 객체가 아닌 본문(문자열, 숫자, 배치)은 거부. 배치로는 도구 호출이 호출별 신원 검사를 건너뛸 수 있음.
- `TestMethods`: `initialize`, `ping`, `tools/list`, 알림은 actor 없이 통과, 그 밖의 메서드(`resources/read`, `prompts/get`, `sampling/createMessage` 등)는 method-not-found JSON-RPC 오류, 메서드가 없는 메시지는 예외 발생.
- `TestSettings`: `GATEWAY_AUTH_FLOW`나 `AGENT_CLIENT_ID`가 없거나 흐름을 알 수 없으면 모든 요청 거부, 에이전트가 쓰는 흐름 이름(`ON_BEHALF_OF_TOKEN_EXCHANGE`, `M2M`)도 허용.
- `TestActorIdentity`: 도구 호출 시 actor가 대상에 도달, 형식이 올바른 actor ID가 없는 도구 호출 거부, 값이 서로 다른 대소문자 변형 `x-aria-actor-id` 헤더도 거부, `tools/list`에는 actor 불필요, `aria_contracts.identity.actor_id`로 계산한 기준값과 계산 결과 일치.
- `TestOnBehalfOf`: 토큰의 `act.sub`는 에이전트 클라이언트여야 하고 헤더는 테넌트 클레임을 포함해 토큰의 쇼핑객을 나타내야 함, `act`가 없거나 다른 클라이언트의 토큰, M2M 토큰, 토큰 없음은 거부.
- `TestClientCredentials`: M2M 토큰의 `client_id`는 에이전트 클라이언트여야 하며 주체는 헤더와 비교하지 않음, 다른 클라이언트의 토큰, `client_id` 없는 토큰, on-behalf-of 토큰, 토큰 없음은 거부.
- `TestRefusals`와 위의 거부 사례: 거부한 요청에는 그 요청 id의 JSON-RPC 오류(검사 실패는 `-32003`)로 답하고 대상에 전달하지 않으며, 사유는 `InterceptorDenials`가 세는 로그 줄에만 남김, 문자열 id는 그대로 돌려줌, 알림이나 불리언 id는 답할 대상이 없어 예외 발생, 예기치 않은 실패도 예외 발생, 로그에는 헤더 이름만 남고 값은 남지 않음.
- `TestHeaderForwarding`: 허용 목록에 있어도 `Authorization`은 전달하지 않음, 호출한 트레이스의 ID를 `traceparent`, 없으면 `x-amzn-trace-id`에서 가져와 `x-correlation-id`로 추가하며 사용자 지정 전달로 바꿀 수 없음, 허용 목록의 사용자 지정 헤더는 대소문자 구분 없이 비교하고 원래 표기를 유지해 전달, 그 밖의 헤더는 제거, 사용자 지정 허용 목록으로 검사한 actor를 바꿀 수 없음.

## 인프라 검증 범위

| 테스트 | 검증 내용 |
| --- | --- |
| `test_gateway_identity.py` | 인가자는 도구 대상(audience)의 on-behalf-of 토큰만, M2M이면 에이전트 클라이언트만 허용합니다. Runtime은 토큰 발급 방법을 전달받고, 자신의 자격 증명 공급자로만 발급하고 그 공급자의 시크릿만 읽을 수 있으며, Gateway 도구가 없으면 ID 권한을 받지 않습니다. 인터셉터는 흐름, 에이전트 클라이언트 ID(M2M 인가자가 허용하는 클라이언트), 테넌트 클레임을 받습니다. Gateway 역할은 해당 스테이지의 Gateway만 맡을 수 있습니다. BYO IdP는 자격 증명 공급자와 에이전트 클라이언트를 지정해야 하며, Gateway 대상(audience)과 M2M 에이전트 클라이언트는 쇼핑객의 것과 같을 수 없습니다. |
| `test_gateway_stack.py` | CUSTOM_JWT와 MCP, 인터셉터에 요청 헤더 전달, 안정적인 권한 ID, SSM의 Gateway URL. saved-items 예제는 VPC 밖의 ARM64에서 실행되고 `get_saved_items`를 공개하며, 인터셉터의 제한 시간은 짧습니다. |
| `test_customer_targets.py` | OpenAPI 대상(OAuth를 사용하는 인라인 문서, 자격 증명과 그 시크릿, Gateway 워크로드 토큰에 필요한 권한만 부여, 해당 객체로 제한된 S3 문서 접근, API 키 위치, 전파 헤더를 선언한 문서, `AUTHORIZATION_CODE` grant를 포함한 잘못된 설정)과 API Gateway 대상(해당 스테이지로 제한된 IAM 서명 호출, API 키 대안, 잘못된 설정). 모든 대상은 헤더 제한 안에서 `x-aria-actor-id`와 전달 헤더를 받을 수 있습니다. |
| `test_catalog_search.py` | 로더의 `catalogFile`: 설정 파일 기준 상대 경로, 비어 있으면 예제, 존재하는 `.toml`만 허용, fixture와는 함께 쓸 수 없음, 잘못된 정의는 파일 이름과 함께 로드 시 실패. 알 수 없는 키는 거부하고 별칭은 인덱스 이름 규칙을 따라야 합니다. Runtime은 정의를 압축 JSON으로 받으며 사용자 정의도 마찬가지이고, Runtime 리소스가 받는 크기의 값으로 나뉘어 들어가며(예제는 두 개), 2,048자 제한을 넘는 환경 변수 값이나 값 8개에 담기지 않는 정의는 합성을 실패시킵니다. OpenSearch 검색은 임베딩 모델 호출과 검색만 할 수 있고 다른 공급자는 임베딩 권한을 받지 않습니다. 도메인은 OpenSearch 3.5를 실행합니다. Nori는 조회한 뒤 최소 권한으로 연결하고 모든 스테이지에서 연결을 보존하며, standard 분석기는 Nori가 필요 없습니다. 카탈로그 버킷은 비공개, TLS 전용, 버전 관리를 씁니다. 태스크 역할은 정확히 세 문장만 가지고, 태스크는 자체 그룹에서만 443으로 도메인에 접근하며 Runtime과 같은 정의로 인덱서를 실행하고, 스택은 스크립트가 읽는 출력을 가집니다. 공개 엔드포인트는 없습니다. |
| `test_runtime_search.py` | Fixture 검색에는 권한이 필요 없습니다. OpenSearch는 Runtime을 VPC에 연결하고, 인덱스의 `_search` 경로에 대한 POST/GET만 허용하며, 쓰기 권한이 없고, 도메인에는 Runtime만 접근하게 하며, 호스트와 인덱스를 전달합니다. HTTP 검색은 자신의 시크릿만 읽습니다. |
| `test_forward_headers.py` | Runtime/인터셉터 헤더 허용 목록, `gateway.forwardHeaders`의 `Authorization`과 대소문자와 관계없는 모든 `x-aria-*` 이름 거부 |
| `test_profiles.py` | 실제 Dev/Prod 프로필이 단독으로, 그리고 OpenSearch, HTTP 검색, MCP 대상, 외부 Gateway, 장기 기억과 평가를 더한 조합으로 cdk-nag 오류 없이 합성되며, 모든 프로필에 Memory가 있고, Runtime은 자체 보안 그룹과 VPC 프라이빗 서브넷을 쓰는 VPC 모드입니다. 와일드카드 리소스는 꼭 필요한 작업에만 쓰고, 각 AgentCore 리소스는 역할의 정책이 생성된 뒤에 만들어집니다. Prod는 HTTPS 리스너, 종료 보호, 보존되는 로그 그룹, 데이터 노드 3개와 전용 클러스터 관리자 노드 3개를 둔 OpenSearch 도메인을 갖추며, `LOG_ONLY` 정책, saved-items 예제, fixture 카탈로그, `allowPublicIngress` 없이 /8(IPv6는 /32)보다 넓은 허용 목록 항목을 거부하고, HTTP나 OpenSearch 카탈로그 또는 카탈로그 없음은 허용합니다. |
| `test_stacks.py` | Runtime 스택(자체 보안 그룹을 쓰는 VPC 모드, 헤더 허용 목록, JWT 인가자, 에이전트 코드와 일치하는 Memory 네임스페이스, 수명 주기, 가드레일, 이 계정의 AgentCore만 신뢰하는 역할, Runtime이 호출하는 작업으로 제한된 Memory와 로그 권한), Auth 스택(시크릿 없이 도구 범위를 가진 웹 클라이언트, 위임할 수 있는 기밀 에이전트 클라이언트, 관리형 로그인, 템플릿에 시크릿을 쓰지 않는 on-behalf-of 자격 증명 공급자, 관리자만 가입, 이메일로만 계정 복구, 모든 스테이지에서 기본값은 선택이며 `auth.mfa`를 따르는 MFA와 그 설정이 관리형 Cognito 전용인 것, Cognito가 허용하는 도메인 접두사), API 서비스(IAM 호출 권한 없는 bearer Runtime 호출, Memory 하나, 프라이빗 서브넷, 모델 접근 없음), VPC 엔드포인트, IPv6와 공개 수신 규칙 |
| `test_images.py` | 각 이미지의 빌드 컨텍스트에는 Dockerfile이 복사하는 패키지만 들어갑니다(에이전트: agent, catalog, contracts, API: api, contracts, 인덱서: indexer, catalog). 다른 패키지는 모두 빠지므로, 한 패키지를 바꿔도 그 패키지가 없는 이미지는 다시 빌드되거나 배포되지 않습니다 |
| `test_gating.py` | Gateway를 끄면 Gateway와 정책 엔진이 없고, 검색은 그대로 로컬에서 실행되며, 정책이나 예제 대상은 거부됩니다. Memory는 두 템플릿 모두 전략 없이 단기 전용으로 배포됩니다. 장기 기억은 켜지 않으면 꺼져 있고, 전략이 하나 이상 필요하며, 선택한 전략만 배포합니다. 이벤트 만료는 7–365일입니다. 가드레일: AUDIT는 아무것도 차단하지 않고, 도구 결과 검사는 기본으로 켜져 있으며 끌 수 있고 가드레일이 있어야 하며, Runtime은 `ApplyGuardrail`을 호출할 수 있고, 스트림 처리 모드가 Runtime에 전달되며(Dev는 async, Prod와 기본값은 sync, 알 수 없는 모드는 거부), Prod는 꺼졌거나 AUDIT인 가드레일을 거부하며 async로 스트리밍할 수 있습니다. Runtime 제한과 WAF 속도 제한은 설정에서 가져오고, 범위를 벗어난 값은 거부하며, WAF 경보는 그 제한값을 표시합니다. 외부 Gateway 엔드포인트는 AgentCore Gateway URL이어야 합니다. |
| `test_policy.py` | 기본적으로 꺼져 있습니다. 켜면 검증된 `ACTIVE` 정책이 엔진과 대상이 생긴 뒤에 만들어지고, `ENFORCE`로 설정하지 않는 한 `LOG_ONLY`로 연결되며, permit이 forbid보다 먼저 설치됩니다. 예제의 Cedar는 자리표시자가 채워져 있고, permit과 `limit`을 방어적으로 확인하는 forbid를 포함하며, 이름과 길이가 API 규칙에 맞고, 예제에만 적용됩니다. 고객 대상의 `policyFile`은 `sample/` 밖의 `components/policies/` 아래 `.cedar` 파일이어야 하며, 허용된 도구마다 한 번씩 배포되고, `ENFORCE`에서는 모든 대상에 필요합니다. 빈 예제 폴더는 거부합니다. |
| `test_guardrail_version.py` | 번호 버전은 콘텐츠 정책과 민감 정보 정책이 바뀌면 함께 바뀌고 롤백을 위해 보존, 적용 권한은 가드레일과 그 프로필로 제한, 콘텐츠 필터에 혐오와 부정행위 포함, 자격 증명/결제 정보/주민등록번호는 마스킹 없이 양방향 차단하되 번호 패턴은 바코드와 타임스탬프를 차단하지 않음, AUDIT는 차단 없이 보고만 함, 도구 결과도 검사하므로 거부 주제나 단어 목록 없음 |
| `test_operations_stack.py` | 경보는 Gateway 스택에서 가져오지 않고 Gateway 함수 이름을 직접 지정합니다. 지표 필터는 API와 에이전트가 기록하는 이벤트(`runtime invocation failed`, `agent_turn_failed`, `session_build_failed`, `tool_denied`)와 정확히 일치하고, 스로틀링, 재시도 가능한 실패, 예산 외의 거부는 필드로 골라내며, 각 이벤트는 에이전트나 API의 소스에서 직접 확인합니다. `InterceptorDenials` 필터는 인터셉터가 호출을 거부할 때 남기는 로그 줄을 세며, 인터셉터를 실행해 확인합니다. 로그 개수는 `Sum`으로 집계하고, 장애 경보는 고정된 임곗값으로 5분 주기 한 번 안에 울리며, 정상 API 작업이 부족하면 데이터가 없어도 경보가 울립니다. WAF 경보는 해당 스테이지의 속도 규칙을 감시하고, Prod의 모든 경보는 토픽으로 알리며, 대시보드에 장애 신호가 표시됩니다. |
| `test_evaluations_stack.py` | 온라인 평가는 `<runtime>.<endpoint>`의 Transaction Search span을 읽음, 기본 제공/사용자 지정 평가기를 1–10개 연결하고 사용자 지정 평가기는 결정적 심사 모델, 평가 척도, 올바른 수준, API 규칙에 맞는 이름, 해당 수준의 자리표시자만 사용, 실행 역할은 AgentCore만 신뢰하며 span 로그 그룹만 조회하고 결과 로그 그룹에만 쓸 수 있음, 결과는 30일간 보관하고 스택을 삭제해도 남음 |
| scripts `test_preflight.py` | Runtime의 영역: 관리형 VPC의 처음 사용 가능한 세 영역은 AgentCore가 지원하면 통과하고, 사용할 수 없는 영역은 CDK처럼 건너뛰며, 지원하지 않는 영역은 그 ID를 밝히며 실패합니다. V1 Runtime에 V2 전용 영역을 쓰거나 VPC를 지원하지 않는 리전도 실패합니다. 기존 VPC는 Runtime이 쓰는 프라이빗 서브넷만 CDK 조회와 같은 방식으로 분류해 세며, 그런 서브넷이 없으면 실패합니다. 표의 모든 항목은 해당 리전의 영역 ID이며 서울은 `apne2-az1`~`az3`을 지원합니다. 추론 프로필 뒤의 모델은 프로필을 거쳐 확인하고, 활성이 아닌 프로필이나 승인되지 않은 모델은 그 이름을 밝히며 실패합니다. 배포하는 엔진용 Nori 패키지를 찾고, 패키지가 없는 리전은 실패합니다. API 인증서는 발급되면 Amazon 발급이든 가져온 것이든 통과하고, 그렇지 않으면 상태를 밝히며 실패하며, 다른 리전의 인증서는 호출 없이 실패합니다. |
| scripts `test_gen_docs.py` | 도구 카탈로그는 모듈을 실행하지 않고 소스에서 로컬 도구의 `@tool` 인자를 읽습니다. 리터럴과 모듈 최상위에서 리터럴에 묶인 이름을 읽고, 나중의 대입이 우선하며, 호출, f-문자열, 알 수 없는 이름은 이유를 밝히며 실패합니다. 최상위에서 없는 패키지를 import하고 파일을 쓰는 도구 모듈도 아무것도 실행하지 않고 카탈로그에 올라갑니다 |

## 웹 검증 범위와 CI

`pnpm --filter nordly-web test`는 `packages/web/tests/`의 테스트를 Node 환경의 Vitest로 실행합니다. 테스트는 예제 카탈로그 첫 세 상품의 `product.v1` 카드인 `tests/fixtures/products.json`과 에이전트 프롬프트(`packages/agent/src/prompts/system.md`)를 사용하며, 테스트별 자세한 내용은 [FRONTEND.md](../FRONTEND.md#testing)에 있습니다.

| 테스트 | 검증 내용 |
| --- | --- |
| `contracts.test.ts` | 카탈로그 fixture를 `product.v1` 결과로 표시하고 올바른 상품만 유지하며, 그 밖의 도구 결과는 원시 형태든 아니든 아무것도 남기지 않아 알 수 없는 MCP 출력이 쇼핑객에게 보이지 않고, 도구 오류는 빈 상품 결과가 아닌 실패로 표시하며, 쓸 수 있는 상품이 없는 `product.v1` 결과는 실패로 처리하되 빈 검색은 실패로 보지 않습니다. SSE 디코더(바이트마다 나뉜 한글, 다른 버전 거부)와 스트림 이벤트(도구 결과 뒤의 텍스트는 새 메시지, 표시 텍스트에 도구 이름이나 입력 없음, `done`은 한 번, `max_tokens` 안내, 가드레일 대체 문구나 한국어 기본 문구, 코드와 함께 발생하는 오류 프레임, 형식이 잘못되었거나 알 수 없는 프레임)도 검사합니다. |
| `citations.test.ts` | 한 줄을 따로 차지한 인용은 카드 자리, 문장 안 인용은 상품 이름, 알 수 없는 ID 표시, 복원된 기록에서는 일반 텍스트, 모든 fixture ID와 프롬프트 예시, 스트리밍 중 완성되지 않은 인용 숨김, 카탈로그 문자열과 모델 마크업 이스케이프, 실패한 호출과 렌더러가 없는 도구 출력을 숨기고 검증된 검색 결과로만 인용을 해석하는 턴 구성 |
| `markdown.test.ts` | 인라인, 참조형, 중첩, 자동 링크, 맨 URL, `javascript:`, 원시 HTML 어느 경우에도 답변에 이미지, 링크, URL 속성이 없음, 코드는 이스케이프 유지, 표는 스크롤, 인용은 그대로 해석 |
| `csp.test.ts` | Content-Security-Policy: 기본적으로 앱과 `data:` 이미지만 허용, 운영자가 지정한 이미지 출처 추가, 모든 호스트를 허용하거나 지시어를 주입하는 출처 거부, 앱 셸 `<head>`의 첫 meta 태그 |
| `errors.test.ts` | 모든 스트림 오류 코드, HTTP 상태, 네트워크 실패에 대한 한국어 문구와 동작, 로그인이 만료되었거나 없으면 맥락과 관계없이 다시 로그인 안내, 예외 문구나 HTTP 본문을 보여 주지 않음, 로그인 콜백 실패 사유, 모든 `COPY` 문자열이 해요체이고 세션 대신 대화라고 말함 |
| `activity.test.ts` | 단계 표시: 대상 접두사 유무와 관계없는 알려진 도구, 내부 도구 이름은 표시하지 않음, 상품이 있는 결과 뒤에만 정리 중 표시, 실패한 검색 뒤에만 검색 조건 재확인 표시 |
| `chat.test.ts` | API와 인증을 모의 처리한 채팅 스토어: `done`까지 스트리밍, `done` 없이 끊긴 답변은 표시하고 다시 보내지 않음, 가드레일 대체 문구, `max_tokens` 안내, 도구 출력과 도구 이름 숨김, 오류 프레임과 HTTP/네트워크 실패의 한국어 문구, 로그인이 만료되면 메시지를 되돌려 대기열에 넣음, 재시도, 답변 시작 전후의 중지, 대체된 스트림은 조용히 버림, 공개 런타임 설정의 마감 시간(사용할 수 없는 값이면 130초), 대화 불러오기/전환/삭제/새로고침 후 다시 열기 |
| `session.test.ts` | 열린 대화는 같은 쇼핑객일 때만 새로고침 후에도 유지되고 지우거나 삭제하면 잊음, 앱이 쓰지 않은 저장값은 무시, 세션 ID가 없는 API 응답은 명확히 실패, 제목은 생성 시각, 없으면 오래된 순 번호, 잘못된 행은 제외 |
| `auth.test.ts` | Cognito 로그아웃은 `logout_uri`를 사용하고 메타데이터를 가져오지 못해도 자격 증명을 먼저 지움, BYO OIDC는 end-session 로그아웃, 관리형 로그인은 한국어, bearer에는 ID 토큰이 아닌 액세스 토큰, 요청한 유효 시간 전에 갱신하고 동시 호출에는 한 번만 갱신하며 갱신이 실패해도 유효한 토큰은 사용, 백그라운드 갱신 재시도, 만료되었거나 API가 거부한 토큰은 로그아웃이 아닌 만료로 표시 |

저장소에는 배포 파이프라인이 포함되지 않으며, `buildspec.yml`은 CodeBuild에서 이미지만 빌드합니다. 검증, 이미지 빌드/검사, 릴리스 태그, 저장된 계획, 보호된 수동 배포를 신뢰할 수 있는 runner 위의 자체 CI에 연결하세요. [릴리스](release.ko.md)를 참고하세요.

## 동작 변경 시

해당 카탈로그, 도구, 계약, 엔트리포인트, API, 핸들러, 인터셉터, 스택, 웹 테스트를 수정합니다. 알 수 없는 도구와 메서드, 공개되지 않은 인자, 누락되거나 일치하지 않는 actor와 토큰, 자격 증명, 소유권 거부 사례를 유지하세요. 로그 이벤트 이름과 오류 코드는 계약입니다. CloudWatch 지표 필터가 이벤트를 세고, 웹은 코드마다 한국어 문구를 보여 줍니다. 운영, contracts, 웹 테스트가 이를 고정하므로 함께 변경하세요. 실제 프로필/계약 테스트를 구현 상수만 반복하는 단언으로 대체하지 마세요. 로컬 테스트는 실제 IAM 강제, ID 공급자와의 토큰 교환, 모델 접근, 로그인, 배포, 복구를 입증하지 않습니다. 이는 별도로 승인된 인수 검사가 필요합니다. [배포 후 실제 환경 검사](#배포-후-실제-환경-검사)를 참고하세요.

## 배포 후 실제 환경 검사

단위 테스트와 합성 테스트로는 AgentCore가 설정을 받아들이는지, 역할 권한이 충분한지 알 수 없습니다. 스테이지에 배포할 때마다 다음을 확인하세요.

1. **대화.** `ARIA_API_URL`과 단기 `ARIA_ACCESS_TOKEN`을 설정한 뒤 `scripts/smoke.py --invoke`를 실행하면 기본 턴이 실행됩니다. 모델을 호출하고 Memory 이벤트를 쓰므로 승인이 필요합니다.
2. **브라우저에서:** 관리형 로그인으로 로그인한 뒤 상품을 물어봅니다(상품 카드가 나타나고 문장 안 인용에는 상품 이름이 표시됨). saved-items 예제를 배포한 경우(Dev 템플릿은 켜 두고 Prod는 거부함) "내가 저장한 상품 보여줘"라고 묻습니다(예제가 Gateway를 거쳐 답함).
3. **`opensearch` 카탈로그 검색:** 카탈로그를 인덱싱한 뒤([운영](operations.ko.md#카탈로그-인덱싱)) 확인합니다.
   - 태스크의 매니페스트가 예상한 모든 상품을 승인하고 `"promoted": true`를 보여 줍니다.
   - 상품 텍스트와 겹치는 단어가 없는 쿼리도 의미로 그 상품을 찾습니다.
   - 정의가 Nori를 쓰면 한국어 키워드 쿼리가 Nori를 거쳐 상품을 찾습니다.
   - 필터만 쓰는 검색과, 정의에 있으면 범위 필터가 일치하는 상품만 반환합니다.
   - 테스트 스테이지에서 Runtime 역할의 임베딩 모델 권한을 막아도 검색은 키워드로 답하고 Runtime은 `product_search_embedding_failed`를 기록합니다.
   - 새 버전으로 다시 인덱싱하면 별칭이 옮겨지고, 매니페스트의 `previousIndices`가 이전 인덱스를 알려 줍니다.
   - 쿼리가 있을 때와 없을 때의 `product_search_finished` `duration_ms`로 쿼리 임베딩 지연 시간을 잽니다.
4. **Gateway의 신원 규칙:** Gateway를 직접 호출합니다. Gateway 스택의 `GatewayUrl` 출력에서 URL을 가져온 뒤 실행합니다.
   ```sh
   curl -s -o /dev/null -w '%{http_code}\n' -X POST "$GATEWAY_URL" \
     -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
     -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
   ```
   - 토큰 없음: 401
   - 웹 앱 sessionStorage의 쇼핑객 액세스 토큰을 `Authorization: Bearer`로 보냄: 403
   - on-behalf-of 토큰: `tools/list` 동작. 에이전트 클라이언트 자격 증명을 파일에서 읽어 [Cognito 참고](../references/cognito-token-exchange-notes.md#the-request)의 교환 요청으로 발급합니다.
5. **각 고객 대상:**
   - 도구 목록에 나타남
   - `x-aria-actor-id`가 토큰 쇼핑객의 actor ID인 호출이 성공하고, 대상 로그에 자체 자격 증명과 actor ID가 보임
   - 헤더가 없는 호출과, on-behalf-of 토큰으로 다른 쇼핑객의 actor ID를 보낸 호출은 대상에 닿지 않고 거부됨. 응답은 HTTP 200의 JSON-RPC 오류(코드 `-32003`, access denied)이며 사유는 인터셉터 로그에 있음
6. **대상이 "An internal error occurred"로 답하면** `bedrock-agentcore.amazonaws.com`의 CloudTrail 이벤트를 조회합니다. `AccessDenied`가 Gateway 역할에 없는 리소스를 알려 줍니다([Gateway 참고](../references/agentcore-gateway-notes.md#outbound-credentials)).
7. **운영 대시보드의 경보가 조용한지** 확인합니다([RELIABILITY.md](../RELIABILITY.md)).

검사 중에 토큰이나 시크릿을 출력하지 마세요. 토큰이 아니라 클레임을 디코딩합니다.
