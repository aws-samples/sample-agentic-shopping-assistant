/**
 * Every sentence the chat composes at run time: step labels, notes on an answer, errors and
 * their actions, sign-in states. Korean in 해요체, and "대화" for a conversation, never "세션".
 *
 * Developer details never reach this module: an error frame's English `message`, an HTTP body
 * and an exception's text are logged at most, and the shopper reads one of these instead.
 */
export const COPY = {
  /** The status line while Aria works, and the one announcement when she's done. */
  steps: {
    thinking: "Aria가 생각하고 있어요",
    searching: "카탈로그를 검색하고 있어요",
    savedItems: "저장한 상품을 확인하고 있어요",
    otherTool: "정보를 확인하고 있어요",
    arranging: "찾은 상품을 정리하고 있어요",
    retrying: "검색 조건을 다시 확인하고 있어요",
    composing: "답변을 정리하고 있어요",
    loadingHistory: "대화를 불러오고 있어요…",
    answered: "답변을 완료했어요",
  },
  /** Notes under an answer that ended early or was shortened. */
  notices: {
    stopped: "응답을 멈췄어요",
    interrupted: "답변이 중간에 끊겼어요",
    maxTokens: "답변이 길어서 여기까지만 보여 드려요",
  },
  /** Replaces an answer the guardrail blocked when it sent no replacement text. */
  blockedAnswer: "이 요청에는 답변할 수 없어요. 다른 질문을 해 주세요.",
  errors: {
    turnLimit: "대화가 길어졌어요. 새 대화를 시작해 주세요.",
    ownerMismatch: "이 대화는 이어갈 수 없어요. 새 대화를 시작해 주세요.",
    busy: "이전 답변을 아직 작성하고 있어요. 잠시 후 다시 보내 주세요.",
    throttled: "지금은 요청이 많아요. 잠시 후 다시 시도해 주세요.",
    upstream: "답변을 만들지 못했어요. 잠시 후 다시 시도해 주세요.",
    internal: "일시적인 문제가 생겼어요. 잠시 후 다시 시도해 주세요.",
    initFailed: "대화를 준비하지 못했어요. 잠시 후 다시 시도해 주세요.",
    deadline: "답변이 너무 오래 걸려서 멈췄어요. 다시 시도해 주세요.",
    invalidRequest: "메시지를 처리하지 못했어요. 내용을 바꿔서 다시 보내 주세요.",
    timeout: "응답 시간이 초과됐어요. 다시 시도해 주세요.",
    network: "연결이 끊겼어요. 네트워크를 확인하고 다시 시도해 주세요.",
    unreadable: "답변을 읽지 못했어요. 페이지를 새로고침한 뒤 다시 시도해 주세요.",
    generic: "답변을 받지 못했어요. 잠시 후 다시 시도해 주세요.",
    authExpired: "로그인이 만료되었어요. 다시 로그인해 주세요.",
    signedOut: "로그인이 필요해요. 로그인한 뒤 다시 보내 주세요.",
    forbidden: "이 대화를 이용할 권한이 없어요. 다시 로그인해 주세요.",
    noConversation: "진행 중인 대화가 없어요. 새 대화를 시작해 주세요.",
    startFailed: "대화를 시작하지 못했어요. 잠시 후 다시 시도해 주세요.",
  },
  history: {
    loadFailed: "대화를 불러오지 못했어요. 다시 시도해 주세요.",
    listFailed: "지난 대화를 불러오지 못했어요. 다시 시도해 주세요.",
    deleteFailed: "대화를 삭제하지 못했어요. 다시 시도해 주세요.",
    createFailed: "새 대화를 시작하지 못했어요. 잠시 후 다시 시도해 주세요.",
    current: "지금 대화",
    /** A conversation without a creation time: numbered from the oldest. */
    untitled: (n: number) => `대화 ${n}`,
    deleteLabel: (title: string) => `${title} 삭제`,
  },
  actions: {
    retry: "다시 시도하기",
    newConversation: "새 대화 시작하기",
    signIn: "다시 로그인하기",
    dismiss: "닫기",
    confirm: "확인",
  },
  auth: {
    unavailable: "로그인을 사용할 수 없어요. 잠시 후 다시 시도해 주세요.",
    expiredTitle: "로그인이 만료되었어요",
    expiredBody: "다시 로그인하면 이 대화를 이어갈 수 있어요.",
    callbackPending: "로그인을 완료하는 중이에요…",
    callbackFailed: "로그인을 완료하지 못했어요. 다시 로그인해 주세요.",
    callbackCancelled: "로그인이 취소되었어요. 다시 로그인하려면 아래 버튼을 눌러 주세요.",
    callbackStale: "로그인 요청이 만료되었어요. 다시 로그인해 주세요.",
    callbackNetwork: "로그인 서버에 연결하지 못했어요. 네트워크를 확인하고 다시 로그인해 주세요.",
  },
  /** Prompts sent on the shopper's behalf, in the same casual voice as the storefront's own. */
  prompts: {
    // No particle after the name: 와/과 depends on its last syllable, and catalog names are often English.
    similar: (name: string) => `비슷한 상품 보여줘: ${name}`,
  },
} as const;
