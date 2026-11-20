# 출시 설정

[English](launch-settings.md)

쇼핑객이 스테이지를 쓰기 전에 스테이지 소유자가 정해야 하는 설정을, 무엇을 정하는지에 따라 묶었습니다. 모두 스테이지 `.toml`의 키입니다. Prod 템플릿 `packages/infra/config/prod.toml`에서 시작하세요. 모든 키의 기본값과 검사는 [설정 레퍼런스](../generated/config-reference.md)에 있습니다. `Prod` 스테이지는 설정과 상관없이 강제하는 것도 있고([Prod가 강제하는 것](#prod가-강제하는-것)), 설정이 아예 없는 결정도 몇 가지 있습니다([설정으로 정할 수 없는 결정](#설정으로-정할-수-없는-결정)).

## 누가 접근할 수 있는지

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `api.allowIpList` | 로드 밸런서가 받아들이는 네트워크 | `127.0.0.1/32` | 자리표시자 `/24` | 공개 스토어프런트에는 `api.allowPublicIngress`와 함께 `0.0.0.0/0`(과 `::/0`)이 필요합니다 |
| `api.allowPublicIngress` | Prod가 /8(IPv6: /32)보다 넓은 네트워크를 받아들이는지 | `false` | `false` | 공개 스토어프런트라면 `true`. 이때는 WAF와 IP별 속도 제한이 경계가 됩니다 |
| `api.wafRateLimit` | WAF가 차단하기 전에 IP 하나가 5분 동안 보낼 수 있는 요청 수 | 100 | 100(기본값) | NAT 하나 뒤의 쇼핑객은 IP를 공유하며, 쇼핑객별 제한은 없습니다 |
| `api.certificateArn`, `api.corsAllowOrigins` | 443 포트의 HTTPS와, API가 응답하는 스토어프런트 출처 | 없음, `http://localhost:4000` | 자리표시자 | 인증서는 발급된 상태이고 스테이지 리전에 있어야 하며, 사전 점검이 둘 다 확인합니다 |

## 로그인

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `auth.provider` | 관리형 Cognito 또는 고객 OIDC IdP | `cognito` | `cognito` | `oidc`에서는 MFA, 로그인 화면, 위협 보호가 고객 IdP의 몫입니다 |
| `auth.mfa` | 인증 앱을 쓰는 다단계 로그인(관리형 Cognito) | `optional` | `optional` | `optional`은 인증 앱을 등록한 쇼핑객에게만 묻고, 등록을 안내하는 곳은 없습니다. `required`는 모든 쇼핑객이 로그인할 때 등록하게 합니다 |
| `auth.domainPrefix` | 관리형 로그인 도메인 `<prefix>.auth.<region>.amazoncognito.com` | 비어 있음: `<namespace>-<account ID>` | 비어 있음 | 첫 배포 전에 정하세요. 배포 후에는 바꿀 수 없고, 기본값에는 계정 ID가 보입니다 |
| `auth.callbackUrls`, `auth.logoutUrls` | 관리형 로그인이 쇼핑객을 돌려보내는 곳 | `localhost:4000` | 자리표시자 | 스토어프런트의 HTTPS URL |
| `auth.tenantClaim` | actor ID에서 테넌트를 나누는 클레임 | 비어 있음 | 비어 있음 | 나중에 바꾸면 모든 쇼핑객의 actor ID가 바뀌어 이전 기록을 찾지 못합니다 |

## 카탈로그와 데이터 상주

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `tools.productSearch.provider` | 검색이 카탈로그를 읽는 곳 | `fixture` | `http` | `opensearch`는 VPC에 도메인과 인덱싱 작업을 만들고, `http`는 고객 API를 호출하며, Prod는 `fixture`를 거부합니다 |
| `tools.productSearch.catalogFile` | 고객 카탈로그 정의 | 예제 | 예제 | 모든 카탈로그가 패션 형태의 카드 필드에 매핑해야 하며, 정의는 최대 16KB입니다 |
| `tools.productSearch.endpoint`, `secretArn` | `http`에서 쓰는 고객 검색 API와 그 자격 증명 | 없음 | 자리표시자 | 시크릿에는 헤더와 그 값이 들어갑니다 |
| 정의의 `[catalog.embedding].model` | `opensearch`에서 쓰는 Embed v4 모델 또는 추론 프로필 | `global.cohere.embed-v4:0` | 같음 | `global`은 카탈로그 텍스트와 쇼핑객 쿼리를 리전 밖에서 처리할 수 있습니다. 서울에서는 이것만 쓸 수 있습니다 |

## 안전

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `guardrail.mode` | 가드레일이 차단하는지, 보고만 하는지 | `ENFORCE` | `ENFORCE` | Prod는 `ENFORCE`가 필요합니다. 먼저 Dev에서 `AUDIT`로 측정하세요 |
| `guardrail.streamProcessing` | 응답을 언제 검사하는지 | `async` | `sync` | `sync`는 스트리밍 전에 검사하고, `async`는 더 빨리 답하지만 응답이 잘리기 전에 잠깐 보일 수 있습니다 |
| `guardrail.screenToolResults` | 모델이 읽기 전에 Gateway 도구 결과를 검사하는지 | `true` | `true` | 정상 결과를 막을 수 있고, throttling되면 막는 쪽으로 실패합니다 |
| `policy.enabled`, `policy.mode` | Gateway 호출에 대한 Cedar | 꺼짐, `LOG_ONLY` | 켜짐, `ENFORCE` | 모든 대상에 `policyFile`이 필요합니다. 먼저 Dev에서 `LOG_ONLY`로 시험하세요 |
| `runtime.maxToolCalls`, `runtime.maxSessionTurns` | 턴 하나의 도구 호출 수와 대화 하나의 턴 수 | 8, 100(기본값) | 8, 100(기본값) | 어느 쪽이든 넘으면 에이전트가 거부합니다 |

## 비용과 용량

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `runtime.modelId`, `runtime.maxTokens` | 모델과 가장 긴 응답 | `global.openai.gpt-5.6-luna`, 2048 | 같음, 2048(기본값) | 리전 간 프로필은 프롬프트를 리전 밖에서 처리할 수 있으며, 대체 모델은 없습니다 |
| `api.minTasks`, `api.maxTasks` | API 태스크 수 | 1~3(기본값) | 2~6 | Prod는 최소 2개가 필요합니다 |
| `vpc.vpcId`, `vpc.natGateways`, `vpc.interfaceEndpoints` | 고객 VPC 또는 관리형 VPC, 그 NAT 게이트웨이와 엔드포인트 | 관리형, NAT 1개, `ecr`, `ecr-docker`, `logs` | 관리형, NAT 3개(기본값) | `bedrock`을 추가하면 모델과 임베딩 호출이 NAT 게이트웨이를 거치지 않습니다 |
| `evaluations.enabled`, `evaluations.samplingPercentage` | 온라인 평가와, 평가하는 세션의 비율 | 꺼짐 | 꺼짐 | 평가한 세션마다 판정 모델 토큰이 들고, `operations.captureContent`가 필요합니다 |

## 개인정보

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `memory.longTerm` | 쇼핑객별로 남기는 파생 사실과 선호 | 꺼짐 | 꺼짐 | 개인정보 검토 뒤에만 켜세요 |
| `memory.eventExpiryDays` | 대화 이벤트를 보관하는 기간 | 90 | 90(기본값) | 7~365 |
| `operations.captureContent` | 트레이스에 남는 프롬프트와 응답 | 꺼짐(기본값) | 꺼짐(기본값) | 쇼핑객의 텍스트가 트레이스 로그에 남습니다 |

## 운영

| 설정 | 정하는 것 | Dev 템플릿 | Prod 템플릿 | 고려할 점 |
| --- | --- | --- | --- | --- |
| `operations.alarmTopicArn` | 경보와 복구 알림을 보낼 곳 | 없음 | 자리표시자 | Prod는 필수입니다 |
| `app.account`, `app.region` | 스테이지를 배포할 곳 | AWS CLI 설정 | 명시 | Prod는 둘 다 필요합니다. 리전에 따라 쓸 수 있는 Embed v4 프로필, Nori 패키지, AgentCore 가용 영역이 달라집니다 |

## Prod가 강제하는 것

`Prod` 스테이지는 설정과 상관없이 다음을 적용합니다.

- HTTPS, 명시적인 HTTPS CORS 출처와 로그인 URL, API 태스크 2개 이상, 경보 토픽, AZ마다 NAT 게이트웨이 하나.
- 켜져 있고 `ENFORCE`인 가드레일, 관리형 Gateway에서는 `ENFORCE`인 Cedar. fixture 카탈로그와 saved-items 예제는 쓸 수 없습니다.
- 위협 보호를 포함한 Cognito Plus 요금제(월간 활성 사용자당 과금)와 사용자 풀 삭제 보호.
- 모든 스택의 종료 보호. Memory, 사용자 풀, OpenSearch 도메인, 버킷, 로그 그룹은 스택이나 리소스를 제거해도 남습니다.
- `opensearch`에서는 세 AZ에 걸친 `r6g.large` 데이터 노드 3개, 전용 마스터 3개, 복제본 1개로 이뤄진 도메인.

## 설정으로 정할 수 없는 결정

자세한 내용은 [기술 부채 목록](../exec-plans/tech-debt-tracker.md)에 있습니다.

- Gateway 연결이나 토큰 발급에 실패한 세션은 시작되지 않으며, 로컬 검색도 함께 멈춥니다(#1).
- 모든 턴을 모델 하나가 처리하므로, Bedrock에서 그 모델이 실패하면 모든 턴이 실패합니다(#10).
- Runtime은 카나리 없이 최신 버전을 제공합니다. 롤백하려면 이전 릴리스를 다시 배포해야 합니다(#2).
- 데이터를 바꾸는 Gateway 도구는 쇼핑객의 확인 없이 실행됩니다. 기본 제공 도구는 읽기만 합니다(#3).
- 쇼핑객의 액세스 토큰으로 API의 WAF와 검사를 거치지 않고 Runtime을 직접 호출할 수 있습니다(#4).
- 쇼핑객별 할당량이 없어, 계정 하나가 스테이지의 모델 예산을 다 쓸 수 있습니다(#5).
- 합성 카나리가 없어, 턴 사이에 생긴 장애는 누군가 대화하기 전까지 드러나지 않습니다(#19).
