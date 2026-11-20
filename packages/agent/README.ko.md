# ARIA 에이전트

[English](README.md)

고정된 Strands 쇼핑 에이전트는 AgentCore Runtime에서 실행됩니다. Runtime이 컨테이너와 세션 수명을 관리합니다. 애플리케이션은 에이전트를 생성하거나 재사용하기 전, 그리고 Memory를 읽기 전에 인증 주체에 연결된 세션 ID를 확인합니다. 세션 데이터베이스는 없습니다.

설정은 CDK 환경 변수로 전달됩니다. Bedrock 호출은 Converse를 사용하며 읽기 제한 시간은 60초입니다. API와 Runtime은 요청 전체 제한 시간도 적용합니다. 프롬프트 캐싱은 기본적으로 꺼져 있고, Claude 이외의 모델에서 켜면 설정 오류가 발생합니다. 도구는 `ALLOWED_TOOLS`로 필터링하며, 훅은 호출당 도구 실행 8회와 인자·결과 크기 제한을 적용합니다. 재사용되는 세션은 기본적으로 최대 100턴을 허용합니다. 프롬프트는 검색된 데이터를 신뢰할 수 없는 정보로 취급하며 읽기 전용 쇼핑 지원을 제공합니다.

모든 배포가 `AGENTCORE_MEMORY_ID`를 설정하며, 에이전트는 각 턴을 `batch_size=1`로 AgentCore Memory에 기록합니다. 종료 훅에 저장을 의존하지 않습니다. 장기 기억이 켜져 있으면 관련 선호도와 사실도 각 턴에 검색해 넣습니다. `GatewayTransport`(`src/core/transport.py`)는 요청마다 에이전트 자신의 Gateway 토큰을 조회하므로, MCP 세션을 다시 연결하지 않아도 갱신된 토큰을 사용합니다.

```sh
uv sync --frozen
uv run --frozen pytest
```

저장소 루트에서 빌드합니다: `finch build -f packages/agent/Dockerfile -t aria-agent:local packages`. 공통 contracts 패키지도 빌드 컨텍스트에 포함됩니다. 시작 시 이미 설치된 가상 환경을 직접 실행하며 의존성을 설치하지 않습니다.

AgentCore가 내보내기 및 라우팅 설정을 관리합니다. `TRACE_CAPTURE_CONTENT=false`는 민감한 Strands 속성을 가리고 메시지 수집을 끕니다. 이 설정은 사이드카의 내보내기 엔드포인트를 바꾸지 않고 콘텐츠 정책만 제어합니다. 지속적 평가를 사용하려면 대화 트레이스 콘텐츠 수집을 명시적으로 허용해야 합니다.
