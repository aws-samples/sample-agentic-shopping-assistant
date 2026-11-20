<template>
  <!-- A native dialog closes itself on Esc and through the backdrop form; its close event keeps the store in step. -->
  <dialog
    ref="modalElement"
    class="modal modal-bottom sm:modal-middle"
    aria-labelledby="error-title"
    aria-describedby="error-message"
    @close="emit('close')"
  >
    <div class="modal-box max-w-sm rounded-[14px] border border-line bg-card p-0">
      <div class="flex items-start gap-3 px-6 pb-2 pt-6">
        <span class="mt-0.5 grid h-8 w-8 shrink-0 place-items-center rounded-full bg-lingon/10 text-lingon">
          <i class="pi pi-exclamation-circle text-sm" aria-hidden="true" />
        </span>
        <div>
          <h3 id="error-title" class="font-display text-[1.15rem] font-[400] text-ink">문제가 생겼어요</h3>
          <p id="error-message" class="mt-1.5 text-sm leading-relaxed text-muted">{{ error?.message }}</p>
        </div>
      </div>
      <div class="modal-action mt-4 gap-2 border-t border-line px-6 py-4">
        <button
          v-if="action"
          type="button"
          class="cursor-pointer rounded-full border border-line px-5 py-2.5 text-sm text-ink-soft transition-colors hover:border-ink hover:text-ink"
          @click="emit('close')"
        >
          {{ COPY.actions.dismiss }}
        </button>
        <button
          type="button"
          class="cursor-pointer rounded-full bg-ink px-6 py-2.5 text-sm font-medium text-paper transition-colors hover:bg-lingon"
          @click="action ? emit('action', action) : emit('close')"
        >
          {{ action ? COPY.actions[action] : COPY.actions.confirm }}
        </button>
      </div>
    </div>
    <form method="dialog" class="modal-backdrop">
      <button>닫기</button>
    </form>
  </dialog>
</template>

<script setup lang="ts">
import { COPY } from "~/utils/copy";
import type { ChatError, ErrorAction } from "~/utils/errors";

const props = defineProps<{
  show: boolean;
  error: ChatError | null;
}>();

const emit = defineEmits<{
  (e: "close"): void;
  (e: "action", action: ErrorAction): void;
}>();

const modalElement = ref<HTMLDialogElement | null>(null);
const action = computed(() => props.error?.action);

watch(
  () => props.show,
  (show) => {
    if (!modalElement.value) return;
    if (show && !modalElement.value.open) modalElement.value.showModal();
    else if (!show && modalElement.value.open) modalElement.value.close();
  },
);

onMounted(() => {
  if (props.show && modalElement.value && !modalElement.value.open) modalElement.value.showModal();
});
</script>
