<template>
  <div class="border-t border-line bg-card px-4 pb-4 pt-3">
    <div v-if="isLoading" class="mb-2.5 flex justify-center">
      <button
        type="button"
        class="inline-flex cursor-pointer items-center gap-1.5 rounded-full border border-line px-3 py-1 text-xs text-ink-soft transition-colors hover:border-ink hover:text-ink"
        @click="stop"
      >
        <i class="pi pi-stop-circle text-[0.7rem]" aria-hidden="true" />
        응답 멈추기
      </button>
    </div>
    <form
      class="flex items-center gap-2 rounded-full border border-line bg-paper py-1.5 pl-4 pr-1.5 transition-colors focus-within:border-ink"
      @submit.prevent="handleSubmit"
    >
      <label for="chat-input" class="sr-only">Aria에게 보낼 메시지</label>
      <!-- Never disabled: disabling it would drop the shopper's focus mid-sentence. Sending is blocked instead.
           16px below `sm`, so iOS doesn't zoom in on focus. -->
      <input
        id="chat-input"
        ref="inputRef"
        v-model="inputText"
        type="text"
        maxlength="8000"
        autocomplete="off"
        enterkeyhint="send"
        placeholder="찾는 옷을 설명해 주세요…"
        class="min-w-0 flex-1 bg-transparent text-base text-ink placeholder:text-muted focus:outline-none sm:text-[0.93rem]"
      />
      <button
        type="submit"
        :disabled="busy || !inputText.trim()"
        aria-label="보내기"
        class="grid h-9 w-9 shrink-0 cursor-pointer place-items-center rounded-full bg-ink text-paper transition-colors hover:bg-lingon disabled:cursor-not-allowed disabled:opacity-35"
      >
        <i class="pi pi-arrow-up text-[0.8rem]" aria-hidden="true" />
      </button>
    </form>
    <p class="mt-2 text-center text-xs text-muted">Aria는 실수할 수 있어요. 구매 전에 상품 정보를 확인해 주세요.</p>
  </div>
</template>

<script setup lang="ts">
const chatStore = useChatStore();
const { isLoading, historyLoading } = storeToRefs(chatStore);

const inputText = ref("");
const inputRef = ref<HTMLInputElement | null>(null);

/** A reply is streaming or a conversation is loading: typing goes on, sending waits. */
const busy = computed(() => isLoading.value || historyLoading.value);

async function handleSubmit() {
  if (!inputText.value.trim() || busy.value) return;
  const message = inputText.value;
  inputText.value = "";
  await chatStore.sendMessage(message);
}

function stop() {
  chatStore.cancel();
  // The Stop button disappears with the reply; keep focus in the conversation.
  inputRef.value?.focus();
}

onMounted(() => {
  nextTick(() => inputRef.value?.focus());
});
</script>
