<script setup lang="ts">
import { COPY } from "~/utils/copy";

const auth = useAuthStore();
const sessionStore = useSessionStore();
const { stashPending } = useConcierge();
const error = ref("");

onMounted(async () => {
  try {
    await auth.restore();
  } catch {
    /* Sign-in shows configuration errors. */
  }
});

async function act() {
  try {
    error.value = "";
    if (auth.authenticated) {
      sessionStore.clearActive();
      await auth.logout();
    } else {
      // Like the panel's button: a prompt asked before signing in is sent after it.
      stashPending();
      await auth.login();
    }
  } catch {
    error.value = COPY.auth.unavailable;
  }
}
</script>

<template>
  <div class="relative flex items-center">
    <button
      type="button"
      class="cursor-pointer text-[0.85rem] text-ink-soft underline-offset-4 transition-colors hover:text-ink hover:underline"
      @click="act"
    >
      {{ auth.authenticated ? "로그아웃" : "로그인" }}
    </button>
    <p
      v-if="error"
      role="alert"
      class="absolute right-0 top-full mt-3 w-64 rounded-md border border-line bg-card px-3 py-2 text-xs text-lingon shadow-lg"
    >
      {{ error }}
    </p>
  </div>
</template>
