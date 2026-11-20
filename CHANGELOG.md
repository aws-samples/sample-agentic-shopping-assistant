# CHANGELOG / 변경 기록

Release notes for ARIA. Add an explicit `## [X.Y.Z]` heading to cut a release;
`[Unreleased]` alone never produces a tag. See the
[changelog guide](docs/guides/changelog.md) for the section format and the
[release guide](docs/guides/release.md) for how tagging is gated.

ARIA의 릴리스 기록입니다. 릴리스하려면 명시적인 `## [X.Y.Z]` 제목을 추가하세요.
`[Unreleased]`만으로는 태그가 생성되지 않습니다. 섹션 형식은
[변경 기록 가이드](docs/guides/changelog.ko.md), 태그 생성 조건은
[릴리스 가이드](docs/guides/release.ko.md)를 참고하세요.

[English](#english) | [한국어](#한국어)

## [Unreleased]

### English

#### Features

- A pluggable shopping assistant: a Strands agent on Amazon Bedrock AgentCore
  Runtime, with AgentCore Memory, a FastAPI service and a sample Nuxt storefront
  (NORDLY). See the [README](README.md) for the packages.
- Sign-in with managed Amazon Cognito or your own OIDC provider. The agent
  mints its own Gateway token, and the shopper's token never leaves it.
- Catalog search from a catalog definition: the packaged example, your catalog
  in Amazon OpenSearch Service with hybrid keyword and Cohere Embed v4 vector
  search, or your HTTPS catalog API. See [integrations](docs/guides/integrations.md).
- Tools behind AgentCore Gateway: Lambda functions, OpenAPI and API Gateway
  APIs, and MCP servers, with a request interceptor and Cedar policies.
- One AWS CDK app with Dev and Prod profiles, a read-only preflight, reviewed
  deployment plans, alarms and a dashboard.
- Documentation in English and Korean: guides, design docs, product specs and
  references.

### 한국어

#### 기능

- 통합 방식을 선택할 수 있는 쇼핑 어시스턴트: Amazon Bedrock AgentCore Runtime의
  Strands 에이전트, AgentCore Memory, FastAPI 서비스, 예제 Nuxt 쇼핑몰(NORDLY).
  패키지 구성은 [README](README.ko.md)를 참고하세요.
- 관리형 Amazon Cognito 또는 자체 OIDC 공급자로 로그인합니다. 에이전트는 자체
  Gateway 토큰을 발급받으며, 쇼핑객의 토큰은 에이전트 밖으로 나가지 않습니다.
- 카탈로그 정의 기반 검색: 패키지에 포함된 예제, 키워드와 Cohere Embed v4 벡터를
  함께 쓰는 하이브리드 검색으로 Amazon OpenSearch Service에 둔 자체 카탈로그,
  또는 HTTPS 카탈로그 API. [통합](docs/guides/integrations.ko.md)을 참고하세요.
- AgentCore Gateway 뒤의 도구: Lambda 함수, OpenAPI와 API Gateway API, MCP 서버.
  요청 인터셉터와 Cedar 정책을 적용합니다.
- Dev와 Prod 프로필을 갖춘 AWS CDK 앱 하나, 읽기 전용 사전 점검, 검토된 배포
  계획, 경보와 대시보드.
- 영어와 한국어 문서: 가이드, 설계 문서, 제품 명세, 참고 자료.
