# 시작하기

[English](getting-started.md)

## 설정

Node 24, pnpm 10.18.2, uv가 필요합니다. 각 Python 패키지는 `.python-version`에 버전을 고정하며, 없으면 uv가 설치합니다. 에이전트, API, infra, 인덱서는 이미지 런타임과 같은 3.14, 두 Lambda 패키지는 Lambda 런타임과 같은 3.13입니다. Docker 또는 Finch는 컨테이너 빌드와 실제 CDK 자산 번들링에만 필요합니다.

```sh
pnpm install --frozen-lockfile
pnpm check
```

검사는 합성 신원, fixture, 모의 전송 계층과 오프라인 CDK 합성을 사용합니다. AWS 서비스를 호출하지 않으며 AWS 리소스가 없는 새 복제본에서도 통과하도록 구성되어 있습니다.

다음으로 설정을 선택합니다:

```sh
cp packages/infra/config/dev.toml packages/infra/.toml
```

기본 템플릿은 관리형 Cognito와, 합성 상품 150개를 메모리에서 검색하는 패키지 포함 예제 카탈로그(`packages/catalog/src/aria_catalog/example/`)를 사용하고 장기 기억과 평가는 끄므로 상품 데이터셋이나 OpenSearch 도메인이 필요하지 않습니다. 자체 카탈로그를 검색하려면 카탈로그 정의를 작성해 `tools.productSearch.catalogFile`에 지정하고 `opensearch`나 `http`를 선택하세요([자체 카탈로그 사용](integrations.ko.md#자체-카탈로그-사용)). 도구는 Git에서 제외된 활성 `.toml`을 덮어쓰지 않습니다. 다른 파일을 선택하려면 `ARIA_CONFIG`를 사용하세요. `config/prod.toml`은 명시적 자리표시자, 강제 권한 검증, TLS, 가용성 설정을 포함합니다. 계정, DNS, 인증서, 알림, 인바운드 접근, 카탈로그 값을 바꾸세요.

## 배포 환경에 웹 앱 연결하기

웹 앱을 제외한 모든 구성 요소는 AWS에서 실행됩니다. 먼저 배포한 다음([릴리스](release.ko.md) 참고) 로컬 컴퓨터에서 SPA를 실행합니다:

```sh
cp packages/web/env/local.env packages/web/.env   # 아래 값을 채우세요
pnpm --filter nordly-web dev                       # http://localhost:4000
```

`packages/web/.env`는 스택 출력값으로 채웁니다. `OIDC_ISSUER`는 `DiscoveryUrl`에서 `/.well-known/openid-configuration`을 뺀 값이고, `OIDC_CLIENT_ID`는 `UserPoolClientId`입니다. `API_URL`은 `http://<ALBDns>:<serverPort>`이며, `api.certificateArn`을 설정했다면 HTTPS API 호스트입니다.

- API 로드 밸런서는 `api.allowIpList`의 주소만 허용합니다. 공인 IP를 `/32`로 추가하고 다시 배포하세요. Dev 템플릿의 `127.0.0.1/32`는 아무도 허용하지 않으며, 네트워크(VPN, 사무실, 집)가 바뀌면 목록을 갱신해야 합니다.
- `api.certificateArn`이 없으면 API는 일반 HTTP로 제공되므로 액세스 토큰이 암호화되지 않은 채 인터넷을 지나갑니다. 테스트 계정만 사용하거나 인증서를 설정하세요. 일부 네트워크는 8000번 포트를 막거나 일반 HTTP 스트림을 버퍼링합니다.
- 앱은 정확히 `http://localhost:4000`으로 여세요. Cognito 콜백과 API CORS 출처의 기본값이 이 주소입니다.

트레이스와 세션 지표는 AgentCore Observability, 즉 AgentCore 콘솔의 관측성 페이지나 CloudWatch의 생성형 AI 관측성 페이지에서 확인합니다. 스팬을 보려면 CloudWatch Transaction Search가 필요합니다. [운영](operations.ko.md)을 참고하세요. 이미지를 따로 빌드하려면 [컨테이너 빌드](release.ko.md)의 `packages/` 컨텍스트를 사용하세요. 자세한 내용은 [`packages/web/README.md`](../../packages/web/README.ko.md)와 [`packages/api/README.md`](../../packages/api/README.ko.md)에 있습니다.

## 쇼핑몰 이름 변경

브랜드는 코드와 문서에 등장합니다. 다음 위치는 동작에 영향을 줍니다:

- `packages/agent/src/prompts/system.md` — 에이전트에 전달하는 매장 정체성(이름과 소개). `packages/agent/tests/unit/test_builder.py`가 검증
- `packages/catalog/src/aria_catalog/example/catalog.toml` — 예제 카탈로그 이름과, 검색 도구를 선택할 때 모델이 읽는 도구 설명
- `packages/infra/stacks/agent_runtime_stack.py` — 배포 리소스 속성인 Memory 설명
- `packages/web/package.json` — `scripts/check.py`의 필터가 사용하는 `nordly-web` 패키지 이름
- `packages/web/nuxt.config.ts`와 `packages/web/env/local.env` — `BRAND_NAME` 기본값

나머지는 표시 문구와 문서입니다. `git grep -i nordly`로 모든 사용 위치를 찾을 수 있습니다.

## 배포 작업의 범위

`pnpm check`와 `scripts/build_images.py`는 로컬 작업만 수행합니다. `scripts/preflight.py`는 AWS 의존성을 읽기 전용으로 확인합니다. `scripts/plan_deployment.sh`는 운영자 검토용 로컬 합성과 읽기 전용 diff를 추가합니다. `scripts/smoke.py --invoke`는 모델을 호출하고 Memory 이벤트를 씁니다. `scripts/deploy.sh`는 AWS 리소스를 변경하므로 별도 운영자 승인이 필요합니다. 로컬 검증에는 배포나 smoke 호출이 포함되지 않습니다.

저장소에는 운영 설정 경로가 있지만 로컬 검사 통과만으로 운영 준비가 완료되지는 않습니다. 실제 트래픽을 받기 전에 위임된 DNS 이름/인증서를 사용하는 공개 HTTPS, 자체 IdP와 카탈로그 권한 관리, 경보 전달과 사고 대응 훈련, 합의된 할당량/비용 예산에 따른 대표 부하·안전성 증거, RPO/RTO를 기록한 복원/롤백 훈련이 필요합니다. 단계별 증거 체크리스트는 [릴리스](release.ko.md#운영-트래픽-전-인수-검사)에 있습니다.
