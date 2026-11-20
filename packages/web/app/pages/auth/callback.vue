<script setup lang="ts">
import { COPY } from "~/utils/copy";
import { callbackErrorMessage } from "~/utils/errors";

const auth = useAuthStore();
const { stashPending } = useConcierge();
const error = ref("");

onMounted(async () => {
  try {
    await auth.callback();
  } catch (e) {
    error.value = callbackErrorMessage(e);
    return;
  }
  // Replace the callback in history, so Back doesn't replay a spent authorization code.
  await navigateTo("/", { replace: true });
});

async function signInAgain() {
  // Keep a prompt queued before sign-in for after it.
  stashPending();
  try {
    await auth.login();
  } catch {
    error.value = COPY.auth.unavailable;
  }
}
</script>

<template>
  <section class="mx-auto flex min-h-[60vh] max-w-md flex-col items-center justify-center gap-5 px-6 text-center">
    <BrandMark :ring="false" :class="['h-8 w-8 text-lingon', { 'animate-spin [animation-duration:2.4s]': !error }]" />
    <p role="status" class="font-display text-[1.25rem] text-ink">{{ error || COPY.auth.callbackPending }}</p>
    <button
      v-if="error"
      type="button"
      class="inline-flex cursor-pointer items-center rounded-full bg-ink px-6 py-3 text-[0.93rem] font-medium text-paper transition-colors hover:bg-lingon"
      @click="signInAgain"
    >
      {{ COPY.actions.signIn }}
    </button>
    <NuxtLink to="/" replace class="text-sm text-muted underline underline-offset-4 hover:text-ink">홈으로 돌아가기</NuxtLink>
  </section>
</template>
