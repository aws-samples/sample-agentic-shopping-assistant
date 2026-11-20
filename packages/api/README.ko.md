# NORDLY API

[English](README.md)

FastAPI는 보호된 작업마다 JWT 액세스 토큰을 검증하고 비동기 bearer HTTP로 AgentCore Runtime을 호출합니다. AgentCore Memory 기록 조회/삭제 SDK 호출은 작업 스레드에서 실행합니다. JWT Runtime의 사용자 호출에는 IAM 호출 권한이 필요하지 않습니다.

## 설정

API는 ApiService 스택의 ECS 서비스로만 실행되며, 이 스택이 선택한 `.toml`로 아래 변수를 설정합니다. `src/config.py`는 시작 시 설정을 검증합니다. `AGENT_RUNTIME_ARN`에는 Runtime ARN, `OIDC_ISSUER`에는 정확한 HTTPS issuer를 설정하고, `OIDC_AUDIENCES` 또는 `OIDC_ALLOWED_CLIENTS` 중 하나 이상을 지정합니다. `OIDC_SCOPES`는 공백으로 구분한 목록이며 기본값은 `aria/chat`입니다. audience와 허용 클라이언트는 쉼표로 구분합니다. `AUTH_PROVIDER`는 `cognito`(기본값) 또는 `oidc`이며, `AWS_REGION` 기본값은 `ap-northeast-2`입니다.

선택 설정: `OIDC_TENANT_CLAIM`, `CORS_ALLOW_ORIGINS`(쉼표 구분, 기본 `http://localhost:4000`), `REQUEST_TIMEOUT_SECONDS`(10–300, 기본 120), `AGENT_RUNTIME_QUALIFIER`(기본 `DEFAULT`), `PORT`(기본 8000). 스택은 세션 기록을 보관하는 Memory의 ID인 `AGENTCORE_MEMORY_ID`를 항상 설정합니다. 이 값이 없으면(배포 밖에서) 세션 목록과 기록은 비어 있고 `DELETE /v1/memory`는 아무것도 삭제하지 않습니다. Runtime 또는 ID 설정이 없거나 잘못되면 시작에 실패합니다. 호출 라우트에도 Runtime ARN이 없을 때 503을 반환하는 방어 검사가 있습니다.

## 엔드포인트와 스트리밍

| 메서드 | 경로 | 동작 |
| --- | --- | --- |
| GET | `/ping` | 프로세스 상태 확인. 하위 통합은 검사하지 않음 |
| POST | `/v1/sessions` | 인증 주체에 연결된 97자 불투명 세션 ID 생성 |
| GET | `/v1/sessions` | Memory에서 발견할 수 있는 호출자의 세션을 최신순으로 조회. `session_id`와, Memory가 알려 주면 `createdAt`(ISO 8601, UTC) 포함. 이벤트가 하나도 남지 않은 세션(삭제한 대화)은 나열하지 않음 |
| GET | `/v1/sessions/{id}/messages` | 사용자/어시스턴트의 텍스트 기록 반환 |
| DELETE | `/v1/sessions/{id}` | Runtime 중지 후 현재 이벤트/요약 삭제. 선호도/사실은 유지 |
| DELETE | `/v1/memory` | 발견 가능한 세션을 중지하고 이벤트와 저장된 기억 삭제 |
| POST | `/v1/invocations` | Runtime 호출. 기본값은 스트리밍 |

호출 JSON은 `message`(1–8000자), 서버에서 생성한 `session_id`, 선택적 `stream`을 포함합니다. 호출자 신원은 요청 본문이 아니라 검증된 액세스 토큰에서만 가져오며, 호출자에게 묶이지 않은 세션 ID는 거부합니다. `stream:false`이면 JSON 객체 하나 `{"data": "...", "stopReason": "..."}`로 응답합니다. 가드레일이 개입했다면 `data`는 에이전트가 보낸 대체 문구이고(`guardrail.streamProcessing = "sync"`일 때 보내며 Prod는 이 값이 필수), 보내지 않았다면 비어 있습니다. 개입 전에 스트리밍된 텍스트는 담지 않습니다. 실패한 턴은 오류 코드를 HTTP 오류로 반환하며 본문은 아래의 코드 형식입니다: 403 `owner_mismatch`, 409 `busy` 또는 `turn_limit`, 422 `invalid_request`, 429 `throttled`, 503 `init_failed`, 504 `deadline`, 502 `internal` 또는 `upstream`.

웹이 대응하는 HTTP 오류는 `{"detail": {"code": ..., "message": ...}}` 형식으로 `aria_contracts.events`의 오류 코드를 담습니다. 호출자의 세션이 아닌 세션 ID는 `owner_mismatch`(403), API가 거부한 요청 본문은 `invalid_request`(422, 32KiB를 넘으면 413), ID 공급자를 사용할 수 없으면 `upstream`(503)입니다. 401을 포함한 그 밖의 오류는 `detail`에 일반 문자열을 담습니다.

SSE 프레임은 `version:1`, `type`, `data`를 포함하며, 이벤트는 `aria_contracts.events`(`packages/contracts`)에 정의되어 있습니다. `textDelta`는 `text`를, `toolUse`는 `toolUseId`와 `name`을 담고 입력값은 담지 않습니다. `toolResult`는 `toolUseId`, `status`를 담고, `product.v1` 결과일 때만 `content`를 담습니다. 모든 턴은 정확히 하나의 `done`(`stopReason`, `guardrail_intervened`이면 가드레일의 대체 문구 `message` 포함) 또는 하나의 `error`(`code`, `retryable`, 개발자용 영어 `message`)로 끝납니다. 오류 코드는 에이전트가 보내는 `init_failed`, `owner_mismatch`, `turn_limit`, `deadline`, `busy`, `throttled`, `internal`, `invalid_request`와, API가 직접 보내는 코드입니다. API는 Runtime 호출이 실패하거나 결과 없이 스트림이 끝나면 `upstream`을, 그 호출의 제한 시간이 지나면 `deadline`을 보냅니다.

`src/runtime_client.py`는 bearer 인증, SSE 수신 형식, Runtime 세션 헤더만 보냅니다. 사용자 지정 브라우저 헤더는 전달하지 않습니다. 에이전트는 모델이나 도구가 실행되는 동안 아무것도 보내지 않으므로 호출 스트림에는 HTTP 읽기 제한 시간이 없고, 설정 가능한 요청 전체 제한 시간이 이를 제한합니다. 에이전트는 자신의 오류가 먼저 도착하도록 10초 먼저 멈춥니다. 세션 중지 같은 그 밖의 Runtime 호출은 읽기 제한 시간이 60초입니다. Memory 호출은 연결 3초, 읽기 10초 제한에 총 3회까지 시도합니다. uvicorn은 유휴 연결을 로드 밸런서의 150초보다 긴 155초 동안 유지하고, 종료 시 최대 25초 동안 진행 중인 응답을 마칩니다. 로그: `invocation_finished`(`session_id`, `status`, `ttfb_ms`, `duration_ms`), 오류 프레임마다 `agent_error`(`code`, `session_id`), Runtime 호출 실패 시 `runtime invocation failed`. 삭제 검증과 비동기 추출의 제한은 [운영 문서](../../docs/guides/operations.ko.md)를 참고하세요.

## 테스트와 이미지

```sh
uv run --frozen pytest
```

저장소 루트에서 빌드합니다. 공통 contracts를 포함하려면 `packages` 컨텍스트가 필요합니다:

```sh
finch build -f packages/api/Dockerfile -t aria-api:local packages
```
