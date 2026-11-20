# ARIA 인프라

[English](README.md)

Python CDK는 고정된 AgentCore Runtime과 AgentCore Memory를 배포하며, Memory의 장기 기억 추출은 명시적으로 켜야 합니다. Cognito, 도구 대상, OpenSearch 카탈로그(도메인, Nori 플러그인, 비공개 카탈로그 버킷, 인덱싱 태스크) 생성 여부는 타입 검증을 거치는 통합 설정에 따릅니다. API는 ALB/WAF 뒤의 Fargate에서 실행되고 Operations 스택은 경보와 대시보드를 제공합니다. 지속적 평가는 명시적으로 활성화해야 합니다.

`ARIA_CONFIG=/absolute/path/config.toml`을 사용합니다. 지정하지 않으면 로더는 Git에서 제외된 `.toml`을 읽습니다. `config/dev.toml`은 fixture 공급자로 패키지에 포함된 예제 카탈로그를 사용하는 Dev 템플릿입니다. `tools.productSearch.catalogFile`은 설정 파일 기준 상대 경로로 카탈로그 정의를 가리키며, 로더가 합성 시 검증해 Runtime과 인덱싱 태스크에 `CATALOG_DEFINITION`으로 전달합니다. `config/prod.toml`은 HTTP 카탈로그 엔드포인트를 포함한 자리표시자를 실제 값으로 바꿔야 하는 운영 환경 템플릿입니다. 운영 환경에는 명시적인 계정/리전, HTTPS/인증서/CORS, 최소 2개의 API 태스크, 도구 권한 검증, 알림 대상이 필요하며, fixture 카탈로그는 거부됩니다. 관리형 VPC는 Prod에서 AZ당 NAT 하나를 사용하며, Dev의 기본값은 NAT 하나와 인터페이스 엔드포인트 서비스 3개입니다. 기존 VPC의 라우팅은 고객이 관리합니다.

`uv sync --frozen && uv run --frozen pytest`를 실행합니다. `tests/test_profiles.py`는 Docker 번들링과 AWS 조회를 끈 상태에서 실제 애플리케이션을 구성하고 AwsSolutionsChecks를 적용합니다. 예제/BYO 및 Dev/Prod 프로필에서 nag 오류가 있으면 실패합니다. 이는 합성 검사이며 배포 성공이나 복구의 증거가 아닙니다.

배포 전에 [릴리스](../../docs/guides/release.ko.md), [통합](../../docs/guides/integrations.ko.md), [운영](../../docs/guides/operations.ko.md) 문서를 읽으세요. 사용 중인 설정을 `config/dev.toml`이나 `config/prod.toml`로 덮어쓰지 마세요. `dev.toml`은 예제 카탈로그를, `prod.toml`은 자리표시자 HTTP 카탈로그를 선택하며, 두 템플릿 모두 장기 기억을 끕니다. 외부 리소스는 고객 소유로 유지됩니다.
