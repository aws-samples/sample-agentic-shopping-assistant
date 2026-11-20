# 운영과 개인정보

[English](operations.md)

운영 전에 온콜 담당자, 검증된 경보 수신 대상, 고객 SLO/RPO/RTO를 정합니다. 아래 작업 중 서비스 상태, 데이터, 계정 설정, 비용을 바꾸는 모든 작업에는 운영자의 일반 변경 승인이 필요합니다. 이 가이드는 작업을 실행하지 않습니다.

## 관측 지표

`{namespace+stage}-operations`는 API/에이전트 오류, p95 턴 소요 시간, 모델 입력/출력 토큰, 상품 검색 실패, 경보 상태를 표시합니다. Operations 스택은 API/에이전트 오류, 세션 생성 실패, 모델 throttling, 도구 거부, 상품 검색 실패와 OpenSearch에서의 쿼리 임베딩 실패, 비정상 대상, 부족한 정상 API 태스크, 대상과 로드 밸런서의 5xx, WAF 속도 제한 차단, 인터셉터 거부, Gateway Lambda(인터셉터, 배포된 경우 saved-items 예제) 오류 또는 throttling, OpenSearch red 상태, 디스크 부족, 자동 스냅샷 실패에 경보를 설정합니다. 경보별 임계값과 기간은 [RELIABILITY.md](../RELIABILITY.md#alarms)에 있으며, 누락 데이터를 위반으로 처리하는 경보는 `ApiHealthyHosts`뿐입니다. 고객에게 가용성 SLO가 필요하면 독립적인 합성 모니터링을 구성하세요.

먼저 검색할 Runtime 구조화 이벤트: `agent_turn_failed`(`code` 포함), `session_build_failed`, `tool_denied`(`reason` 포함), `tool_budget_ended_turn`, `agent_turn_superseded`, `interrupted_turn_closed`, `tool_result_blocked`, `tool_result_screen_failed`, `gateway_http_error`, `gateway_request_failed`, `gateway_token_failed`, `local_tool_not_allowed`, `product_search_failed`, `product_search_embedding_failed`(키워드로만 실행된 검색), `product_search_invalid_product`(카드가 되지 못한 카탈로그 상품). 인터셉터는 거부할 때마다 `interceptor: target invocation denied: <reason>`을 기록합니다.

API와 Runtime은 구조화된 JSON을 출력합니다. API는 OTLP 트레이스를 ECS collector로 보내고 Runtime은 `opentelemetry-instrument`로 시작해 관리형 텔레메트리 라우팅을 사용합니다. 배포 후 실제 서비스 이름과 트레이스/로그 목적지를 확인하세요. 애플리케이션 실행이 텔레메트리 전달을 입증하지는 않습니다. Collector가 실패해도 API는 정상 상태일 수 있습니다.

Runtime 로그는 30일, WAF 로그는 30일 보존하며 ALB 접근 아카이브 수명 주기는 90일입니다. API, Lambda, OpenSearch 로그 보존은 리소스에 선언됩니다. WAF 로그의 Authorization/Cookie 헤더는 가리고 샘플 요청은 비활성화합니다. Runtime 콘텐츠 수집은 기본적으로 꺼져 있습니다. 사고 티켓에 토큰, 프롬프트, 카탈로그 시크릿, Memory 페이로드를 넣지 마세요. 구조화된 예외 트레이스에는 하위 오류 상세가 포함될 수 있으므로 접근을 제한해야 합니다.

온라인 평가에는 명시적으로 승인된 `operations.captureContent=true`, 검증된 `transactionSearchReady=true`, 실제 `traceLogGroup`, `evaluations.enabled=true`가 필요합니다. 새 통합 Runtime은 일반적으로 자체 Runtime 로그 그룹을 사용하고 `aws/spans`는 기존 분리형 목적지입니다. 평가기를 구성하기 전에 현재 목적지를 조회하고 알려진 트레이스를 확인하세요. 이 CDK 앱은 계정 수준 Transaction Search를 활성화하지 않습니다. 선택한 기본 평가기/judge가 계정/리전에서 사용 가능한지 확인하고 실제 평가 세션 비율을 측정하세요. 평가된 세션이 0개인 상태를 좋은 품질로 간주하지 마세요. 단계별 설정, 검증, 문제 해결은 [평가](evaluations.ko.md)를 참고하세요.

## 사고 초기 분석

| 증상 | 먼저 확인할 항목 | 대응 |
| --- | --- | --- |
| 로그인/API 401/403 | IdP issuer/JWKS, 액세스 토큰 `aud` 또는 `client_id`, 스코프, 만료, API/Runtime/Gateway 인증 설정 | 설정/키 교체를 수정하거나 다시 로그인합니다. JWT 검증을 우회하거나 CUSTOM_JWT Runtime에 SigV4를 대신 사용하지 마세요. |
| 모델 403/429 | 설정된 프로필/모델 접근, 역할 정책, 계정 제한, 할당량 | 접근 문제나 승인된 할당량을 해결하고 검토된 호환 모델/설정 롤백을 고려합니다. 자동 모델 대체나 공급자 간 데이터 전송은 없습니다. |
| 모든 턴에서 모델 500 | `code`가 `internal`이고 `error_type`이 `InternalServerException`인 `agent_turn_failed`, 스테이지 리전에서 같은 모델에 보내는 아주 작은 `bedrock-runtime converse` 호출 | 그 모델에 대한 Bedrock 쪽 장애입니다. 대체 모델이 없으므로 복구될 때까지 모든 턴이 실패합니다([기술 부채 목록](../exec-plans/tech-debt-tracker.md)). 복구를 기다리거나, 검토된 배포로 `runtime.modelId`를 응답하는 모델로 바꾸세요. |
| 에이전트/Gateway 초기화 실패 | Gateway/대상 준비, 자격 증명 공급자, 디스커버리(엔드포인트 조회), Cedar 결정, Runtime IAM 의존성 | 의존성을 복구하고 새 요청으로 재시도합니다. 실패한 세션 생성은 캐시하지 않습니다. 가용성 복구를 위해 Prod를 LOG_ONLY로 바꾸지 마세요. |
| 느리거나 실패한 턴 | 전체 트레이스, API 지연, 모델 throttling, Lambda 소요 시간/throttling, 검색 상태 | 제한 시간을 늘리기 전에 느린 의존성을 찾습니다. 기본값: 모델 읽기 60초, HTTP 어댑터 10초, 인터셉터와 예제 Gateway Lambda 10초, MCP 읽기 30초, 턴 120초, 브라우저 요청 130초, ALB 유휴 150초. |
| ECS 대상 비정상 | 태스크 이벤트, `/ping`, 컨테이너 포트/설정, 시작 로그, collector/리소스 압박 | 새 태스크 리비전을 확인하고 필요하면 호환 애플리케이션 릴리스로 롤백합니다. 상태 확인은 모델/IdP/검색 접근을 검증하지 않습니다. |
| OpenSearch red/디스크/스냅샷 경보 | 클러스터/노드 상태, 저장 공간, JVM 압박, 샤드 할당, 복제본/스냅샷 상태 | 인덱스 승격을 중단합니다. 승인된 변경으로 용량/복제본을 복구하고 트래픽 전환 전에 격리 도메인에 복원합니다. 복구 계획 없이 공간 확보를 위해 활성 인덱스를 삭제하지 마세요. |
| 로그/평가 누락 | IAM, Runtime 로그 그룹 소유권, collector 로그, 트레이스 목적지, `service.name`, 샘플링/수집 설정 | 전달/평가 범위를 복구합니다. 첫 진단 단계로 광범위한 콘텐츠 로깅을 켜지 마세요. |
| 토큰/도구 악용 의심 | WAF/ALB/API 메타데이터, IdP 세션, `tool_denied`와 인터셉터 거부, Gateway 결정, 영향받은 대상 | 고객 사고 대응 절차로 ID 접근을 철회하고 해당 통합을 제한합니다. 필요한 범위의 증거만 보존하고 백엔드 권한을 최종 데이터 경계로 사용합니다. |
| `WafRateLimitBlocks` | WAF 로그 그룹의 차단된 클라이언트 IP | 한 NAT 뒤의 쇼핑객(사무실, 캠퍼스)은 IP를 공유합니다. 차단된 IP가 정상 사용자라면 검토된 배포로 `api.wafRateLimit`을 올립니다. |

웹 중지 버튼은 브라우저 스트림을 취소하고 리더를 해제합니다. 이미 실행 중인 모델/도구 작업의 즉시 취소를 보장하지는 않습니다. 대화 삭제는 사용 가능한 Memory 기록을 삭제하기 전에 `StopRuntimeSession`을 명시적으로 호출합니다. Runtime이 유휴 만료(900초)와 최대 수명(3600초)을 관리하며 애플리케이션 세션 데이터베이스나 세션별 정리 백그라운드 작업은 없습니다.

## 용량과 비용

Dev 템플릿은 NAT gateway 하나, API 태스크 하나, 메모리에서 검색하는 합성 예제 카탈로그를 사용하며 Memory의 장기 기억 추출은 끕니다. OpenSearch, 카탈로그 버킷, 인덱싱 태스크는 `opensearch` 공급자를 선택한 경우에만 생성합니다. 이 선택은 유휴 비용을 줄이지만 고가용성을 구성하지는 않습니다.

관리형 Prod VPC는 NAT gateway 3개를 사용하고 API는 최소 2개 태스크로 시작해 CPU/메모리/ALB 요청에 따라 확장합니다. 예제 Prod OpenSearch는 AZ에 걸쳐 데이터 노드 3개와 마스터 3개를 사용합니다. 이 중복 구성은 대화가 없어도 상당한 고정 비용이 듭니다. 실제 카탈로그로 샤드/복제본 수, 저장 공간 여유, 처리량을 검증하세요. 인덱스마다 기본 샤드 하나와 명시적인 복제본 수를 씁니다. 1024차원 벡터는 HNSW 그래프를 빼고도 상품당 4 KB를 차지하며, 도메인에 남겨 둔 버전마다 따로 공간을 씁니다. 인덱싱 태스크는 적재하는 동안에만 1 vCPU와 4 GiB로 실행됩니다.

인터셉터는 동시 실행 20개, saved-items 예제는 10개를 예약합니다. 모든 Gateway 도구 호출이 인터셉터를 거치므로 스테이지의 동시 Gateway 호출은 20개로 제한됩니다. 서비스 할당량, API 확장, 도구 병렬 호출에 맞는지 검증하세요. 태스크 수만 늘려서는 Lambda, Gateway, AgentCore, 모델 용량이 늘지 않습니다. 에이전트는 턴별 도구 수와 Runtime 컨테이너별 턴 수를 제한하지만 이는 악용 경계이며 영속적인 사용자별 금액 할당량이 아닙니다.

활성/유휴 Runtime 세션, 모델 입력/출력 토큰, 교차 리전 추론, Embed v4 호출(인덱싱하는 상품 96개마다 한 번, 쿼리가 있는 검색마다 한 번), Memory 이벤트와 추출/검색, 로그/트레이스량, 평가 judge 사용량을 측정합니다. 고객 관리 예산과 비용 이상 알림은 계정 전체 및 `namespace`/`stage` 태그를 포함해야 합니다. 이 프로젝트는 이를 자동 설치하지 않습니다. 가용성/비용 절충을 명시적으로 조정하고 검토되지 않은 비용 목표를 맞추려고 Prod를 단일 AZ로 축소하지 마세요.

## Memory와 개인 식별 정보

모든 배포는 각 대화를 AgentCore Memory에 원시 이벤트(단기 기억)로 보관합니다. 원시 이벤트는 `memory.eventExpiryDays`(기본 90일, 7–365일)가 지나면 만료됩니다. 장기 기억(`memory.longTerm`)은 기본적으로 꺼져 있습니다. 켜면 쇼핑객마다 선호도, 사실, 요약을 추출하므로 Prod에서는 개인정보 검토를 거친 뒤에만 켜세요. 파생 선호도/사실은 별도 기록이므로 원시 이벤트 만료가 그 기록의 삭제를 입증하지는 않습니다. 해시된 actor ID도 개인과 연결되므로 개인정보로 취급합니다.

시스템 프롬프트는 결제 자격 증명, 비밀번호, 상세 주소, 민감한 특성의 요청을 피하도록 합니다. 이는 행동 제어이며 DLP 보장이 아닙니다. 이벤트 보존 기간과 장기 기억/콘텐츠 수집 활성화 여부는 고객 동의, 데이터 분류, 허용 콘텐츠, 암호화 키 정책, 데이터 상주 위치, 보존 요건에 따릅니다. 글로벌 모델 추론은 별도의 데이터 상주 결정입니다.

삭제 의미:

- `DELETE /v1/sessions/{session_id}`는 소유한 Runtime 세션을 중지하고 모든 페이지의 원시 이벤트와 요약을 삭제하며 세션 간 선호도/사실은 유지합니다.
- `DELETE /v1/memory`는 해당 actor의 Memory 기록에서 발견 가능한 세션을 중지하고 기록을 삭제한 뒤 선호도/사실을 삭제합니다. 완료 메시지와 `verificationRequired=true`를 반환하며 응답에는 삭제 개수가 없습니다.
- 상위 실패는 오류로 반환하며 API가 완전한 삭제를 주장하지 않습니다. 진행 중인 비동기 추출이 파생 기록을 다시 만들거나 새/동시 세션이 다시 쓸 수 있습니다.
- Memory는 이벤트를 삭제한 세션도 계속 나열하므로 `GET /v1/sessions`는 이벤트가 하나도 남지 않은 세션을 건너뜁니다. 삭제는 Memory 자체의 세션 목록이 아니라 actor의 이벤트와 기록으로 확인하세요.

개인정보 삭제 요청에서는 먼저 신뢰 IdP/애플리케이션 접근 제어로 새 처리를 중단합니다. Actor의 세션을 조사하고 명시적 삭제를 완료한 뒤 진행 중인 추출이 마무리되기를 기다립니다. 올바른 범위의 네임스페이스로 원시/파생 기록을 다시 나열하고 필요하면 승인된 삭제를 반복합니다. 콘텐츠를 복사하지 않고 완료 증거를 기록하세요. 트레이스/로그 아카이브, 내보내기, 백업, 고객 백엔드도 각 보존 정책에 따라 처리합니다. 사용자 동의/철회 결정은 고객 개인정보 절차에서 보존합니다. 이 무상태 예제는 영속적인 삭제/철회 원장을 제공하지 않습니다.

스택이나 리소스를 제거해도 Prod는 Memory, OpenSearch 도메인, 카탈로그 버킷, Cognito 사용자 풀, API·Gateway Lambda·인덱싱 태스크·VPC 흐름 로그 그룹을 보존하고(`RemovalPolicy.RETAIN`), Dev는 카탈로그 버킷을 비운 뒤 이들을 삭제합니다. Runtime, WAF, OpenSearch 로그 그룹, 평가 결과 로그 그룹, 로드 밸런서 접근 로그 버킷, 도메인의 Nori 연결은 모든 스테이지에서 보존되며, Dev 도메인을 삭제하면 플러그인도 함께 사라집니다. 보존된 Memory는 이름(소문자 `<ns>_memory`, Dev 템플릿에서는 `ariadev_memory`)을 그대로 가지므로, 같은 네임스페이스에서 AgentRuntime 스택을 다시 만들면 보존된 Memory를 명시적으로 삭제하기 전까지 이름이 충돌합니다. 실제 스테이지에서 실행해 보지는 않았습니다. 리소스 보존은 복구 계획을 대신하지 않습니다.

## 카탈로그 런북

예제 카탈로그는 합성 데이터입니다. 실제 카탈로그 데이터는 고객 소유이며 저장소에 넣지 않습니다. 고객 데이터를 사용하기 전에 원본 소유권/라이선스, 갱신 주기, 검증, 접근, 복구 책임을 정합니다. 카탈로그 정의 작성은 [통합](integrations.ko.md#자체-카탈로그-사용)에서, 설계는 [카탈로그 검색](../design-docs/catalog-search.md)에서 다룹니다.

`opensearch` 공급자에서 스테이지의 카탈로그는 하나의 별칭 `tools.productSearch.openSearchIndex`(기본값 `products`) 뒤에 있는 버전 인덱스 `<alias>-<version>`에 들어 있습니다. VPC 안의 Fargate 태스크인 인덱싱 작업이 버전마다 인덱스를 만듭니다. `fixture`와 `http` 공급자는 인덱싱이 필요하지 않습니다. 배포는 도메인, 카탈로그 버킷, 태스크를 만들지만 인덱스는 만들지 않습니다. 첫 적재가 승격되기 전까지는 모든 검색이 실패하므로(`product_search_failed`), 스테이지를 처음 배포한 직후 카탈로그를 인덱싱하세요.

OpenSearch 자동 스냅샷만으로 검증된 재해 복구 절차가 되지는 않습니다. 교차 계정/리전 복구 요구를 별도로 정의하고 스냅샷 성공을 검증하며 격리된 목적지로 복원 훈련을 수행합니다. 운영 전에 측정한 RPO/RTO를 기록하세요.

### 카탈로그 인덱싱

저장소 루트에서 스테이지의 검토된 설정으로 실행합니다.

```sh
ARIA_CONFIG=/absolute/path/to/reviewed.toml sh scripts/index_catalog.sh --catalog /absolute/path/catalog.jsonl --version v2
ARIA_CONFIG=/absolute/path/to/reviewed.toml sh scripts/index_catalog.sh --catalog /absolute/path/catalog.jsonl --version v2 --apply
```

| 인자 | 의미 |
| --- | --- |
| `--catalog` | 로컬 JSON 배열 또는 JSONL 파일, 또는 `s3://<catalog bucket>/catalog/<key>`. 태스크는 이 접두사만 읽을 수 있습니다 |
| `--version` | 새 인덱스는 `<alias>-<version>`입니다. 소문자, 숫자, 하이픈으로 이루어진 최대 63자이며 문자나 숫자로 시작합니다. 이미 있는 버전이면 안 됩니다 |
| `--replicas` | 샤드당 복제본 수, 0~2. 기본값은 스택의 `IndexReplicas` 출력으로, 단일 노드 Dev는 0, Prod는 1입니다. 스크립트는 `--apply`에서 이 값을 읽으므로, 값을 넘기지 않은 드라이 런의 매니페스트는 `null`을 보고합니다 |
| `--apply` | 쓰기를 수행합니다. 없으면 아무것도 쓰지 않습니다 |

- **드라이 런(기본값):** 로컬 파일은 배포와 같은 로더가 읽은 스테이지의 카탈로그 정의로 운영자 컴퓨터에서 검증하며 AWS를 호출하지 않습니다. `s3://` 카탈로그는 태스크가 검증하고 아무것도 쓰지 않습니다. 매니페스트에는 읽은 상품, 승인 및 거부 개수, 필드별 거부 사유(`rejectedReasons`), 원본 SHA-256, 인덱스와 파이프라인 이름, 적재에 필요한 임베딩 호출 수, 문자 수, 벡터 바이트가 담깁니다. 거부된 상품은 인덱스에서 빠지므로 적용하기 전에 고치세요.
- **`--apply`:** 정의가 `nori`를 쓰면 스크립트는 먼저 도메인의 Nori 플러그인이 `ACTIVE`인지 확인합니다. 연결은 배포 뒤 도메인의 블루/그린 배포로 끝나므로 `ASSOCIATING`인 동안에는 기다렸다가 다시 시도하세요. 그다음 로컬 파일을 `s3://<bucket>/catalog/<version>/<file>`에 올리고, 스테이지의 ECS 클러스터와 프라이빗 서브넷에서 태스크 전용 보안 그룹으로 태스크를 실행하며(`aws ecs run-task`), 태스크가 멈출 때까지 기다립니다. 태스크는 카탈로그를 다시 검증하고, 인덱스를 만들고, 상품을 96개씩 임베딩해 적재하고, 개수를 확인하고, 검색 파이프라인 `<alias>-hybrid`를 넣은 뒤 별칭을 옮깁니다.
- **결과:** 스크립트는 태스크의 매니페스트를 JSON 한 줄로 stdout에 출력하고 태스크의 종료 코드로 끝납니다. 승격된 적재에는 `"applied": true`, `"promoted": true`, 그리고 별칭이 떠난 인덱스를 담은 `previousIndices`가 있습니다. 실패한 적재에는 단계와 오류 유형을 밝히는 `error`가 있으며 상품 내용은 담기지 않습니다. `created`, `loaded`, `indexed`로 어디까지 진행했는지 알 수 있습니다. 태스크 로그는 스택의 `IndexerLogGroup`, 스트림 `indexer/indexer/<task ID>`에 있습니다.
- **시작하기 전에:** 스크립트에는 AWS CLI, `uv`, `python3`이 필요하며, 공급자가 `opensearch`가 아니거나 배포된 스택의 별칭이 설정과 다르면 중단합니다. 운영자 자격 증명은 `Opensearch` 스택 출력(`cloudformation:DescribeStacks`)과 Nori 상태(`es:ListPackagesForDomain`)를 읽고, 버킷의 `catalog/` 접두사에 쓰고(`s3:PutObject`), 태스크를 실행하고 지켜보며(`ecs:RunTask`, `ecs:DescribeTasks`, 태스크 역할과 실행 역할에 대한 `iam:PassRole`), 로그를 읽습니다(`logs:GetLogEvents`). 인덱싱 자체는 태스크의 역할이 수행합니다.

승격된 적재 뒤에는 필터만 쓰는 검색, 자유 텍스트 쿼리, 그리고 정의가 Nori를 쓰면 한국어 쿼리로 대표 검색을 실행하세요.

### 승격, 롤백, 정리

- **승격:** 마지막 단계는 `_aliases` 호출 한 번으로 별칭을 기존 인덱스에서 새 인덱스로 옮기므로 검색이 한 번에 바뀝니다.
- **롤백:** 이전 원본을 새 버전으로 다시 인덱싱합니다. 또는 매니페스트의 `previousIndices`에 있는 이전 인덱스로 별칭을 되돌립니다. 인덱스 관리 권한이 있는 VPC 내부의 승인된 환경(예: 도메인 보안 그룹으로 가는 자체 TCP 443 규칙이 있는 프라이빗 서브넷의 인스턴스)에서 서명된 요청 하나를 보냅니다.

  ```http
  POST /_aliases
  {"actions": [{"remove": {"index": "products-v3", "alias": "products"}}, {"add": {"index": "products-v2", "alias": "products"}}]}
  ```

- **실패 후:** 승격 전에 실패하면 별칭은 그대로이므로 쇼핑객은 현재 버전을 계속 씁니다. 태스크가 만든 인덱스는 적재된 만큼을 담은 채 승격되지 않고 남습니다. 태스크는 인덱스를 삭제할 수 없으므로, 같은 종류의 승인된 환경에서 변경 승인을 받아 승격되지 않은 인덱스를 삭제한 뒤(`DELETE /<alias>-<version>`) 새 버전으로, 또는 인덱스를 지운 같은 버전으로 다시 인덱싱하세요.
- **이전 버전**은 남습니다. 태스크도 에이전트도 인덱스를 삭제하지 않습니다. 롤백을 위해 최소한 이전 정상 버전은 유지하고, 더 오래된 버전은 저장/보존 정책에 따라 삭제하세요. 여유 공간이 10 GiB 아래로 내려가면 `SearchDiskLow`가 경보를 보냅니다.
- **별칭과 같은 이름의 실제 인덱스:** 태스크는 아무것도 쓰기 전에 중단합니다. `tools.productSearch.openSearchIndex`를 `catalog-products` 같은 다른 별칭으로 바꿔 배포한 다음 그 별칭으로 인덱싱하세요. 에이전트의 검색 도구와 태스크의 역할은 이 설정을 따릅니다.

### 카탈로그 정의 변경

배포는 Runtime과 인덱싱 태스크에 새 정의를 함께 전달하지만, 인덱스는 만들 때의 매핑과 상품을 그대로 유지합니다.

- **새 인덱스 없이:** 설명, 매개변수 이름, boost, `[catalog.search].k`, 필터나 그 값의 제거는 도구와 쿼리만 바꿉니다. 배포와 함께 쇼핑객에게 반영됩니다.
- **새 인덱스가 필요한 경우:** 필드, 타입, 필터에 추가한 값(그 값의 상품은 거부되었습니다), 카드, 분석기, 임베딩 텍스트나 모델 등 그 밖의 변경에는 새 버전이 필요합니다. 배포한 뒤 바로 다시 인덱싱하세요([카탈로그 인덱싱](#카탈로그-인덱싱)). 새 버전이 승격될 때까지 검색은 이전 인덱스를 대상으로 하므로, 인덱스에 없는 필드의 필터는 아무것도 찾지 못하고 인덱스에 없는 카드 필드가 있으면 그 상품은 제외됩니다.
- **`http`:** 배포 이후 고객 API는 정의가 정한 이름으로 새 매개변수를 받습니다.
- **분석기:** `nori`를 쓰면 도메인에 플러그인이 추가되며 블루/그린 배포가 실행되므로, 이 변경은 따로 계획하세요. 끄더라도 플러그인 연결은 남으므로 Nori로 분석한 버전은 계속 검색과 롤백에 쓰입니다. 플러그인을 없애려면 그 인덱스들을 삭제한 뒤 직접 연결을 해제하세요(`aws opensearch dissociate-package`). 인덱스가 아직 쓰고 있으면 서비스가 거부합니다.

## 자격 증명 교체

| 자격 증명 | 위치 | 교체 방법 | 교체 후 확인 |
| --- | --- | --- | --- |
| Gateway 대상의 OAuth 클라이언트 시크릿 | AgentCore Identity OAuth2 공급자(대상의 `providerArn`) | `aws bedrock-agentcore-control update-oauth2-credential-provider --name <provider> --credential-provider-vendor CustomOauth2 --oauth2-provider-config-input file://<new-config>.json`. ARN이 그대로이므로 다시 배포할 필요가 없습니다 | 해당 대상 도구 호출([실제 환경 검사](testing.ko.md#배포-후-실제-환경-검사)) |
| Gateway 대상의 API 키 | AgentCore Identity API 키 공급자 | `{"name": "<provider>", "apiKey": "<new key>"}`로 `aws bedrock-agentcore-control update-api-key-credential-provider --cli-input-json file://<file>.json`. ARN은 그대로이며 재배포가 필요 없습니다 | 위와 같음 |
| HTTP 카탈로그 자격 증명 | Secrets Manager(`tools.productSearch.secretArn`) | 새 값을 새 시크릿 버전으로 저장합니다. 실행 중인 컨테이너가 5분 안에 다시 읽으므로 그동안 이전 키를 유효하게 유지하세요 | 검색 턴 |
| 에이전트 클라이언트의 Cognito 시크릿 | Auth 스택 배포 시 에이전트 클라이언트에서 읽어 `<ns>-gateway` 공급자에 저장 | 교체하려면 검토된 배포로 새 에이전트 클라이언트를 만들어야 하며, 인터셉터와 Gateway 인가자가 그 클라이언트 ID를 씁니다. 먼저 스테이지에서 계획하고 테스트하세요. 아직 실행해 보지 않았습니다 | 실제 환경 검사 |
| IdP 서명 키(BYO OIDC) | IdP의 JWKS | 새 키로 서명하기 전에 먼저 게시합니다. API는 키를 5분 동안 캐시합니다 | 로그인과 턴 |

시크릿은 명령줄이 아니라 파일에 두고, 사용 후 파일을 삭제하세요.

## 도구 설정과 문제 해결

`packages/mcp-servers`의 Gateway Lambda 두 개는 `packages/infra/stacks/gateway_stack.py`에서 환경 변수를 받습니다. 에이전트의 로컬 상품 검색은 `packages/infra/stacks/agent_runtime_stack.py`가 설정한 AgentCore Runtime 환경 변수에서 카탈로그 설정을 읽습니다. 어느 것도 `.env`를 읽거나 서버를 시작하지 않습니다. 인덱싱 태스크는 `packages/infra/components/catalog_indexer.py`에서 설정을, `scripts/index_catalog.sh`에서 인자를 받습니다([카탈로그 런북](#카탈로그-런북)).

### 상품 검색 환경 변수(Runtime)

| 변수 | CDK 원본 / 용도 |
| --- | --- |
| `SEARCH_PROVIDER` | `tools.productSearch.provider`: `fixture`, `opensearch`, `http`. `disabled`이면 도구를 제거 |
| `CATALOG_DEFINITION`, `CATALOG_DEFINITION_2`, … | 카탈로그 정의(`catalogFile` 또는 패키지에 포함된 예제)를 압축한 JSON. 합성 시 검증하며, 최대 2,048바이트인 값(최대 8개)으로 나뉘어 순서대로 합쳐집니다. 도구 스키마, 인자 검사, 쿼리, 카드가 여기서 만들어지며 `disabled`이면 빈 값 |
| `SEARCH_ENDPOINT` | `endpoint`: HTTP 공급자의 HTTPS 카탈로그 URL |
| `SEARCH_SECRET_ARN` | `secretArn`: HTTP 어댑터의 선택적 Secrets Manager 자격 증명 |
| `SEARCH_RESULT_PATH` | `resultPath`: 상품 배열까지의 점 구분 응답 경로, 기본 `products` |
| `OPENSEARCH_HOST` | 관리형 도메인 엔드포인트. 도메인을 선택하지 않으면 빈 값 |
| `OPENSEARCH_PRODUCT_INDEX` | `openSearchIndex`, 기본 `products`: 검색하는 별칭. 검색 파이프라인은 `<alias>-hybrid` |
| `AWS_REGION` | Runtime이 제공. Runtime 밖에서는 `AWS_REGION`이나 `AWS_DEFAULT_REGION`을 설정하세요. 둘 다 없으면 카탈로그의 AWS 클라이언트(OpenSearch, Bedrock, Secrets Manager)가 실패합니다 |

HTTP 어댑터는 `header`(`Authorization` 또는 `x-api-key`)와 `value`가 있는 시크릿 JSON 값을 받습니다. 자격 증명을 5분 동안 캐시하고 401이나 403 응답을 받으면 한 번 다시 가져오며, 리디렉션을 거부하고, 10초 제한 시간을 사용하고, 응답을 256 KiB와 상품 20개로 제한합니다. 자격 증명과 URL은 설정 값이며 모델 인자가 될 수 없습니다. [통합 계약](integrations.ko.md#공급자-계약)을 참고하세요.

인덱싱 태스크는 Runtime과 같은 `CATALOG_DEFINITION`과 그 뒤의 값, 그리고 `OPENSEARCH_HOST`, `OPENSEARCH_PRODUCT_INDEX`, `AWS_REGION`을 받습니다.

Runtime은 `gateway.agentAuth`에서 계산한 에이전트의 Gateway 토큰 설정 `GATEWAY_AUTH_FLOW`, `GATEWAY_CREDENTIAL_PROVIDER`, `GATEWAY_TOKEN_SCOPES`, `GATEWAY_TOKEN_AUDIENCE`도 받습니다. 토큰 흐름은 [신원과 토큰](../design-docs/identity-and-tokens.md)을 참고하세요.

### 인터셉터 환경 변수

| 변수 | CDK 원본 / 용도 |
| --- | --- |
| `LOG_LEVEL` | `INFO` |
| `FORWARD_HEADERS` | 쉼표로 구분한 `gateway.forwardHeaders` 이름 |
| `OIDC_TENANT_CLAIM` | `auth.tenantClaim`: 에이전트와 똑같은 방식으로 on-behalf-of 토큰에서 actor ID를 계산할 때 사용 |

인터셉터는 모든 도구 호출에 올바른 형식의 `x-aria-actor-id`를 요구하고, on-behalf-of 토큰이면 토큰 주체와 일치해야 합니다. `Authorization`은 절대 전달하지 않으며, 사용자 지정 전달로 `Authorization`, `auth`, 대체 `x-aria-actor-id`를 보낼 수 없습니다. 검사 실패나 예외가 발생하면 요청을 거부합니다. Gateway 인터셉터 설정은 `pass_request_headers=True`를 지정합니다.

### Saved-items 환경 변수

| 변수 | CDK 원본 / 용도 |
| --- | --- |
| `LOG_LEVEL` | `INFO` |

예제에는 다른 설정이 필요하지 않습니다. `gateway.savedItemsSample`이 true일 때만 생성합니다.

### 패키징, 네트워크, 권한

두 Gateway Lambda 모두 Python 3.13, ARM64, `handler.lambda_handler`, 활성 추적을 사용하며 VPC에 연결하지 않습니다. 각 함수는 Gateway 실행 역할에 호출 권한을 부여합니다.

- **Saved-items 예제:** 예약 동시성 10, 제한 시간 10초, 256 MiB. 자산에는 `saved-items-lambda/src/`와 `packages/catalog/src/aria_catalog`만 들어가고 해시도 이 폴더만 대상으로 하므로, 관련 없는 패키지 변경으로 다시 배포되지 않습니다. 로컬 번들링이 Docker 없이 이 폴더를 복사합니다.
- **인터셉터:** 번들링 단계 없이 `src/`의 표준 라이브러리 코드만 패키징합니다. 예약 동시성 20, 제한 시간 10초, 256 MiB입니다.
- **Runtime 네트워크:** Runtime은 항상 자체 보안 그룹을 가지고 VPC의 프라이빗 서브넷에서 실행됩니다. 관리형 VPC에서는 `vpc.interfaceEndpoints`에 나열한 인터페이스 엔드포인트로 AWS 서비스에 접근하고(기본값은 ECR과 CloudWatch Logs이며, Bedrock과 SSM은 `bedrock`과 `ssm`을 추가합니다), S3에는 게이트웨이 엔드포인트로 접근합니다. 나머지는 모두 VPC의 NAT 게이트웨이를 거칩니다. Gateway, AgentCore Identity와 Memory, Secrets Manager, 공개 HTTP 카탈로그, 엔드포인트가 없는 AWS 서비스가 여기에 해당합니다. 기존 VPC(`vpc.vpcId`)에도 같은 경로가 필요합니다.
- **가용 영역:** AgentCore는 리전마다 일부 AZ ID에서만 VPC 연결을 지원하며, 그 밖의 서브넷으로는 Runtime 생성이 실패합니다. 사전 점검이 Runtime 서브넷의 AZ ID를 확인합니다([릴리스](release.ko.md#읽기-전용-사전-점검)).
- **네트워크 인터페이스:** AgentCore는 Runtime이 삭제된 뒤에도 그 네트워크 인터페이스를 최대 8시간 유지합니다. 인터페이스가 사라질 때까지 CloudFormation은 Runtime 보안 그룹과 인터페이스가 있는 서브넷을 삭제할 수 없으므로 스택 삭제가 실패합니다(`DELETE_FAILED`). 인터페이스가 해제된 뒤 스택을 다시 삭제하세요.
- **상품 검색, OpenSearch 공급자:** 검색은 에이전트에서 실행되고, 도메인은 VPC에서만 접근할 수 있습니다. 명시적 TCP 443 규칙이 Runtime 보안 그룹에서 도메인 보안 그룹으로의 접근을 허용합니다. Runtime 역할은 별칭의 `_search` 경로에 대한 `es:ESHttpPost`/`es:ESHttpGet`과, 쿼리 임베딩을 위한 정의의 임베딩 모델에 대한 `bedrock:InvokeModel`만 수행할 수 있습니다. `vpc.interfaceEndpoints`에 `bedrock`을 추가하지 않으면 Bedrock에는 NAT 게이트웨이로 접근합니다.
- **인덱싱 태스크:** Fargate, ARM64, 1 vCPU와 4 GiB, 읽기 전용 루트 파일 시스템이며, 공개 IP 없이 프라이빗 서브넷에서 실행됩니다. 전용 보안 그룹에는 도메인 보안 그룹으로 가는 TCP 443 규칙 하나만 있고 인바운드는 없습니다. 역할은 카탈로그 버킷의 `catalog/` 접두사만 읽고, 임베딩 모델만 호출하며, 별칭, `<alias>-*`, `_aliases`, 파이프라인에만 GET, HEAD, PUT, POST를 보낼 수 있고 삭제는 할 수 없습니다. ECR, CloudWatch Logs, S3에는 VPC 엔드포인트로 접근하고, `vpc.interfaceEndpoints`에 `bedrock`을 추가하지 않으면 Bedrock에는 NAT 게이트웨이로 접근합니다.
- **카탈로그 버킷:** 모든 퍼블릭 액세스 차단, TLS 전용, S3 관리형 암호화, 버전 관리를 쓰는 비공개 버킷이며 접근 로그 버킷은 없습니다. 운영자가 스크립트로 쓰고, 인덱싱 태스크의 역할만 읽습니다.
- **상품 검색, fixture/HTTP 공급자:** 도메인이 필요하지 않습니다. HTTP 카탈로그는 VPC에서 접근할 수 있어야 하며, 공개 카탈로그는 NAT 게이트웨이를 거칩니다. 설정한 시크릿에는 Runtime 역할의 리소스별 읽기 권한이 부여됩니다.
- **Gateway 인가자:** 에이전트 자체 토큰만 받습니다. `on_behalf_of`이면 도구용 audience(`aud`)과 스코프를, `m2m`이면 에이전트 클라이언트 ID와 스코프를 요구합니다. 쇼핑객의 토큰은 거부됩니다.
- **대상:** 모든 대상의 `allowedRequestHeaders`는 `x-aria-actor-id`와 `x-correlation-id`를 먼저, 설정한 전달 헤더를 그 뒤에 나열합니다(모두 합쳐 최대 10개).

### 검증과 배포

저장소 루트에서 오프라인 검사를 실행합니다:

```sh
pnpm check
```

`ARIA_CONFIG`로 검토된 TOML을 선택합니다. 활성 `.toml`을 템플릿으로 덮어쓰지 마세요. 변경 사항이 커밋된 깨끗한 체크아웃에서 별도로 승인된 계획 단계가 읽기 전용 AWS 검사와 로컬 합성을 수행합니다:

```sh
ARIA_CONFIG=/absolute/path/to/reviewed.toml sh scripts/plan_deployment.sh
```

`ARIA_ALLOW_DEPLOY=yes`로 `scripts/deploy.sh`를 승인하기 전에 저장된 어셈블리, diff, 설정 해시를 검토합니다. [릴리스](release.ko.md)를 따르세요. 승인 절차를 우회하는 CDK 명령을 직접 대신 사용하지 마세요. bootstrap과 리소스 가져오기는 별도 운영자 작업입니다.

### 로그와 문제 해결

CDK는 보존 기간 1개월인 `SavedItemsLogGroup`과 `InterceptorLogGroup`을 생성합니다. 스택을 제거하면 Dev는 이를 삭제하고 Prod는 보존합니다(`RemovalPolicy.RETAIN`). 두 핸들러 모두 표준 `logging`과 `LOG_LEVEL=INFO`를 사용합니다. 예제는 actor ID 접두부와 결과 개수를, 인터셉터는 메서드와 전달 헤더 이름을 기록합니다. 일반 메시지에는 헤더 값이나 토큰을 기록하지 않습니다. 상품 검색은 Runtime 로그 그룹에 결과 개수와 소요 시간, 임베딩 대체 실행과 제외된 상품의 개수, 추적 정보가 포함된 실패를 기록하며, 인자 값이나 상품은 기록하지 않습니다. 예외 추적 정보에는 접근 제한이 필요합니다.

사용자 지정 상태 확인 엔드포인트는 없습니다. Lambda Invocations, Errors, Duration, Throttles, 로그, 승인된 전체 도구 호출을 확인하세요.

| 증상 | 확인 항목 |
| --- | --- |
| 빈 성공 검색 결과 | 선택한 공급자, 실제 fixture/인덱스 내용, 필터. 조건이 없으면 백엔드 호출 없이 빈 목록 반환. 정의를 바꾼 뒤에는 다시 인덱싱할 때까지 인덱스에 없는 필드의 필터가 아무것도 찾지 못함 |
| 검색이 잘못된 값 오류 반환 | 모델이 정의에 선언되지 않은 값이나 도구에 없는 매개변수를 보냈습니다. 오류에 허용되는 항목이 나열되므로 모델이 호출을 고칠 수 있습니다. 계속 발생하면 도구 스키마와 정의의 설명을 확인 |
| OpenSearch 연결 실패 | Runtime에서 도메인으로의 443 규칙, `OPENSEARCH_HOST`, 정확한 `_search` IAM 권한 |
| 배포 직후 모든 검색이 실패 | OpenSearch에서는 첫 적재가 승격되기 전까지 별칭에 인덱스가 없음: [카탈로그 인덱싱](#카탈로그-인덱싱) |
| `product_search_embedding_failed`, `SearchEmbeddingFailures` | 검색이 키워드로만 실행됨. `error_type`이 원인을 알려 줌: 임베딩 모델의 모델 액세스, Runtime 역할의 `bedrock:InvokeModel` 권한, Bedrock throttling, Bedrock으로 가는 경로(NAT 게이트웨이 또는 `bedrock` 엔드포인트) |
| 인덱싱 태스크 실패 | 매니페스트의 `error`와 태스크 로그: `ACTIVE`가 아닌 Nori 연결, Bedrock 액세스나 throttling, 거부된 문서(오류 유형만), 개수 불일치. 별칭은 그대로이므로 승격되지 않은 인덱스를 삭제 ([실패 후](#승격-롤백-정리)) |
| 에이전트가 Gateway 토큰을 받지 못함 | `GATEWAY_CREDENTIAL_PROVIDER`와 Runtime 역할의 `GetResourceOauth2Token` 권한. 관리형 Cognito on-behalf-of에서는 웹 앱이 `aria-tools/invoke`를 요청해야 하고(`OIDC_SCOPES`) 사용자 풀 도메인이 관리형 로그인을 사용해야 함. [신원과 토큰](../design-docs/identity-and-tokens.md) 참고 |
| Gateway가 401 또는 403 반환 | 발급된 토큰의 흐름/대상/스코프와 Gateway 인가자(`gateway.agentAuth`) 비교. 토큰 없이 호출하면 401. 쇼핑객 자신의 토큰은 항상 403으로 거부됨 |
| 인터셉터가 도구 호출 거부 | `x-aria-actor-id` 누락 또는 불일치. 에이전트는 모든 Gateway 요청에 이 헤더를 보내므로 `OIDC_TENANT_CLAIM`과 인터셉터 로그를 확인 |
| 대상에 actor나 사용자 지정 헤더가 없음 | 대상의 `allowedRequestHeaders`, `pass_request_headers=True`, Runtime 허용 목록, 인터셉터 허용 목록. API는 사용자 지정 헤더를 전달하지 않음 |
