<template>
  <div
    class="fixed inset-0 z-[60] flex items-center justify-center bg-[rgb(14_20_18/0.35)] p-4 backdrop-blur-sm"
    @click="emit('close')"
    @keydown.esc="onEscape"
  >
    <div
      ref="dialog"
      role="dialog"
      aria-modal="true"
      aria-labelledby="sessions-title"
      tabindex="-1"
      class="flex max-h-[80vh] w-full max-w-md flex-col overflow-hidden rounded-[14px] border border-line bg-card shadow-2xl focus:outline-none"
      @click.stop
    >
      <div class="flex items-center justify-between border-b border-line px-6 py-5">
        <div>
          <p class="kicker">History</p>
          <h3 id="sessions-title" class="mt-1 font-display text-[1.35rem] font-[360] text-ink">지난 대화</h3>
        </div>
        <button
          type="button"
          class="grid h-8 w-8 cursor-pointer place-items-center rounded-full text-muted transition-colors hover:bg-paper-deep hover:text-ink"
          aria-label="닫기"
          title="닫기"
          @click="emit('close')"
        >
          <i class="pi pi-times text-[0.8rem]" aria-hidden="true" />
        </button>
      </div>

      <div class="flex-1 overflow-y-auto">
        <div v-if="status === 'loading'" class="flex flex-col items-center gap-3 py-12 text-sm text-muted" role="status">
          <i class="pi pi-spin pi-spinner text-lingon" aria-hidden="true" />
          대화를 불러오는 중이에요…
        </div>

        <div v-else-if="status === 'error'" role="alert" class="px-6 py-12 text-center">
          <p class="text-sm text-ink-soft">{{ listError }}</p>
          <button
            type="button"
            class="mt-4 cursor-pointer rounded-full border border-ink px-5 py-2 text-sm text-ink transition-colors hover:bg-paper-deep"
            @click="load"
          >
            {{ COPY.actions.retry }}
          </button>
        </div>

        <div v-else-if="sessions.length === 0" class="px-6 py-12 text-center">
          <BrandMark :ring="false" class="mx-auto h-6 w-6 text-line" />
          <p class="mt-4 text-sm text-ink-soft">아직 대화가 없어요.</p>
          <p class="mt-1 text-xs text-muted">새 대화를 시작해보세요.</p>
        </div>

        <ul v-else class="divide-y divide-line">
          <li
            v-for="(session, index) in sessions"
            :key="session.session_id"
            class="group flex items-center gap-3 px-6 py-4 transition-colors hover:bg-paper"
          >
            <button
              type="button"
              class="flex min-w-0 flex-1 cursor-pointer items-center gap-3 text-left"
              :aria-current="session.session_id === sessionId ? 'true' : undefined"
              @click="selectSession(session.session_id)"
            >
              <i class="pi pi-comments text-sm text-muted group-hover:text-lingon" aria-hidden="true" />
              <span class="min-w-0">
                <span class="block truncate text-[0.92rem] text-ink">{{ titles[index] }}</span>
                <span class="mt-0.5 block text-xs tabular-nums text-muted">
                  <template v-if="session.session_id === sessionId">{{ COPY.history.current }} · </template>ID ···{{ session.session_id.slice(-8) }}
                </span>
              </span>
            </button>
            <button
              type="button"
              class="grid h-8 w-8 cursor-pointer place-items-center rounded-full text-muted transition-colors hover:bg-paper-deep hover:text-lingon"
              :aria-label="COPY.history.deleteLabel(titles[index]!)"
              :title="COPY.history.deleteLabel(titles[index]!)"
              @click.stop="confirmDelete(session)"
            >
              <i class="pi pi-trash text-[0.75rem]" aria-hidden="true" />
            </button>
          </li>
        </ul>
      </div>

      <div class="border-t border-line bg-paper px-6 py-5">
        <button
          type="button"
          :disabled="creating"
          class="flex w-full cursor-pointer items-center justify-center gap-2 rounded-full bg-ink px-5 py-3 text-[0.9rem] font-medium text-paper transition-colors hover:bg-lingon disabled:cursor-wait disabled:opacity-60"
          @click="createNewSession"
        >
          <i :class="creating ? 'pi pi-spin pi-spinner' : 'pi pi-plus'" class="text-xs" aria-hidden="true" />
          새 대화 시작하기
        </button>
        <p v-if="createError" role="alert" class="mt-3 text-center text-xs text-lingon">{{ createError }}</p>
        <p v-else class="mt-3 text-center text-xs text-muted">대화를 고르면 이어서 이야기할 수 있어요.</p>
      </div>
    </div>

    <!-- Delete confirmation -->
    <div
      v-if="pendingDelete"
      class="fixed inset-0 z-[70] flex items-center justify-center bg-[rgb(14_20_18/0.5)] p-4 backdrop-blur-sm"
      @click.stop
    >
      <div
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="delete-title"
        aria-describedby="delete-body"
        class="w-full max-w-sm rounded-[14px] border border-line bg-card p-6 shadow-2xl"
        @click.stop
      >
        <h4 id="delete-title" class="font-display text-[1.2rem] font-[400] text-ink">이 대화를 삭제할까요?</h4>
        <p id="delete-body" class="mt-2 text-sm leading-relaxed text-muted">삭제한 대화는 되돌릴 수 없어요.</p>
        <p v-if="deleteError" role="alert" class="mt-3 text-sm text-lingon">{{ deleteError }}</p>
        <div class="mt-6 flex gap-3">
          <button
            ref="cancelButton"
            type="button"
            class="flex-1 cursor-pointer rounded-full border border-line px-4 py-2.5 text-sm text-ink-soft transition-colors hover:border-ink hover:text-ink"
            @click="cancelDelete"
          >
            취소
          </button>
          <button
            type="button"
            :disabled="deleting"
            class="flex flex-1 cursor-pointer items-center justify-center gap-2 rounded-full bg-lingon px-4 py-2.5 text-sm font-medium text-paper transition-opacity hover:opacity-90 disabled:opacity-50"
            @click="deleteSessionHandler"
          >
            <i v-if="deleting" class="pi pi-spin pi-spinner text-xs" aria-hidden="true" />
            삭제
          </button>
        </div>
      </div>
    </div>
  </div>
</template>

<script setup lang="ts">
import { COPY } from "~/utils/copy";
import { describeError } from "~/utils/errors";
import { sessionTitle, type SessionSummary } from "~/utils/sessions";

const sessionStore = useSessionStore();
const chatStore = useChatStore();
const { sessionId } = storeToRefs(sessionStore);

const emit = defineEmits<{
  (e: "close"): void;
  (e: "selectSession", sessionId: string): void;
}>();

const dialog = ref<HTMLElement | null>(null);
const cancelButton = ref<HTMLButtonElement | null>(null);
const sessions = ref<SessionSummary[]>([]);
const status = ref<"loading" | "ready" | "error">("loading");
const listError = ref("");
const pendingDelete = ref<SessionSummary | null>(null);
const deleting = ref(false);
const deleteError = ref("");
const creating = ref(false);
const createError = ref("");

const titles = computed(() => sessions.value.map((session, index) => sessionTitle(session, index, sessions.value.length)));

async function load() {
  status.value = "loading";
  try {
    // Scoped to the authenticated caller by the JWT, no user id in the path.
    sessions.value = await sessionStore.listSessions();
    status.value = "ready";
  } catch (e) {
    listError.value = describeError(e, { message: COPY.history.listFailed }).message;
    status.value = "error";
  }
}

const selectSession = (id: string) => {
  emit("selectSession", id);
  emit("close");
};

const confirmDelete = async (session: SessionSummary) => {
  pendingDelete.value = session;
  deleteError.value = "";
  await nextTick();
  cancelButton.value?.focus();
};

const deleteSessionHandler = async () => {
  const target = pendingDelete.value;
  if (!target || deleting.value) return;
  deleting.value = true;
  deleteError.value = "";
  try {
    // Deleting the open conversation also empties the transcript.
    await chatStore.deleteConversation(target.session_id);
    sessions.value = sessions.value.filter((s) => s.session_id !== target.session_id);
    pendingDelete.value = null;
    dialog.value?.focus();
  } catch (e) {
    // The confirmation stays open, so deleting again is the retry.
    deleteError.value = describeError(e, { message: COPY.history.deleteFailed }).message;
  } finally {
    deleting.value = false;
  }
};

const cancelDelete = () => {
  pendingDelete.value = null;
  deleteError.value = "";
  dialog.value?.focus();
};

const createNewSession = async () => {
  if (creating.value) return;
  creating.value = true;
  createError.value = "";
  try {
    await chatStore.startConversation();
    emit("close");
  } catch (e) {
    createError.value = describeError(e, { message: COPY.history.createFailed }).message;
  } finally {
    creating.value = false;
  }
};

function onEscape() {
  if (pendingDelete.value) cancelDelete();
  else emit("close");
}

// Focus moves into the dialog and returns to what opened it.
let opener: HTMLElement | null = null;

onMounted(() => {
  opener = document.activeElement instanceof HTMLElement ? document.activeElement : null;
  dialog.value?.focus();
  void load();
});

onBeforeUnmount(() => {
  if (opener?.isConnected) opener.focus();
});
</script>
