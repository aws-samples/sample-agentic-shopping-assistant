# 평가

[English](evaluations.md)

ARIA는 AgentCore Evaluations를 두 가지 방식으로 사용합니다. 두 방식 모두 같은 평가기를 사용하므로 점수를 서로 비교할 수 있습니다.

| | 온라인 평가 | 데이터셋 평가 |
| --- | --- | --- |
| 채점 대상 | 실제 세션 일부를 지속적으로 샘플링 | 고정된 테스트 대화 집합을 필요할 때 실행 |
| 위치 | `packages/infra`(`EvaluationsStack`), `[evaluations]`에서 설정 | `packages/agent/evals`. [데이터셋 평가](#데이터셋-평가) 참고 |
| 정답 정보 | 없음. judge가 쇼핑객의 목표를 추론 | 직접 작성한 `assertions`, `expected_trajectory`, `expected_response` |
| 적합한 용도 | 실제 트래픽에서 품질 변화와 회귀 발견 | 릴리스 점검, 프롬프트/모델 변경 |

이 가이드는 두 방식을 모두 다루며, 아래 사전 조건은 두 방식에 공통입니다.

## 사전 조건

- **승인된 콘텐츠 수집.** `operations.captureContent = true`는 프롬프트, 응답, 도구 결과를 Runtime 로그 그룹(30일 보존)에 저장합니다. 먼저 저장과 보존에 대한 승인을 받으세요([운영](operations.ko.md) 참고). 수집된 콘텐츠가 없으면 채점할 대상이 없습니다.
- **배포 리전에서 Transaction Search가 CloudWatch Logs로 전달될 것.** 이 앱은 계정 수준 설정을 바꾸지 않습니다. `aws xray get-trace-segment-destination --region <region>`으로 확인하며, `"Destination": "CloudWatchLogs"`와 `"Status": "ACTIVE"`가 나와야 합니다. 같은 계정이라도 리전마다 목적지가 다를 수 있습니다. 확인한 뒤에만 `operations.transactionSearchReady = true`로 설정하세요. 평가가 활성화되어 있으면 `scripts/preflight.py`가 다시 확인합니다.
- **judge 모델 접근 권한.** 기본 judge는 `global.anthropic.claude-haiku-4-5-20251001-v1:0`입니다. `aws bedrock get-foundation-model-availability --region <region> --model-id anthropic.claude-haiku-4-5-20251001-v1:0` 결과가 `AUTHORIZED`, `AVAILABLE`이어야 합니다.
- **judge 예산.** 채점되는 세션마다 judge 호출 비용이 듭니다. trace 수준 평가기는 턴마다, 도구 수준 평가기는 도구 호출마다, 세션 수준 평가기는 세션마다 한 번 실행됩니다. 기본 평가기 6개 기준으로, 턴 2개와 도구 호출 3개가 있는 세션 하나에서 점수 13개가 나왔습니다.

## 활성화(배포 2회)

`operations.traceLogGroup`은 Runtime 자체 로그 그룹인 `/aws/bedrock-agentcore/runtimes/<RuntimeId>-DEFAULT`를 가리켜야 하는데, Runtime ID는 첫 배포에서 생성됩니다. 그래서 배포가 두 번 필요합니다.

1. `captureContent = true`, `transactionSearchReady = true`, `[evaluations] enabled = false`로 배포합니다. [릴리스](release.ko.md#수동-배포)의 계획/배포 절차를 따르세요.
2. `<ns>AgentRuntime` 스택의 `RuntimeId` 출력을 확인합니다. 테스트 대화를 하나 보낸 뒤, Runtime 로그 그룹에 대해 다음 CloudWatch Logs Insights 쿼리를 실행해 스팬이 스택이 필터링할 서비스 이름(`<runtime name>.DEFAULT`, Dev 템플릿에서는 `ariadev_agent.DEFAULT`)으로 들어와 있는지 확인합니다.

   ```
   fields @message
   | filter ispresent(spanId)
   | stats count(*) by resource.attributes.service.name
   ```

   통합 Runtime에서는 `aws/spans`가 존재하지만 비어 있을 수 있으며, 정상입니다.
3. `operations.traceLogGroup`을 그 로그 그룹으로, `[evaluations] enabled = true`로 설정하고 `samplingPercentage`와 평가기(사용자 정의 포함 최대 10개)를 고른 뒤 다시 계획하고 배포합니다. 이때 `<ns>Evaluations` 스택이 추가됩니다.

Runtime이 교체되어 새 `RuntimeId`를 받으면 `traceLogGroup`을 고치고 다시 배포하세요. 그러지 않으면 이전 이름은 아무것도 일치하지 않는데, 어디에서도 오류가 보고되지 않습니다.

## 세션이 채점되는지 확인

- 세션은 `sessionTimeoutMinutes` 동안 유휴 상태가 된 뒤 채점되며, 처리 시간만큼 더 지연됩니다. 검증 실행에서는 제한 시간을 5분으로 두었을 때 마지막 턴 후 약 15분 뒤에 결과가 나왔습니다.
- 결과는 스택의 결과 로그 그룹 `/<ns>/evaluations/results`(`ResultsLogGroupName` 스택 출력)에 JSON 이벤트로 기록됩니다. Logs Insights로 요약할 수 있습니다.

  ```
  fields attributes.session.id, attributes.gen_ai.evaluation.name,
         attributes.gen_ai.evaluation.score.value, attributes.gen_ai.evaluation.score.label
  | sort @timestamp desc
  ```

  judge의 판단 근거는 `attributes.gen_ai.evaluation.explanation`에 있습니다.
- 점수는 `Bedrock-AgentCore/Evaluations` 네임스페이스의 CloudWatch 지표로도 게시되며, CloudWatch 콘솔의 GenAI Observability → Bedrock AgentCore → 에이전트 → Evaluations에서도 볼 수 있습니다.
- 채점된 세션과 Runtime 로그 그룹의 세션을 비교해 평가 비율을 측정하세요. 채점된 세션이 0개라면 조사해야 할 실패이지, 문제없다는 뜻이 아닙니다.
- 운영 외 스테이지에서 새 설정을 검증할 때는 테스트 트래픽만 있는 동안 `samplingPercentage`를 100으로 올리고 `sessionTimeoutMinutes`를 낮춘 뒤, 끝나면 되돌리세요.

**보존 기간.** judge 설명은 대화 일부를 다시 서술하므로, 스택은 Runtime 로그 그룹과 같은 30일 보존 기간의 자체 로그 그룹에 결과를 기록합니다. 따로 지정하지 않으면 서비스는 만료되지 않는 결과 로그 그룹을 만듭니다. 스택을 삭제해도 이 그룹은 남습니다.

## 점수 해석

온라인 평가기에는 정답 정보가 없으므로, 의도한 대로 동작했는지가 아니라 쇼핑객의 목표가 달성되었는지를 판단합니다. 정직하게 결과가 없다고 답한 검색도 `Builtin.GoalSuccessRate`에서 0점을 받을 수 있습니다. 에이전트가 올바른 필터로 검색하고 일치하는 상품이 없다고 답해도, 검색 범위를 넓히거나 대안을 제안하지 않으면 judge가 목표 미달성으로 판단하기 때문입니다. 같은 대화의 데이터셋 실행은 "결과가 없으면 그렇게 말한다"만 요구하는 assertion 기준으로 1.0을 받습니다. 낮은 점수는 설명과 함께 읽으세요. 일부는 에이전트 버그가 아니라 카탈로그 범위나 제품 결정에 관한 신호입니다.

## 사용자 정의 평가기

`packages/infra/components/evaluators.py`에 `CustomEvaluatorSpec`을 추가하면 Evaluations 스택과 함께 배포되어 온라인 구성에 연결됩니다. 기본 제공 예시인 `catalogue_grounding`은 어시스턴트가 언급한 모든 상품이 검색 결과에 있었는지 확인합니다.

- placeholder는 중괄호 하나를 씁니다. TRACE는 `{context}`, `{assistant_turn}`, SESSION은 `{context}`, `{available_tools}`, TOOL_CALL은 `{context}`, `{available_tools}`, `{tool_turn}`입니다. 중괄호를 두 개 쓰면 배포 시 "Instructions contain invalid placeholders"로 실패합니다.
- 출력 형식 지시를 넣지 마세요. 서비스가 이유와 점수를 요청하는 지시를 자동으로 덧붙입니다.
- 정답 정보 placeholder(`{assertions}`, `{expected_response}`, `{expected_tool_trajectory}`)는 온라인 구성에서 사용할 수 없습니다.
- 데이터셋 실행에서 사용자 정의 평가기를 쓰려면 `CustomEvaluatorIds` 스택 출력의 ID를 `python -m evals.run --evaluator`에 넘기세요.

## 문제 해결

| 증상 | 확인할 사항 |
| --- | --- |
| 결과 로그 그룹이나 이벤트가 없음 | `sessionTimeoutMinutes`와 처리 시간이 지났는지 먼저 확인합니다. 그다음 샘플링, `EvaluatedServiceName` 스택 출력과 스팬의 `resource.attributes.service.name` 일치 여부, `traceLogGroup`이 현재 Runtime을 가리키는지, `captureContent`, Transaction Search 목적지를 확인합니다. |
| 사용자 정의 평가기 `CREATE_FAILED`: "invalid placeholders" | 중괄호 하나를 쓰고, 해당 평가기 수준이 허용하는 placeholder만 사용합니다. |
| 결과 로그 그룹에 오류 이벤트 | 리전의 judge 모델 접근 권한, 할당량, 스로틀링을 확인합니다. |
| 모든 세션이 만점 | 믿기 전에 평가 비율을 확인하고 설명 몇 개를 읽어 보세요. 샘플이 작거나 대표성이 없을 수 있습니다. |

## 비활성화

`[evaluations] enabled = false`로 설정하고 배포하면 앱에서 Evaluations 스택이 빠집니다. 하지만 배포는 앱에 없는 스택을 삭제하지 않으므로, `<ns>Evaluations` 스택을 직접 삭제하기 전까지 온라인 구성과 사용자 정의 평가기는 계속 동작합니다(Prod 스택은 종료 보호가 켜져 있어 먼저 꺼야 합니다). 끄지 않고 비용만 줄이려면 `samplingPercentage`를 낮추거나 평가기를 줄이세요. 결과 로그 그룹은 스택을 삭제해도 남으므로, 결과가 더 이상 필요 없으면 직접 삭제하세요.

## 검증 상태

Dev 스테이지에서 테스트 세션 4개가 기본 평가기 6개 모두로 오류 없이 채점되었으며, 도구 수준 평가기와 `catalogue_grounding`도 포함됩니다. 아직 실행하지 않은 항목: 기본 제공 Dev 템플릿으로 배포한 스테이지, Prod 구성, 외부 Gateway, `tenantClaim`을 사용하는 스테이지.

## 데이터셋 평가

테스트 대화로 구성된 JSON 데이터셋을 **배포된** AgentCore Runtime에 실행하고 **AgentCore Evaluations**로 채점합니다. `packages/infra`의 온라인 평가 구성(실시간 트래픽 샘플링)을 보완하며, 릴리스 승격 전처럼 필요할 때 고정된 케이스 집합을 채점합니다.

### 동작 방식

`run.py`는 AgentCore SDK의 `OnDemandEvaluationDatasetRunner`를 감쌉니다.

1. **호출.** 시나리오마다 Runtime 세션 하나를 사용합니다. 각 턴을 평가 사용자의 bearer 액세스 토큰과 함께 Runtime HTTPS 엔드포인트로 순서대로 보냅니다(Runtime은 SigV4가 아닌 CUSTOM_JWT 인증을 사용합니다).
2. **대기.** CloudWatch가 트레이스를 수집할 때까지 `--ingestion-delay`초(기본 180초) 기다립니다.
3. **채점.** Runtime 로그 그룹(및 `aws/spans`)에서 스팬을 수집해 데이터셋의 정답 정보와 함께 Evaluate API로 보냅니다.

러너가 만드는 세션 ID는 에이전트의 인증 주체 기반 세션 검사를 통과하지 못합니다. 그래서 `runtime_invoker.py`가 토큰에서 소유자를 도출해 시나리오마다 소유자에 묶인 세션을 열고, 수집한 스팬의 세션 ID를 다시 바꿔 붙입니다(해당 모듈의 docstring 참고).

`runner.py`는 SDK 러너를 두 군데 조정합니다(해당 모듈의 docstring 참고).
- Evaluate API가 거부하므로 ADOT의 MCP 클라이언트 스팬을 도구 수준 평가 대상에서 제외합니다.
- `expected_trajectory`가 없는 시나리오에는 trajectory 평가기를 건너뜁니다. 예를 들어 도구를 호출하지 않아야 하는 주제 외 요청은 요약에 trajectory 줄이 나오지 않습니다.

### 케이스 작성

AgentCore [데이터셋 스키마](https://docs.aws.amazon.com/bedrock-agentcore/latest/devguide/dataset-evaluations-schema.md)를 사용합니다. `cases/example.json`을 참고하세요. `cases/safety.json`에는 안전 케이스가 있습니다. 시스템 프롬프트를 요구하는 프롬프트 인젝션, Aria에 도구가 없는 주문 취소와 결제 요청, 다른 쇼핑객의 개인 정보 요청, 카탈로그로 답할 수 없는 가격·재고 질문, 그리고 `saved-items___get_saved_items`를 호출해야 하는 저장 상품 요청입니다. 시나리오별 필드는 다음과 같습니다.

| 필드 | 사용처 |
| --- | --- |
| `turns[].input` | 한 세션 안에서 순서대로 사용자 프롬프트로 전송 |
| `turns[].expected_response` | `Builtin.Correctness` |
| `assertions` | `Builtin.GoalSuccessRate` |
| `expected_trajectory` | `Builtin.Trajectory*Match`. 에이전트의 도구 이름을 사용합니다. 로컬 도구는 이름 그대로(`search_products`), Gateway 도구는 `<target>___<tool>` 형식(`saved-items___get_saved_items`)입니다. |

스테이지가 fixture 카탈로그를 쓰는 경우가 아니라면 어떤 카탈로그에서도 성립하는 assertion을 작성하세요.

### 실행

스테이지의 Cognito 풀에 평가 전용 사용자를 만든 뒤 한 번 로그인해 액세스 토큰을 받습니다. `login.py`는 웹 클라이언트에 등록된 `localhost:4000` 콜백으로 authorization code + PKCE 흐름을 실행하므로, 먼저 로컬 웹 개발 서버를 중지하세요.

```bash
cd packages/agent
uv run python -m evals.login \
  --domain https://<prefix>.auth.<region>.amazoncognito.com \
  --client-id <AuthStack UserPoolClientId> --out ../../artifacts/eval-token

export ARIA_ACCESS_TOKEN="$(cat ../../artifacts/eval-token)"
uv run python -m evals.run evals/cases/example.json \
  --runtime-arn <AgentRuntime 스택 출력 RuntimeArn> --invoke
```

토큰은 1시간 동안 유효합니다. 리전과 로그 그룹은 Runtime ARN에서 도출합니다(로그 그룹은 `--log-group`으로 바꿀 수 있습니다).

시나리오와 평가기 조합마다 한 줄을 출력하고, judge 설명과 Runtime 세션 ID를 포함한 원본 결과를 `evals/results/`(gitignore 대상)에 저장합니다. 시나리오 실행이 실패하거나 평가기가 오류를 반환하면 0이 아닌 코드로 종료합니다. `not scored`는 통과가 아니라 평가되지 않았다는 뜻입니다. `ignored: ...`는 서비스가 정답 정보 일부를 사용하지 않았다는 뜻이므로 데이터셋을 확인하세요.

기본 평가기는 온라인 평가의 5개에 `Builtin.TrajectoryInOrderMatch`와 `Builtin.Correctness`를 더한 것입니다. `--evaluator`(반복 가능)로 교체할 수 있으며, 예를 들어 Evaluations 스택의 `CustomEvaluatorIds` 출력에 있는 `catalogue_grounding` ID를 넣을 수 있습니다.

점수로 릴리스를 막으려면 `--min EVALUATOR=SCORE`(반복 가능)를 넘기세요. 그러면 해당 평가기의 실행 전체 평균 점수가 `SCORE`보다 낮거나 점수가 하나도 없을 때도 0이 아닌 코드로 종료합니다. 실행에서 쓰지 않는 평가기를 지정하면 호출 전에 거부합니다. 예:

```bash
uv run python -m evals.run evals/cases/safety.json --runtime-arn <RuntimeArn> --invoke \
  --min Builtin.GoalSuccessRate=0.8 --min Builtin.InstructionFollowing=0.8
```

### 실행 전 확인

- **콘텐츠 수집.** 스테이지에 `operations.captureContent=true`가 설정되어 있고 Transaction Search가 CloudWatch Logs로 전달되어야 합니다([운영](operations.ko.md) 참고). 대화 콘텐츠가 수집되지 않으면 채점할 대상이 없습니다.
- **비용과 부수 효과.** 모든 턴은 과금되는 에이전트 호출이고, 모든 평가기는 과금되는 judge 모델을 호출합니다(도구 수준 평가기는 도구 호출마다 실행). 턴은 평가 사용자의 Memory에 기록되고 장기 기억이 켜져 있으면 선호도와 사실로도 추출되므로, 테스트 데이터가 실제 사용자나 이후 실행에 섞이지 않도록 평가 전용 사용자를 사용하세요.
- **자격 증명.** AWS 호출자에게 로그 그룹에 대한 `logs:StartQuery`, `logs:GetQueryResults`와 AgentCore `Evaluate`, `GetEvaluator` 권한이 필요합니다. CloudWatch 쿼리가 실패하면 경고만 남고 "스팬 없음"처럼 보이므로, 모든 시나리오가 채점되지 않으면 권한부터 확인하세요.
- **`pnpm check`에 포함되지 않음.** 실제 서비스를 호출하기 때문입니다. 러너 자체는 `tests/unit/test_evals.py`의 단위 테스트가 오프라인으로 검증합니다.
