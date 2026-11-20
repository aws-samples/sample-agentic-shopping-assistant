# 릴리스와 인수 검사

[English](release.md)

저장소는 로컬 검증 게이트와 배포 파이프라인이 호출할 스크립트를 제공합니다. 파이프라인 정의 자체는 포함되지 않으므로, 이 게이트를 사용하는 CI를 직접 구성하고 거기에서 runner 신뢰, 보호된 브랜치/태그, 자격 증명, 환경 승인을 설정하세요. 자동 배포는 없습니다.

## 로컬 게이트

저장소 루트에서 실행합니다:

```sh
pnpm install --frozen-lockfile
pnpm check
pnpm audit --audit-level high
CONTAINER_ENGINE=finch pnpm build:images
```

`pnpm check`는 잠금된 Python 환경을 설치하고 API, 에이전트, 카탈로그, 인덱서, contracts, Lambda 테스트와 Dev/Prod CDK 프로필 행렬을 실행한 뒤 스크립트 테스트, 문서 검사(`scripts/check_docs.py`), 웹 계약 테스트, 운영 빌드를 수행합니다. Python 테스트는 모의 처리를 벗어난 botocore 네트워크 요청을 거부합니다. 오프라인 합성은 명시적 가짜 계정/AZ 컨텍스트를 사용하고 자산 번들링을 끕니다. 이 검사는 배포된 AWS 서비스에 접근하거나 호출하지 않습니다.

이미지 빌더는 Git에서 제외된 `artifacts/`에 ARM64 에이전트, API, 인덱서 아카이브와 릴리스 매니페스트를 생성합니다. 베이스/collector 이미지는 `build-images.json`과 Dockerfile에 고정되어 있고 Python/pnpm 잠금 파일이 패키지 버전을 관리합니다. 운영 이미지는 개발 의존성을 제외하고 시작 시가 아닌 빌드 시 패키지를 설치합니다. 고정값을 정기적으로 갱신하고 다시 스캔하세요. 불변 입력만으로 취약점 안전성이 확보되지는 않습니다.

세 아카이브와 Lambda 의존성 매니페스트를 Trivy로 스캔하고 High/Critical 발견 시 빌드를 실패시키세요. 고정된 스캐너 다이제스트는 `build-images.json`에 있습니다. 의존성 감사에는 빌드 도구도 포함됩니다. 통과만을 위해 포괄적인 무시 규칙을 추가하지 마세요. 선택된 Nuxt 버전과 제한적인 전이 의존성 override는 감사된 잠금 파일에 맞춘 것이므로 업그레이드 시 다시 평가합니다.

## 파이프라인 요건과 태그

이 스크립트로 구성한 파이프라인은 검증, 이미지 빌드, 이미지 스캔, 릴리스 태그를 실행한 뒤 저장된 배포 계획과 보호된 수동 배포 단계를 두어야 합니다. 이미지 빌드에는 Docker 데몬이 있는 ARM64 runner가 필요합니다. 높은 권한의 runner 접근은 격리되고 신뢰할 수 있는 runner에만 허용합니다. 배포 단계에는 Docker 데몬/소켓과 명시적 대상 계정용 단기 자격 증명이 필요합니다. 신뢰할 수 없는 pull request에 배포 자격 증명이나 소켓을 노출하지 마세요.

`scripts/release.py`는 명시적인 `## [X.Y.Z]` 또는 `## [vX.Y.Z]` changelog 제목을 인식해 태그할 버전을 출력합니다. `[Unreleased]`만으로는 태그를 만들지 않습니다. Unreleased 메모 편집이 이전 릴리스를 다시 태그하지 않도록 `--changed-from <rev>`를 전달하세요. 새로 추가한 버전에 이미 태그가 있다면 같은 커밋을 가리켜야 합니다. 보호된 push ID를 구성하고 Git 원격 URL에 토큰을 넣지 마세요. 태그에서는 계획/배포 단계 전에 검증/빌드/스캔을 다시 실행합니다.

스캔 보고서, 커밋, 이미지 ID, 잠금 파일 해시, 설정 지문, 승인, 배포/변경 세트 기록을 함께 보관하세요. 제공된 CDK 경로는 선택한 커밋에서 자체 이미지 자산을 빌드하며 ECR 다이제스트 승격 시스템이 아닙니다. 트래픽 승격 전에 게시된 자산 다이제스트를 대조/스캔하세요. 정책상 사전 스캔한 바이너리와 정확히 같은 결과물을 배포해야 한다면 고객 아티팩트 레지스트리/승격 단계를 추가합니다. 빌더는 ARM64 이미지를 내보내므로 X86_64 API 설정에는 맞는 빌드/스캔 작업이 필요합니다.

## 읽기 전용 사전 점검

선택한 AWS 계정을 조회할 권한이 있을 때만 실행합니다:

```sh
ARIA_CONFIG=/absolute/path/to/reviewed.toml \
uv run --project packages/api --frozen python scripts/preflight.py
```

사전 점검은 STS ID, 모델/추론 프로필 가용성, Runtime 서브넷의 가용 영역, API 인증서, 카탈로그 검색 의존성, 활성화된 경우 X-Ray 트레이스 목적지를 읽습니다. 계정 불일치와 템플릿 자리표시자를 거부합니다. `api.certificateArn`이 있으면 인증서가 `ISSUED` 상태이고 스테이지 리전에 있어야 합니다(`acm:DescribeCertificate`). DNS로 검증하는 인증서는 검증 레코드가 도메인의 권한 있는 네임서버에서 조회될 때에만 발급되며, 그 밖의 인증서는 배포 마지막에 로드 밸런서가 도메인 이름, 서명, 키 크기에 관한 오류로 거부합니다. 사전 점검은 모델을 호출하거나 AWS를 변경하지 않습니다.

Runtime은 항상 VPC에서 실행되며, AgentCore는 [지원되는 가용 영역](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/agentcore-vpc.md)에 나열된 AZ ID에서만 VPC 연결을 지원합니다. Runtime이 쓰는 서브넷이 그 밖의 AZ에 있으면 사전 점검이 해당 AZ ID를 밝히며 실패합니다.

- **기존 VPC(`vpc.vpcId`):** Runtime이 선택하는, 외부로 나가는 경로가 있는 프라이빗 서브넷(`ec2:DescribeSubnets`, `ec2:DescribeRouteTables`).
- **관리형 VPC:** VPC가 서브넷을 만드는 리전의 처음 세 가용 영역(`ec2:DescribeAvailabilityZones`).

이 목록은 `scripts/preflight.py`에 복사되어 있으니 AWS가 영역을 추가하면 갱신하세요.

`opensearch` 공급자에서는 사전 점검이 카탈로그 정의의 의존성도 확인합니다.

- **임베딩 모델:** `embedding.model`의 추론 프로필이 `ACTIVE`이고(`bedrock:GetInferenceProfile`), 그 뒤의 기반 모델이 리전에서 승인되어 사용 가능한지(`bedrock:GetFoundationModelAvailability`) 확인합니다. Runtime 모델과 같은 검사입니다.
- **Nori:** `analyzer = "nori"`이면 리전이 OpenSearch 3.5용 `analysis-nori` 플러그인을 제공하는지 확인합니다(`es:DescribePackages`). 제공하지 않으면 실패하며 `standard`를 쓰라고 안내합니다.

JSON 출력에는 Runtime의 영역 ID(`runtimeZoneIds`), 인증서 유형(`certificate`, `AMAZON_ISSUED` 또는 `IMPORTED`)과, `opensearch`에서는 `embeddingModel`과 `noriPackage`가 들어갑니다. SCP, 할당량, 대상 권한, 전체 요청 경로는 별도 인수 검사가 필요합니다. 리전을 거부하는 SCP는 `global` 프로필의 요청을 허용해야 합니다([Embed v4 참고](../references/bedrock-embed-v4-notes.md#permissions)).

저장소의 기본 모델은 `global.openai.gpt-5.6-luna`입니다. 기본 Strands `BedrockModel`은 Converse를 사용하고 CDK는 `MODEL_CACHE_ENABLED=false`를 주입합니다. 모델 builder는 GPT-5 모델 ID의 temperature를 생략합니다. 로컬 테스트는 이 설정을 검증할 뿐 계정의 모델 접근이나 실제 호출 성공을 검증하지 않습니다. 구현된 캐시 허용 조건은 [모델 캐싱](../design-docs/model-caching.md)을 참고하세요.

## 수동 배포

먼저 `ARIA_CONFIG`를 설정한 깨끗한 체크아웃에서 `sh scripts/plan_deployment.sh`를 실행합니다. 읽기 전용 사전 점검, 로컬 합성, `--change-set=false` CDK diff를 수행하며 변경 세트 생성이나 배포는 하지 않습니다. 모든 IAM 변경과 교체를 포함해 저장된 어셈블리, `artifacts/deployment-diff.txt`, `artifacts/deployment-plan.json`을 검토하세요.

그 뒤에만 `ARIA_ALLOW_DEPLOY=yes`로 `sh scripts/deploy.sh`를 실행합니다. 배포 스크립트는 검토된 계획과 커밋, 설정 해시, 모든 어셈블리 파일을 비교하고 사전 점검을 다시 실행한 후 재합성 없이 해당 어셈블리를 배포합니다. 이 명시적 opt-in이 승인 경계이며 승인된 이 명령에만 대화형 CDK 프롬프트를 비활성화합니다. CI에서 실행한다면 보호된 수동 단계로 유지하고 작업/아티팩트 접근과 태그/runner 권한을 적절히 제한하세요. 계획이 만료되거나 변경되면 새 계획과 검토가 필요합니다.

다른 스택이 가져오는(import) 리소스를 제거하는 변경이라면 가져오는 스택을 먼저 배포합니다. `ARIA_DEPLOY_STACKS`에 그 스택 이름을 공백으로 구분해 지정하세요([배포 안전성](../design-docs/deployment-safety.md#cross-stack-exports-fix-deploy-order)). 스크립트는 같은 검증된 어셈블리에서 해당 스택만 배포합니다. 그다음 변수 없이 다시 실행합니다.

배포는 상태를 변경하므로 검토 중 실행하지 마세요.

`opensearch` 공급자에서는 인덱싱이 별도의 릴리스 단계입니다. 배포는 도메인, 카탈로그 버킷, 인덱싱 태스크를 만들고 Runtime과 태스크에 정의를 전달하지만 인덱스는 만들지 않습니다. 스테이지를 처음 배포한 직후와, 인덱스가 따라가야 하는 정의 변경 뒤에는 `scripts/index_catalog.sh`를 `--apply`와 함께 실행하세요([운영](operations.ko.md#카탈로그-인덱싱)). 배포된 정의로 만든 인덱스를 별칭이 가리킬 때까지 검색은 실패하거나 결과를 놓칩니다([카탈로그 정의 변경](operations.ko.md#카탈로그-정의-변경)).

두 스크립트 모두 `--lookups false`로 CDK를 실행하므로 계획 단계에서 계정을 조회할 수 없습니다. 새 계정이나 리전에서 처음 계획을 만들기 전에 해당 가용 영역(과 기존 VPC 조회 결과)을 `packages/infra/cdk.context.json`에 기록하세요. 대상 계정의 읽기 전용 자격 증명으로 조회를 켠 합성을 한 번 실행합니다. CDK는 `ec2:DescribeAvailabilityZones`를 호출하고, 기존 VPC를 쓰면 VPC, 서브넷, 라우트 테이블도 읽습니다.

```sh
cd packages/infra
ARIA_CONFIG=/absolute/path/to/reviewed.toml ../../node_modules/.bin/cdk synth <ns>Vpc --exclusively
```

파일을 검토한 뒤 배포에 사용하는 브랜치에 커밋하세요. 계획은 깨끗한 체크아웃을 요구하므로 추적되지 않은 컨텍스트 파일이 있으면 중단됩니다. 이 파일에는 대상 계정이 들어가므로 이 저장소에는 포함하지 않습니다.

이 스크립트는 계정 bootstrap, DNS 설정, Transaction Search 활성화, 기존 리소스 가져오기를 수행하지 않습니다.

첫 Prod 배포 전에 [출시 설정](launch-settings.ko.md)의 설정을 정하세요. Prod 설정에는 계정/리전, TLS 인증서, 명시적 네트워크/CORS 설정, 최소 2개 태스크, 경보 대상, 강제/검증된 도구 권한이 필요하며, fixture 카탈로그는 거부됩니다. 저장소의 Prod 템플릿은 의도적으로 자리표시자를 포함합니다. Prod의 롤링 업데이트는 기존 태스크를 멈추기 전에 교체 태스크를 먼저 시작하므로, 잠시 원하는 태스크 수의 최대 두 배가 실행됩니다. 이에 맞게 Fargate 할당량과 서브넷 주소를 남겨 두세요. 대상이 정상이고 로그인 검사를 통과한 뒤에만 DNS를 로드 밸런서로 연결하세요.

## 롤백

1. 마지막으로 정상이었던 릴리스 태그를 체크아웃합니다.
2. 같은 검토된 설정으로 계획을 만들고(`scripts/plan_deployment.sh`), diff에서 교체와 삭제를 검토합니다.
3. 그 계획을 배포합니다.

롤백으로 되돌아가지 않는 것:

- Memory 이벤트와 기록, OpenSearch 문서, 인덱스 별칭. 이전 카탈로그로 돌아가려면 그 원본을 새 버전으로 다시 인덱싱하거나, 별칭을 이전 인덱스로 옮깁니다([운영](operations.ko.md#승격-롤백-정리)). 정의를 롤백하면 그 정의로 카탈로그를 다시 인덱싱해야 합니다.
- 쇼핑객 토큰의 스코프. 토큰은 로그인할 때 받은 스코프를 그대로 유지하므로, 롤백으로 스코프가 바뀌면 쇼핑객이 다시 로그인해야 할 수 있습니다([로그인과 세션](../product-specs/sign-in-and-sessions.md#after-a-scope-change)).
- 제거된 스택 간 export. 이를 다시 가져오는 이전 계획에는 단계적 배포가 필요할 수 있습니다([배포 안전성](../design-docs/deployment-safety.md)).
- 이미지. 다이제스트로 복원하지 않고 커밋에서 다시 빌드합니다.

Runtime 버전은 고정되지 않으므로 롤백은 모든 새 세션에 한꺼번에 적용됩니다([기술 부채 목록](../exec-plans/tech-debt-tracker.md)).

## 운영 트래픽 전 인수 검사

각 게이트의 예상 결과, 담당자, 증거, 통과/실패를 기록합니다:

| 게이트 | 필수 증거 |
| --- | --- |
| 로그인과 갱신 | 관리형 Cognito 또는 선택한 BYO IdP에서 PKCE, 액세스 토큰 갱신, 로그아웃이 완료됨. 잘못된 issuer/수신 대상/서명/스코프, 만료 토큰, ID 토큰 대체가 거부됨. |
| 세션 격리 | 두 사용자가 서로의 세션을 읽기/호출/삭제/재개할 수 없음. Runtime 직접 호출과 신뢰 테넌트 간에도 반복. 호출자에 묶이지 않은 세션 ID는 형식이 잘못된 것을 포함해 거부됨. |
| 실제 모델/도구 경로 | 모델 접근이 허용되고 한국어 스트리밍 대화가 설정된 도구를 호출해 근거 있는 상품을 반환하며 빈/오류/알 수 없는 출력을 처리함. 진행 중 세션의 자격 증명 갱신도 확인. |
| 도구 권한 | Cedar ENFORCE가 비허용 작업과 잘못된 인자를 거부함. 새 도구는 승인 전 거부 상태 유지. 외부 Gateway는 `authorizationVerified` 설정 전에 동등한 제어를 검증. |
| 콘텐츠 안전 | 사용자 입력, 카탈로그 필드, Memory의 프롬프트 주입을 테스트. 시크릿/개인정보 요청, 크기 초과 요청/결과, 미지원 쓰기, 도구 반복 예산 소진이 안전하게 실패해야 함. Fixture에 실제 시크릿을 사용하지 않음. |
| 신뢰성과 지연 | 대표 동시 세션에서 첫 토큰/전체 턴 p50/p95, 시간 초과/오류율, 확장을 측정. Lambda/모델 throttling, 상위 실패, 연결 종료/취소, Runtime 만료를 시험하고 결과로 고객 SLO/할당량 설정. |
| Memory와 복구 | Runtime 세션이 끝난 뒤에도 대화가 이어지는지, 장기 기억 켬/끔, 페이지 처리 기록, 삭제 검증, 네임스페이스 격리를 확인. 카탈로그 스냅샷을 격리 목적지에 복원하고 별칭 롤백 입증. RPO/RTO 기록. 검증되지 않은 복구 약속 금지. |
| 운영과 개인정보 | 구조화 로그, 트레이스, 토큰 지표, 경보 전달, 온콜 담당 확인. 콘텐츠 수집이 꺼진 상태에서 가림 처리 검증. 온라인 평가 전에 저장/보존을 명시적 승인. |

`scripts/smoke.py --invoke`는 **명시적으로 선택하는 유료 에이전트 호출이며 Memory에 기록합니다**. `ARIA_API_URL`과 단기 `ARIA_ACCESS_TOKEN`이 필요합니다. 시크릿은 커밋하거나 출력하지 않고 환경 변수로 제공합니다. 세션을 만들고 한국어 응답 하나를 확인하며 더 넓은 인수 검사 행렬을 대체하지 않습니다. 정리/삭제는 자동 수행하지 않습니다.

텔레메트리와 개인정보 전제 조건을 충족할 때까지 온라인 평가를 꺼 두세요. 최소한 출시 전에 기록된 대표 품질/안전 회귀 검사를 완료합니다. 온라인 평가를 켠다면 승인된 낮은 샘플 비율로 시작하고 비용/평가 범위를 모니터링합니다. 로컬 단위 테스트는 실제 호출, 로그인, 부하, 복구 증거를 만들지 않습니다.

## 웹 앱 호스팅

쇼핑몰은 정적 SPA이며, 이를 호스팅하는 스택은 없습니다.

1. `pnpm --filter nordly-web generate`로 빌드하면 `packages/web/dist/`가 생성됩니다. 공개 설정(`API_URL`, `OIDC_*`, `BRAND_NAME`. [FRONTEND.md](../FRONTEND.md) 참고)은 빌드 시 고정되므로 바뀌면 다시 빌드합니다.
2. `auth.callbackUrls`, `auth.logoutUrls`, `api.corsAllowOrigins`에 등록한 출처에서 HTTPS로 제공하고, `/auth/callback`을 포함한 알 수 없는 경로는 `index.html`로 다시 씁니다.
3. 호스트에서 Content-Security-Policy 헤더를 보냅니다([SECURITY.md](../SECURITY.md)).

## 컨테이너 빌드와 런타임 구성

컨테이너 런타임이 필요한 단계는 두 가지입니다:

1. **릴리스 이미지 빌드** — `scripts/build_images.py`가 `agent`, `api`, `indexer` 이미지(`linux/arm64`)를 빌드해 `artifacts/` 아래 tarball로 저장합니다.
2. **CDK 이미지 자산** — `scripts/plan_deployment.sh`가 실행하는 `cdk synth`는 에이전트와 API 이미지, 그리고 `opensearch`에서는 인덱서 이미지(`DockerImageAsset`)의 빌드 컨텍스트를 준비하고, `scripts/deploy.sh`는 자산을 게시할 때 이미지를 빌드합니다. 두 스크립트 모두 런타임을 찾지 못하면 시작하기 전에 멈춥니다. Lambda 자산은 로컬에서 복사하므로 컨테이너가 필요하지 않습니다.

런타임은 **Docker**, **Finch**, **CodeBuild** 세 방식으로 제공할 수 있습니다.

### Docker (기본값)

`PATH`에 `docker`가 있으면 추가 설정 없이 동작합니다. `build_images.py`는 `CONTAINER_ENGINE`(기본 `docker`)을 사용하고, `plan_deployment.sh`와 `deploy.sh`는 `CDK_DOCKER`용 런타임을 자동으로 찾습니다.

### Finch

[Finch](https://runfinch.com/)는 Docker 호환 CLI입니다. 먼저 VM을 시작한 뒤(`finch vm start`) 사용합니다:

- **이미지 빌드:** `CONTAINER_ENGINE=finch python3 scripts/build_images.py`
- **CDK 이미지 자산:** `plan_deployment.sh`와 `deploy.sh`는 Docker를 우선하고 없으면 Finch를 자동으로 사용합니다. `CDK_DOCKER=finch`로 강제할 수 있습니다. CDK는 `CDK_DOCKER` 값을 Docker 호환 CLI로 실행합니다.

### CodeBuild

개발자 컴퓨터나 CI runner 대신 AWS에서 빌드하려면 AWS CodeBuild 프로젝트가 저장소의 [`buildspec.yml`](../../buildspec.yml)을 사용하도록 설정합니다. 같은 `scripts/build_images.py`를 실행하고 `artifacts/{agent,api,indexer}.tar`와 `artifacts/release.json`을 빌드 아티팩트로 내보냅니다. 직접 push하지 않으므로 후속 단계에서 tarball을 검사하거나 게시합니다.

프로젝트에는 다음 설정이 필요합니다:

- **`privilegedMode: true`** — 이미지 빌드에 Docker 데몬이 필요합니다.
- **ARM64 환경 이미지** — 빌드 이미지가 `linux/arm64`이므로 예를 들어 `aws/codebuild/amazonlinux-aarch64-standard:3.0`을 사용합니다.

`build_images.py`에는 해당 이미지에 포함된 `python3`, `git`, Docker 데몬만 필요하므로 buildspec은 `uv`/`pnpm`을 설치하지 않습니다. CDK 합성/번들링은 Docker/Finch 호스트의 `plan_deployment.sh` 또는 같은 권한의 ARM64 환경을 가진 별도 CodeBuild 프로젝트에서 실행합니다.

### 선택 기준

Docker와 Finch는 호스트별로 선택하며 서로 교체할 수 있습니다. 배포별 설정 필드는 없고 `CONTAINER_ENGINE` / `CDK_DOCKER` 환경 변수만 사용합니다. Docker Desktop이 없는 호스트에서는 Finch를, 개발자 컴퓨터나 CI runner 대신 AWS에서 빌드하려면 CodeBuild를 사용합니다.

### 이미지 직접 빌드

에이전트와 API Dockerfile은 공통 contracts 패키지를, 에이전트와 인덱서 Dockerfile은 카탈로그 패키지를 복사합니다. 저장소 루트에서 `packages`를 컨텍스트로 실행하세요:

```sh
finch build -f packages/agent/Dockerfile -t aria-agent:local packages
finch build -f packages/api/Dockerfile -t aria-api:local packages
finch build -f packages/indexer/Dockerfile -t aria-indexer:local packages
```

Docker를 선택했다면 `finch`를 `docker`로 바꿉니다. 에이전트 이미지는 ARM64이고 `:8080`에서 서비스합니다. API 기본 포트는 `:8000`입니다. 둘 다 `opentelemetry-instrument`로 설치된 환경을 시작하며 시작 시 의존성을 설치하지 않습니다. 인덱서 이미지도 ARM64이며 `python -m aria_indexer`를 한 번 실행하고 끝납니다. `--help`가 인자를 보여 줍니다. 개별 패키지 안에서 빌드하면 `contracts`와 `catalog`가 제외되어 이 Dockerfile에 맞지 않습니다.
