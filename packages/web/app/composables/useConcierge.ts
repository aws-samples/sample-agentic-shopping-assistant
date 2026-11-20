import { readonly, ref } from "vue";

/**
 * The Aria concierge panel, shared by every entry point on the page (header, hero,
 * categories, seasons, product cards, launcher). Module-level state keeps a single
 * chat panel, the same pattern as useMinimize.
 *
 * `ask` queues a prompt; ChatBot sends it once a session exists and the shopper is
 * signed in. A prompt asked while signed out is stashed in sessionStorage (UI state
 * only, never credentials) so it can resume after the sign-in redirect.
 */

const PENDING_KEY = "nordly.pendingPrompt";

const isOpen = ref(false);
const pendingPrompt = ref<string | null>(null);

export const useConcierge = () => {
  const open = () => {
    isOpen.value = true;
  };

  const close = () => {
    isOpen.value = false;
  };

  const ask = (prompt: string) => {
    const text = prompt.trim().slice(0, 8000);
    if (!text) return open();
    pendingPrompt.value = text;
    isOpen.value = true;
  };

  /** Hand the queued prompt to the sender exactly once. */
  const takePending = () => {
    const prompt = pendingPrompt.value;
    pendingPrompt.value = null;
    return prompt;
  };

  /** Keep the queued prompt across the sign-in redirect. */
  const stashPending = () => {
    if (pendingPrompt.value) sessionStorage.setItem(PENDING_KEY, pendingPrompt.value);
  };

  /** Re-queue a prompt stashed before the sign-in redirect, if any. */
  const resumeStashed = () => {
    const stashed = sessionStorage.getItem(PENDING_KEY);
    if (!stashed) return;
    sessionStorage.removeItem(PENDING_KEY);
    ask(stashed);
  };

  return {
    isOpen: readonly(isOpen),
    pendingPrompt: readonly(pendingPrompt),
    open,
    close,
    ask,
    takePending,
    stashPending,
    resumeStashed,
  };
};
