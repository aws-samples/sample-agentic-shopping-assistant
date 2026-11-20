<template>
  <Transition name="launcher">
    <button
      v-if="!isOpen"
      type="button"
      class="group fixed bottom-6 right-6 z-40 inline-flex cursor-pointer items-center gap-3 rounded-full bg-ink py-3 pl-3.5 pr-5 text-paper shadow-[0_18px_40px_-18px_rgb(0_0_0/0.55)] transition-transform hover:-translate-y-0.5"
      aria-label="Aria에게 물어보기"
      data-chat-launcher
      @click="open()"
    >
      <span class="relative grid h-8 w-8 place-items-center rounded-full bg-paper/10">
        <BrandMark :ring="false" class="h-4 w-4 text-paper transition-transform duration-700 group-hover:rotate-180" />
        <span class="absolute -right-0.5 -top-0.5 h-2.5 w-2.5 rounded-full bg-lingon ring-2 ring-ink" />
      </span>
      <span class="text-left leading-tight">
        <span class="block font-display text-[0.95rem]">Aria</span>
        <span class="block text-xs text-paper/70">무엇이든 물어보세요</span>
      </span>
    </button>
  </Transition>
</template>

<script setup lang="ts">
const { isOpen, open } = useConcierge();
</script>

<style scoped>
.launcher-enter-active,
.launcher-leave-active {
  transition: opacity 0.25s ease, transform 0.25s ease;
}

.launcher-enter-from,
.launcher-leave-to {
  opacity: 0;
  transform: translateY(12px);
}
</style>
