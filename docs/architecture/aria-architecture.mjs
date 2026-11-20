// ARIA on AWS: one chat turn, as packages/infra deploys it, with the catalog indexing job.
// Render: node <excalidraw-arch>/bin/draw.mjs docs/architecture/aria-architecture.mjs --out docs/architecture
// The facts come from ARCHITECTURE.md, docs/design-docs/identity-and-tokens.md and
// docs/design-docs/catalog-search.md; keep them in step.
export const options = { preset: "doc", locales: ["en", "ko"], svg: true };

export default async function (d, { t, C }) {
  const ROW = 520; // centre line of the request path
  const X = 60; // horizontal offset of the AWS Cloud frame and everything drawn in or under it; the shopper column is not offset

  // A numbered badge next to an arrow; the numbered key under the diagram explains it.
  const badge = (n, x, y) => d.chip(x, y, 22, 20, String(n), { labelSize: 13 });

  // ── frames ─────────────────────────────────────────────────────────────────
  d.awsCloud(X + 200, 40, 1700, 1170);
  d.region(X + 224, 84, 1652, 1102);

  // ── the shopper and sign-in ─────────────────────────────────────────────────
  const shopper = d.iconAt("Res_User_48_Light", 96, ROW, {
    title: t("Shopper", "쇼핑객"),
    sub: ["NORDLY web app", t("static SPA, your host", "정적 SPA, 고객 호스팅")],
  });
  const cognito = d.icon("Arch_Amazon-Cognito_64", X + 500, 168, {
    title: "Amazon Cognito",
    sub: [t("managed login · PKCE", "관리형 로그인 · PKCE"), t("web and agent clients", "웹·에이전트 클라이언트")],
  });
  d.secondary([[shopper.cx, shopper.y - 4], [shopper.cx, cognito.cy], [cognito.x - 4, cognito.cy]], { start: true });
  badge(1, 124, cognito.cy - 20);

  // ── the VPC: the API, the Runtime, the catalog and its indexing job ─────────
  d.vpc(X + 392, 330, 838, 640);
  d.publicSubnet(X + 410, 376, 176, 300);
  d.privateSubnet(X + 604, 376, 608, 576);

  const waf = d.iconAt("Arch_AWS-WAF_64", X + 322, ROW, { title: "AWS WAF", sub: t("per-IP rate limit", "IP별 속도 제한") });
  const alb = d.iconAt("Res_Elastic-Load-Balancing_Application-Load-Balancer_48", X + 498, ROW, {
    size: 48, title: ["Application", "Load Balancer"], sub: "allowIpList",
  });
  const api = d.iconAt("Arch_AWS-Fargate_64", X + 699, ROW, {
    title: ["Amazon ECS", "on AWS Fargate"], sub: [t("API · verifies the JWT", "API · JWT 검증"), t("session owner · SSE", "세션 소유 · SSE")],
  });
  d.connect(shopper, waf);
  badge(2, 170, ROW - 20);
  d.connect(waf, alb);
  d.connect(alb, api);

  // The agent on AgentCore Runtime, whose network interfaces sit in the private subnets.
  const rt = d.group(X + 830, 420, 362, 284, { icon: "Arch_Amazon-Bedrock-AgentCore_32", title: "AgentCore Runtime" });
  d.marker(X + 854, ROW - 54, 312, 108);
  const agent = d.rect(X + 864, ROW - 46, 292, 92, {
    label: [t("Strands agent · Aria", "Strands 에이전트 · Aria"), t("never forwards the shopper's JWT", "쇼핑객 JWT는 전달하지 않음"), t("one microVM per session", "세션마다 microVM 하나")],
  });
  const tool = d.rect(X + 990, 612, 180, 56, { label: ["search_products", t("local tool", "로컬 도구")] });
  d.connect(api, agent);
  badge(3, X + 760, ROW - 20);
  d.arrow([[tool.cx, agent.b + 4], [tool.cx, tool.y - 4]]);

  const catalog = d.icon("Arch_Amazon-OpenSearch-Service_64", tool.cx, 800, {
    size: 48, title: ["Amazon", "OpenSearch Service"], sub: [t("your catalog · Nori", "고객 카탈로그 · Nori"), t("or the fixture, or your HTTPS API", "또는 fixture, 고객 HTTPS API")],
  });
  d.arrow([[tool.cx, tool.b + 4], [tool.cx, catalog.y - 4]]);
  d.note(tool.cx + 10, 724, t("hybrid · RRF", "하이브리드 · RRF"));

  const indexer = d.icon("Res_Amazon-Elastic-Container-Service_Task_48", X + 760, 800, {
    size: 48, title: t("Fargate task", "Fargate 작업"), sub: [t("catalog indexer", "카탈로그 인덱서"), t("Embed v4 · promote alias", "Embed v4 · 별칭 승격")],
  });
  d.secondary([[indexer.x + indexer.w + 4, indexer.cy], [catalog.x - 4, indexer.cy]]);

  const bucket = d.icon("Res_Amazon-Simple-Storage-Service_Bucket_48", X + 760, 1060, {
    size: 48, title: "Amazon S3", sub: t("your catalog JSON", "고객 카탈로그 JSON"),
  });
  d.secondary([[indexer.cx, bucket.y - 4], [indexer.cx, indexer.foot + 4]]);

  // ── the model and Memory, above the VPC ─────────────────────────────────────
  const mem = d.icon("Arch_Amazon-Bedrock-AgentCore_64", X + 870, 168, {
    size: 48, title: ["AgentCore", "Memory"], sub: [t("events: always on", "이벤트: 항상 저장"), t("long-term: optional", "장기 기억: 선택")],
  });
  // Arrows to the managed services above leave from the Runtime group's top edge, clear of its title.
  d.secondary([[X + 870, rt.y - 4], [X + 870, mem.foot + 4]], { start: true });
  badge(4, X + 884, 342);
  const model = d.icon("Arch_Amazon-Bedrock_64", X + 1046, 168, {
    title: "Amazon Bedrock", sub: [t("model · Converse", "모델 · Converse"), t("Guardrails · Embed v4", "Guardrails · Embed v4")],
  });
  d.arrow([[X + 1046, rt.y - 4], [X + 1046, model.foot + 4]], { start: true });

  // ── the agent's own token ───────────────────────────────────────────────────
  const identity = d.icon("Arch_Amazon-Bedrock-AgentCore_64", X + 1410, 168, {
    size: 48, title: ["AgentCore", "Identity"], sub: [t("token exchange · vault", "토큰 교환 · 저장소")],
  });
  d.secondary([[X + 1150, rt.y - 4], [X + 1150, identity.cy], [identity.x - 4, identity.cy]]);
  badge(5, X + 1176, identity.cy - 20);
  d.secondary([[identity.cx, identity.y - 4], [identity.cx, 120], [cognito.cx, 120], [cognito.cx, cognito.y - 4]], { start: true });
  d.note(X + 640, 96, t("on-behalf-of token exchange", "on-behalf-of 토큰 교환"));

  // ── AgentCore Gateway and its targets ───────────────────────────────────────
  const gw = d.group(X + 1270, 338, 300, 372, { icon: "Arch_Amazon-Bedrock-AgentCore_32", title: "AgentCore Gateway" });
  d.note(X + 1290, 382, t("authorizer: the agent's token only", "인가자: 에이전트 토큰만"));
  const icp = d.iconAt("Res_AWS-Lambda_Lambda-Function_48", X + 1350, ROW, {
    size: 48, title: "AWS Lambda", sub: [t("REQUEST interceptor", "REQUEST 인터셉터"), t("actor + agent token", "actor + 에이전트 토큰")],
  });
  d.rect(X + 1290, 628, 260, 56, { label: [t("Cedar policies (optional)", "Cedar 정책 (선택)"), t("both must allow", "둘 다 허용해야 통과")] });
  d.connect(agent, icp);
  badge(6, X + 1250, ROW - 20);
  d.secondary([[identity.cx, gw.y - 4], [identity.cx, identity.foot + 4]]);
  d.note(identity.cx + 12, 300, t("target credentials", "대상 자격 증명"));

  const targets = d.group(X + 1630, 338, 230, 372, { title: t("Gateway targets", "Gateway 대상"), color: C.sub });
  d.icon("Res_AWS-Lambda_Lambda-Function_48", X + 1745, 392, { size: 48, title: "AWS Lambda", sub: t("saved-items sample", "저장 상품 샘플") });
  d.icon("Res_Amazon-API-Gateway_Endpoint_48", X + 1745, 562, {
    size: 48, title: t("Your APIs", "고객 API"), sub: ["OpenAPI · API Gateway", "Lambda · MCP server"],
  });
  d.connect(icp, targets);
  badge(7, X + 1500, ROW - 20);

  // ── the answer streams back ─────────────────────────────────────────────────
  d.arrow([[api.cx, api.foot + 4], [api.cx, 740], [shopper.cx, 740], [shopper.cx, shopper.foot + 4]]);
  badge(8, 170, 720);

  // ── observability ──────────────────────────────────────────────────────────
  const obs = d.group(X + 1270, 770, 590, 190, { title: t("Observability", "관측성"), color: C.sub });
  d.icon("Arch_Amazon-CloudWatch_64", X + 1380, 826, { size: 48, title: "Amazon CloudWatch", sub: t("logs · alarms · dashboard", "로그 · 경보 · 대시보드") });
  d.icon("Arch_AWS-X-Ray_64", X + 1580, 826, { size: 48, title: "AWS X-Ray", sub: t("traces", "트레이스") });
  d.icon("Arch_Amazon-Bedrock-AgentCore_64", X + 1760, 826, { size: 48, title: ["AgentCore", "Evaluations"], sub: t("optional", "선택") });
  // Telemetry leaves the Runtime on its right and runs down the gap between the VPC and the Gateway.
  d.secondary([[rt.x + rt.w + 4, 680], [X + 1250, 680], [X + 1250, 885], [obs.x - 4, 885]]);

  // ── the steps, under the drawing ───────────────────────────────────────────
  const steps = [
    t("Sign in with managed login (PKCE)", "관리형 로그인(PKCE)으로 로그인"),
    t("The message and the shopper's JWT go to the API", "메시지와 쇼핑객 JWT를 API로 보냄"),
    t("The API verifies the JWT and the session owner, then invokes the Runtime with it", "API가 JWT와 세션 소유자를 검증하고 같은 JWT로 Runtime 호출"),
    t("The agent reads and writes the conversation events", "에이전트가 대화 이벤트를 읽고 씀"),
    t("The agent mints its own Gateway token: an on-behalf-of exchange", "에이전트가 자기 Gateway 토큰 발급: on-behalf-of 교환"),
    t("Gateway calls carry the agent's token, x-aria-actor-id and the trace ID, never the shopper's JWT", "Gateway 호출에는 에이전트 토큰, x-aria-actor-id, 트레이스 ID만, 쇼핑객 JWT는 없음"),
    t("The interceptor and Cedar both allow; the target uses its own credential", "인터셉터와 Cedar가 모두 허용, 대상은 자체 자격 증명 사용"),
    t("Text, tool steps and product cards stream back as SSE", "텍스트, 도구 단계, 상품 카드가 SSE로 돌아옴"),
  ];
  steps.forEach((s, i) => {
    const col = i < 4 ? 0 : 1;
    const x = X + 200 + col * 860;
    const y = 1240 + (i % 4) * 30;
    badge(i + 1, x + 11, y + 9);
    d.text(x + 30, y, s, { size: 15 });
  });

  // ── legend ─────────────────────────────────────────────────────────────────
  d.legend(X + 200, 1380, [
    { kind: "flow", text: t("A chat turn", "채팅 한 턴") },
    { kind: "secondary", text: t("Sign-in · tokens · memory · indexing · telemetry", "로그인 · 토큰 · 메모리 · 인덱싱 · 관측") },
    { kind: "built", text: t("Reads the shopper's JWT and never forwards it", "쇼핑객 JWT를 읽기만 하고 전달하지 않음") },
  ]);
  d.text(X + 200, 1422, t(
    "Indexing: scripts/index_catalog.sh puts your catalog JSON in S3; the Fargate task embeds it with Cohere Embed v4, loads OpenSearch and promotes the alias.",
    "인덱싱: scripts/index_catalog.sh가 고객 카탈로그 JSON을 S3에 올리면, Fargate 작업이 Cohere Embed v4로 임베딩해 OpenSearch에 적재하고 별칭을 승격합니다.",
  ), { size: 15 });
}
