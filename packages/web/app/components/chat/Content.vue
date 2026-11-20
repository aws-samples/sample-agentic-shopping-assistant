<template>
  <div ref="chatContainer" class="flex-1 overflow-y-auto px-5 py-6">
    <!-- A past conversation that couldn't load. The conversation on screen, if any, stays below. -->
    <div v-if="historyStatus === 'error' && historyError" role="alert" class="mb-5 rounded-[10px] border border-line bg-paper px-4 py-3">
      <p class="text-sm leading-relaxed text-ink-soft">{{ historyError.message }}</p>
      <div class="mt-3 flex flex-wrap gap-2">
        <button type="button" :class="primaryButton" @click="historyAction">
          {{ historyError.action === "signIn" ? COPY.actions.signIn : COPY.actions.retry }}
        </button>
        <button type="button" :class="secondaryButton" @click="chatStore.dismissHistoryError()">
          {{ COPY.actions.dismiss }}
        </button>
      </div>
    </div>

    <template v-if="!historyLoading">
      <!-- Empty conversation -->
      <div v-if="turns.length === 0 && sessionId">
        <p class="font-display text-[1.45rem] font-[340] leading-snug text-ink">무엇을 도와드릴까요?</p>
        <p class="mt-2 text-sm text-muted">찾는 옷을 설명하거나, 아래에서 골라보세요.</p>
        <p class="label mt-8">둘러보기</p>
        <ChatSuggestion class="mt-3" :suggestions="shoppingSuggestions" @select="send" />
        <p class="label mt-7">추천 받기</p>
        <ChatSuggestion class="mt-3" :suggestions="recommendationSuggestions" @select="send" />
      </div>

      <!-- Not a live region: streamed text would be read out delta by delta. The status line below speaks instead. -->
      <ol class="space-y-5">
        <li v-for="(turn, index) in turns" :key="index">
          <!-- Shopper -->
          <div v-if="turn.role === 'user'" class="flex justify-end">
            <p
              class="max-w-[82%] whitespace-pre-wrap rounded-[18px] rounded-br-[5px] bg-ink px-4 py-2.5 text-[0.93rem] leading-relaxed text-paper"
            >
              {{ turn.message.content }}
            </p>
          </div>

          <!-- Aria: one answer per turn, with product cards where she cited them and a note if it ended early -->
          <div v-else class="flex gap-3" :aria-busy="isLoading && index === turns.length - 1 ? 'true' : undefined">
            <span class="mt-0.5 grid h-7 w-7 shrink-0 place-items-center rounded-full border border-line text-lingon">
              <BrandMark :ring="false" class="h-3.5 w-3.5" />
            </span>
            <div class="min-w-0 flex-1 space-y-3">
              <template v-for="(part, p) in turn.parts" :key="p">
                <p v-if="part.type === 'notice'" class="flex items-center gap-1.5 text-xs text-muted">
                  <i :class="part.notice === 'stopped' ? 'pi pi-stop-circle' : 'pi pi-info-circle'" class="text-xs" aria-hidden="true" />
                  {{ part.content }}
                </p>
                <ChatAnswer
                  v-else
                  :text="part.content"
                  :products="products"
                  :restored="!!part.restored"
                  :streaming="isLoading && part === streamingMessage"
                />
              </template>
            </div>
          </div>
        </li>
      </ol>
    </template>

    <!-- The one live region: the step while Aria works, then one announcement when she's done. -->
    <div role="status" :class="busy ? ['mt-5 flex items-center gap-2 text-xs text-muted', { 'pl-10': isLoading }] : 'sr-only'">
      <template v-if="isLoading">
        <span class="flex gap-1" aria-hidden="true">
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-lingon [animation-delay:-0.3s]" />
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-lingon [animation-delay:-0.15s]" />
          <span class="h-1.5 w-1.5 animate-bounce rounded-full bg-lingon" />
        </span>
        {{ activity }}
      </template>
      <template v-else-if="historyLoading">
        <i class="pi pi-spin pi-spinner text-lingon" aria-hidden="true" />
        {{ COPY.steps.loadingHistory }}
      </template>
      <template v-else>{{ announcement }}</template>
    </div>

    <!-- The latest turn's error. The error dialog announced it; this keeps it in view, with its action. -->
    <div v-if="error" class="mt-4 flex flex-col items-center gap-2.5 text-center">
      <p class="text-sm text-lingon">{{ error.message }}</p>
      <button v-if="error.action" type="button" :class="primaryButton" @click="emit('action', error.action)">
        {{ COPY.actions[error.action] }}
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
import type { TurnOutcome } from "~/stores/chat";
import { activityLabel } from "~/utils/activity";
import { COPY } from "~/utils/copy";
import type { ErrorAction } from "~/utils/errors";

const emit = defineEmits<{
  (e: "action", action: ErrorAction): void;
}>();

const chatStore = useChatStore();
const { messages, turns, products, isLoading, error, lastOutcome, historyStatus, historyError, historyLoading } =
  storeToRefs(chatStore);
const { sessionId } = storeToRefs(useSessionStore());
const { ask } = useConcierge();

const chatContainer = ref<HTMLElement | null>(null);

watch(
  [() => turns.value, error],
  () => {
    nextTick(() => {
      if (chatContainer.value) chatContainer.value.scrollTop = chatContainer.value.scrollHeight;
    });
  },
  { deep: true },
);

interface Suggestion {
  text: string;
  highlighted?: boolean;
}

const shoppingSuggestions: Suggestion[] = [
  { text: "여름 캐주얼 의류" },
  { text: "직장인 정장 코디", highlighted: true },
  { text: "가을 아우터 트렌드", highlighted: true },
  { text: "20대 여성 데일리룩", highlighted: true },
];

const recommendationSuggestions: Suggestion[] = [
  { text: "여름 휴가에 어울리는 액세서리", highlighted: true },
  { text: "데이트 코디에 어울리는 가방", highlighted: true },
];

const primaryButton =
  "inline-flex cursor-pointer items-center rounded-full bg-ink px-4 py-2 text-sm font-medium text-paper transition-colors hover:bg-lingon";
const secondaryButton =
  "cursor-pointer rounded-full border border-line px-4 py-2 text-sm text-ink-soft transition-colors hover:border-ink hover:text-ink";

function send(suggestion: Suggestion) {
  ask(suggestion.text);
}

function historyAction() {
  if (historyError.value?.action === "signIn") emit("action", "signIn");
  else chatStore.retryHistory();
}

const busy = computed(() => isLoading.value || historyLoading.value);

/** The message still being written, if the latest one is assistant text. */
const streamingMessage = computed(() => {
  const last = messages.value[messages.value.length - 1];
  return last?.role === "assistant" && last.type === "text" ? last : undefined;
});

/** What Aria is doing right now, for the status line. */
const activity = computed(() => activityLabel(messages.value));

// Announced once per turn: set when a turn ends, and never replayed when the panel reopens.
const ANNOUNCEMENTS: Record<TurnOutcome, string> = {
  done: COPY.steps.answered,
  stopped: COPY.notices.stopped,
  interrupted: COPY.notices.interrupted,
};
const announcement = ref("");
watch(lastOutcome, (next) => {
  announcement.value = next ? ANNOUNCEMENTS[next.outcome] : "";
});
</script>
