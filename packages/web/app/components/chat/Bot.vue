<template>
  <Transition name="concierge">
    <section
      v-if="isOpen"
      ref="panel"
      role="dialog"
      aria-label="Aria 쇼핑 어시스턴트"
      tabindex="-1"
      :class="[
        'fixed z-50 flex flex-col overflow-hidden border border-line bg-card shadow-[0_40px_90px_-40px_rgb(0_0_0/0.6)] transition-[width,height,border-radius] duration-300 ease-out focus:outline-none',
        isMinimized
          ? 'bottom-6 right-6 h-14 w-[232px] rounded-full'
          : 'inset-0 sm:inset-auto sm:bottom-6 sm:right-6 sm:h-[min(700px,calc(100vh-3rem))] sm:w-[420px] sm:rounded-[14px]',
      ]"
      @keydown.esc="onEscape"
    >
      <ChatHeader @close="close" @minimize="toggleMinimize" @show-sessions="showSessionList = true" />

      <template v-if="!isMinimized">
        <!-- Signed out, or the sign-in expired with no conversation on screen -->
        <div v-if="view === 'signedOut'" class="flex flex-1 flex-col justify-center px-8 pb-10">
          <BrandMark :ring="false" class="h-7 w-7 text-lingon" />
          <h2 class="mt-6 font-display text-[1.7rem] font-[340] leading-snug text-ink">
            <template v-if="auth.expired">{{ COPY.auth.expiredTitle }}</template>
            <template v-else>안녕하세요,<br />Aria예요.</template>
          </h2>
          <p class="mt-3 text-[0.93rem] leading-relaxed text-muted">
            {{ auth.expired ? COPY.auth.expiredBody : "로그인하면 찾는 옷을 말로 설명하고, 카탈로그에서 바로 골라 받을 수 있어요." }}
          </p>
          <blockquote
            v-if="pendingPrompt"
            class="mt-6 border-l-2 border-lingon pl-4 font-display text-[1.05rem] leading-relaxed text-ink-soft"
          >
            “{{ pendingPrompt }}”
            <span class="mt-1 block font-sans text-xs text-muted">로그인 후 바로 답해드릴게요.</span>
          </blockquote>
          <button
            type="button"
            data-autofocus
            class="mt-8 inline-flex cursor-pointer items-center justify-center gap-2 rounded-full bg-ink px-6 py-3 text-[0.93rem] font-medium text-paper transition-colors hover:bg-lingon"
            @click="signIn"
          >
            {{ auth.expired ? COPY.actions.signIn : "로그인하고 시작하기" }}
          </button>
          <p v-if="signInError" role="alert" class="mt-3 text-sm text-lingon">{{ signInError }}</p>
        </div>

        <!-- Signed in, no conversation yet -->
        <div v-else-if="view === 'welcome'" class="flex flex-1 flex-col justify-center px-8 pb-10">
          <BrandMark :ring="false" class="h-7 w-7 text-lingon" />
          <h2 class="mt-6 font-display text-[1.7rem] font-[340] leading-snug text-ink">
            무엇을 찾고<br />계신가요?
          </h2>
          <p class="mt-3 text-[0.93rem] leading-relaxed text-muted">
            새 대화를 시작하거나, 지난 대화를 이어가세요.
          </p>
          <div class="mt-8 space-y-3">
            <button
              type="button"
              data-autofocus
              :disabled="starting"
              class="flex w-full cursor-pointer items-center justify-center gap-2 rounded-full bg-ink px-6 py-3 text-[0.93rem] font-medium text-paper transition-colors hover:bg-lingon disabled:cursor-wait disabled:opacity-60"
              @click="startNewSession"
            >
              <i :class="starting ? 'pi pi-spin pi-spinner' : 'pi pi-plus'" class="text-xs" aria-hidden="true" />
              새 대화 시작하기
            </button>
            <button
              type="button"
              class="flex w-full cursor-pointer items-center justify-center gap-2 rounded-full border border-ink px-6 py-3 text-[0.93rem] font-medium text-ink transition-colors hover:bg-paper-deep"
              @click="showSessionList = true"
            >
              <i class="pi pi-history text-xs" aria-hidden="true" />
              지난 대화 보기
            </button>
          </div>
          <p class="mt-8 flex items-center gap-2 text-xs text-muted">
            <span class="h-1.5 w-1.5 rounded-full bg-pine" aria-hidden="true" />
            실제 카탈로그에 있는 상품만 추천해요.
          </p>
        </div>

        <template v-else>
          <ChatContent @action="runAction" />
          <ChatInput v-if="auth.authenticated && sessionId" />
          <!-- The sign-in expired: the conversation stays in view, and a new sign-in takes the input's place. -->
          <div v-else-if="!auth.authenticated" role="alert" class="border-t border-line bg-card px-5 pb-5 pt-4">
            <p class="text-sm font-medium text-ink">{{ COPY.auth.expiredTitle }}</p>
            <p class="mt-1 text-sm leading-relaxed text-muted">{{ COPY.auth.expiredBody }}</p>
            <button
              type="button"
              class="mt-3 inline-flex cursor-pointer items-center rounded-full bg-ink px-5 py-2.5 text-sm font-medium text-paper transition-colors hover:bg-lingon"
              @click="signIn"
            >
              {{ COPY.actions.signIn }}
            </button>
            <p v-if="signInError" class="mt-2 text-sm text-lingon">{{ signInError }}</p>
          </div>
        </template>
      </template>
    </section>
  </Transition>

  <ChatErrorModal :show="showErrorModal" :error="error" @close="chatStore.closeErrorModal()" @action="runAction" />

  <ChatSessionList v-if="showSessionList" @close="showSessionList = false" @select-session="handleSessionSelect" />
</template>

<script setup lang="ts">
import { COPY } from "~/utils/copy";
import { isAuthFailure, type ErrorAction } from "~/utils/errors";

const auth = useAuthStore();
const chatStore = useChatStore();
const sessionStore = useSessionStore();
const { error, showErrorModal, isLoading, historyStatus, messages } = storeToRefs(chatStore);
const { sessionId } = storeToRefs(sessionStore);

const { isOpen, pendingPrompt, close, ask, takePending, stashPending, resumeStashed } = useConcierge();
const { isMinimized, toggleMinimize, maximize } = useMinimize();

const panel = ref<HTMLElement | null>(null);
const showSessionList = ref(false);
const starting = ref(false);
const signInError = ref("");

/** The panel's body. When the sign-in expires mid-conversation, the conversation stays on screen. */
const view = computed(() => {
  if (auth.authenticated) return sessionId.value || historyStatus.value !== "idle" ? "conversation" : "welcome";
  return auth.expired && messages.value.length > 0 ? "conversation" : "signedOut";
});

function reportStartFailure(e: unknown) {
  chatStore.reportError(e, { message: COPY.errors.startFailed });
}

async function startNewSession() {
  if (starting.value) return;
  starting.value = true;
  try {
    await chatStore.startConversation();
  } catch (e) {
    reportStartFailure(e);
  } finally {
    starting.value = false;
  }
}

/** Send a prompt queued by the header, hero, categories or a product card. */
async function sendPending() {
  if (!isOpen.value || !pendingPrompt.value || !auth.authenticated || isLoading.value || starting.value) return;
  // A conversation being reopened takes the prompt once it has loaded.
  if (historyStatus.value !== "idle") return;
  const prompt = takePending();
  if (!prompt) return;
  if (!sessionId.value) {
    starting.value = true;
    try {
      await chatStore.startConversation();
    } catch (e) {
      // The prompt waits for the new sign-in; any other failure shows the error.
      if (isAuthFailure(e)) ask(prompt);
      reportStartFailure(e);
      return;
    } finally {
      starting.value = false;
    }
  }
  await chatStore.sendMessage(prompt);
}

async function signIn() {
  signInError.value = "";
  stashPending();
  try {
    await auth.login();
  } catch {
    signInError.value = COPY.auth.unavailable;
  }
}

function runAction(action: ErrorAction) {
  chatStore.closeErrorModal();
  if (action === "retry") chatStore.retry();
  else if (action === "newConversation") void startNewSession();
  else void signIn();
}

async function handleSessionSelect(selected: string) {
  if (selected === sessionId.value && historyStatus.value === "idle") return;
  await chatStore.loadSessionMessages(selected);
}

function onEscape(event: KeyboardEvent) {
  // With a Korean IME, Esc first cancels the syllable being composed.
  if (event.isComposing) return;
  close();
}

// Focus moves into the panel when it opens, and back to what opened it when it closes.
let opener: HTMLElement | null = null;

watch(isOpen, async (open) => {
  if (open) {
    const active = document.activeElement;
    opener = active instanceof HTMLElement && active !== document.body ? active : null;
    maximize();
    await nextTick();
    const root = panel.value;
    (root?.querySelector<HTMLElement>("#chat-input, [data-autofocus]") ?? root)?.focus();
  } else {
    await nextTick();
    // The launcher is re-created when the panel closes, so an opener that was the launcher is gone.
    (opener?.isConnected ? opener : document.querySelector<HTMLElement>("[data-chat-launcher]"))?.focus();
    opener = null;
  }
});

watch(
  [isOpen, pendingPrompt, () => auth.authenticated, isLoading, historyStatus],
  () => {
    // Reopen the conversation from before a reload or the sign-in redirect, then send what's queued.
    chatStore.resumeConversation();
    void sendPending();
  },
  { immediate: true },
);

onMounted(resumeStashed);

onUnmounted(() => {
  chatStore.clearMessages();
});
</script>

<style>
.concierge-enter-active {
  transition: opacity 0.3s ease-out, transform 0.35s cubic-bezier(0.2, 0.7, 0.1, 1);
}

.concierge-leave-active {
  transition: opacity 0.2s ease-in, transform 0.2s ease-in;
}

.concierge-enter-from,
.concierge-leave-to {
  opacity: 0;
  transform: translateY(18px) scale(0.985);
}
</style>
