# NORDLY 웹

[English](README.md)

OIDC 인증 코드 + PKCE, 액세스 토큰 갱신, 버전이 있는 SSE, 상품 카드를 제공하는 Nuxt 4 SPA입니다. 답변은 HTML, 이미지, 링크 없이 렌더링하며 상품 카드 외의 도구 출력은 표시하지 않습니다. 에이전트와 Memory에는 API를 통해 접근하며 브라우저의 AWS 자격 증명으로 직접 접근하지 않습니다.

Cognito/BYO OIDC와 공개 환경 변수는 [통합 설정](../../docs/guides/integrations.ko.md)을 참고하세요. IdP에 `/auth/callback`을 등록합니다. `AUTH_PROVIDER`는 문서에 정의된 배포 공급자를 선택하며 두 방식 모두 같은 OIDC 브라우저 구현을 사용합니다. 토큰과 OAuth 상태는 sessionStorage에 저장합니다. 애플리케이션 클라이언트 시크릿을 공개 설정에 넣으면 안 됩니다. 모든 공개 변수는 [docs/FRONTEND.md](../../docs/FRONTEND.md#configuration)에 정리되어 있으며, 그중 `NUXT_PUBLIC_REQUEST_TIMEOUT_SECONDS`는 채팅 한 턴에 대한 브라우저의 제한 시간(기본 130초)입니다.

```sh
pnpm install --frozen-lockfile
pnpm --filter nordly-web dev
pnpm --filter nordly-web test
pnpm --filter nordly-web build
```

정적 생성 시 공개 설정이 결과물에 포함되므로 API/IdP 설정이 바뀌면 다시 빌드합니다. 앱 셸에는 이미지를 앱 자체, `data:` URL, `NUXT_PUBLIC_CSP_IMG_SRC`의 출처로만 허용하고 플러그인과 `<base>`를 막는 Content-Security-Policy 메타 태그가 들어 있습니다. HTTPS로 게시하고, `frame-ancestors`를 비롯한 나머지 정책은 웹 호스트에서 설정합니다. 중지 버튼은 HTTP 요청을 취소하지만 Runtime 세션 ID의 영구 무효화를 보장하지 않습니다. API는 기록 조회와 삭제를 검증된 호출자 범위로 제한합니다.
