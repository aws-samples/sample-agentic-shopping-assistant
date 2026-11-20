# Gateway 도구

[English](README.md)

이 패키지에는 AgentCore Gateway 뒤에서 실행되는 Lambda 함수가 있습니다. 상품 검색은 에이전트의 로컬 Strands 도구(`packages/agent/src/tools/product_search.py`)로 실행되며, 검색 코드, 공급자, 예제 카탈로그는 `packages/catalog`에, 카탈로그를 OpenSearch에 인덱싱하는 작업은 `packages/indexer`에 있습니다. 도구를 어디에 둘지는 [도구 위치](../../docs/design-docs/tool-placement.md), 로컬 도구 작성은 [로컬 도구 추가](../../docs/guides/add-a-local-tool.ko.md), API나 MCP 서버를 Gateway에 등록하는 방법은 [Gateway 등록](../../docs/guides/gateway-onboarding.ko.md), 신원과 토큰의 흐름은 [신원과 토큰](../../docs/design-docs/identity-and-tokens.md)을 참고하세요.

- `saved-items-lambda`는 쇼핑객의 ID가 필요한 도구 `get_saved_items`를 보여 주는 예제 Gateway Lambda 대상입니다. Gateway가 Lambda 클라이언트 컨텍스트로 전달하는 `x-aria-actor-id` 헤더에서 쇼핑객의 actor ID를 읽고, 올바른 헤더가 없으면 호출을 거부합니다. `packages/catalog`의 예제 카탈로그에서 쇼핑객마다 일정한 `product.v1` 표본을 골라, 예제 카탈로그 정의로 카드에 매핑해 반환합니다. 실제 구현은 actor ID 범위로 쇼핑객 자신의 데이터를 읽습니다. 결과 객체를 직접 반환하고 Gateway가 MCP 콘텐츠 봉투를 생성합니다. API Gateway의 `statusCode/body` 응답을 반환하면 안 됩니다. AgentCore는 오류를 감싼 경우에도 그 래퍼를 일반 도구 콘텐츠로 취급합니다.
- `gateway-interceptor`는 표준 라이브러리만 사용하는 REQUEST 인터셉터입니다. 에이전트는 쇼핑객의 로그인 토큰을 전달하지 않습니다. 에이전트 자신을 위해 발급한 토큰으로 Gateway를 호출하고 `x-aria-actor-id`를 보냅니다. 인터셉터는 모든 도구 호출에 올바른 형식의 actor ID를 요구하고 대상에 전달합니다. on-behalf-of 토큰이면 헤더가 토큰 자체의 주체와 일치하는지도 확인합니다. `Authorization`은 절대 전달하지 않습니다. 검사에 실패하면 호출을 거부합니다.

저장소 루트에서 단위 테스트를 실행합니다:

```sh
(cd packages/mcp-servers/saved-items-lambda && uv run --frozen pytest)
(cd packages/mcp-servers/gateway-interceptor && uv run --frozen pytest)
(cd packages/catalog && uv run --frozen pytest)
```

`pnpm check`는 다른 모든 테스트와 함께 이를 실행합니다. [통합 구성 방법](../../docs/guides/integrations.ko.md)과 [카탈로그 런북](../../docs/guides/operations.ko.md#카탈로그-런북)을 참고하세요.
