# 로컬 도구 추가

[English](add-a-local-tool.md)

1. **도구 작성:** `packages/agent/src/tools/`에 Strands `@tool` 함수로 작성합니다. `search_products`는 스키마를 카탈로그 정의에서 만들기 때문에 `AgentTool` 클래스일 뿐이니, 오류 처리 방식을 따르세요.
   - 모든 인자를 신뢰할 수 없는 값으로 다룹니다. 값을 검증하고 크기를 제한합니다. 검색은 `limit`를 20으로 제한합니다.
   - 인자가 잘못된 경우에만 `ValueError`나 카탈로그의 `CatalogQueryError` 같은 하위 클래스를 발생시키고, 허용된 값 목록처럼 모델이 바로 고칠 수 있는 메시지를 담습니다.
   - 그 밖의 실패는 모두 잡아서 인자 값 없이 `product_search_failed` 같은 이벤트로 기록하고, `RuntimeError("Product search failed")`처럼 고정된 메시지로 발생시킵니다. 그러면 모델이 성공할 수 없는 호출을 반복하지 않습니다.
   - 웹에 상품 카드를 표시하려면 `product.v1`을 반환합니다.
   - `@tool`의 `name`, `description`, `inputSchema`는 리터럴이나, 모듈 최상위에서 리터럴에 묶인 이름으로 지정합니다. `scripts/gen_docs.py`는 [도구 카탈로그](../generated/tool-catalog.md)를 만들 때 모듈을 실행하지 않고 소스에서 이 값을 읽습니다.
2. **등록:** `SessionBuilder._build_tools()`(`packages/agent/src/core/builder.py`)에 추가합니다. 등록했더라도 `ALLOWED_TOOLS`에 없는 도구는 모델에 제공되지 않으며, 세션이 `local_tool_not_allowed`를 기록합니다.
3. **허용:** `Config.local_tools`(`packages/infra/config/loader.py`)에 이름을 추가합니다. 이 목록이 `ALLOWED_TOOLS`가 됩니다. `ToolSafety`가 턴별 호출 예산(`runtime.maxToolCalls`), 8 KiB 인자 제한, 64 KiB 결과 제한과 함께 이를 강제하고, 거부할 때마다 `tool_denied`를 기록합니다.
4. **필요한 권한만 부여:** Runtime 역할(`packages/infra/stacks/agent_runtime_stack.py`)에 필요한 권한만 줍니다. `_grant_product_search()`를 참고하세요.
5. **테스트:** Strands의 호출 경로인 `tool.stream(tool_use, {})`로 테스트합니다. `packages/agent/tests/unit/test_product_search_tool.py`를 참고하세요.

로컬 도구는 Runtime 역할로 실행되며 쇼핑객의 토큰이 없습니다. 쇼핑객의 신원이나 사용자 지정 헤더가 필요하면 [신원과 토큰](../design-docs/identity-and-tokens.md#custom-headers)에 나온 방법으로 읽습니다. 다른 서비스에서 쇼핑객으로 동작해야 하는 도구는 Gateway 뒤에 둡니다.
