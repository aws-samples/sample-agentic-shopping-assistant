<template>
  <div v-if="isMinimized" class="flex h-full items-center justify-between gap-2 pl-2 pr-2">
    <button type="button" class="flex flex-1 cursor-pointer items-center gap-2.5 text-left" @click="emit('minimize')">
      <span class="grid h-9 w-9 place-items-center rounded-full bg-ink text-paper">
        <BrandMark :ring="false" class="h-4 w-4" />
      </span>
      <span class="font-display text-[1rem] text-ink">Aria</span>
      <span v-if="isLoading" class="h-1.5 w-1.5 animate-pulse rounded-full bg-lingon" aria-hidden="true" />
    </button>
    <button type="button" :class="iconButton" aria-label="창 복원" title="창 복원" @click="emit('minimize')">
      <i class="pi pi-window-maximize text-[0.8rem]" aria-hidden="true" />
    </button>
    <button type="button" :class="iconButton" aria-label="닫기" title="닫기" @click="emit('close')">
      <i class="pi pi-times text-[0.8rem]" aria-hidden="true" />
    </button>
  </div>

  <div v-else class="flex items-center justify-between gap-3 border-b border-line px-5 py-4">
    <div class="flex items-center gap-3">
      <span class="relative grid h-10 w-10 place-items-center rounded-full bg-ink text-paper">
        <BrandMark :ring="false" class="h-[1.1rem] w-[1.1rem]" />
        <span
          v-if="isLoading"
          class="absolute -right-0.5 -top-0.5 h-3 w-3 animate-ping rounded-full bg-lingon"
          aria-hidden="true"
        />
      </span>
      <div>
        <p class="font-display text-[1.15rem] leading-none text-ink">Aria</p>
        <!-- Not a live region: the transcript's status line is the one that speaks. -->
        <p class="mt-1.5 text-xs text-muted">
          {{ isLoading ? "답변을 준비하고 있어요…" : `${brandName} 쇼핑 어시스턴트` }}
        </p>
      </div>
    </div>
    <div class="flex items-center gap-0.5">
      <button
        type="button"
        :class="iconButton"
        :aria-label="isDark ? '밝은 화면' : '어두운 화면'"
        :title="isDark ? '밝은 화면' : '어두운 화면'"
        @click="toggle"
      >
        <i :class="isDark ? 'pi pi-sun' : 'pi pi-moon'" class="text-[0.8rem]" aria-hidden="true" />
      </button>
      <button
        v-if="auth.authenticated"
        type="button"
        :class="iconButton"
        aria-label="지난 대화"
        title="지난 대화"
        @click="emit('showSessions')"
      >
        <i class="pi pi-history text-[0.8rem]" aria-hidden="true" />
      </button>
      <button type="button" :class="iconButton" aria-label="최소화" title="최소화" @click="emit('minimize')">
        <i class="pi pi-minus text-[0.8rem]" aria-hidden="true" />
      </button>
      <button type="button" :class="iconButton" aria-label="닫기" title="닫기" @click="emit('close')">
        <i class="pi pi-times text-[0.8rem]" aria-hidden="true" />
      </button>
    </div>
  </div>
</template>

<script setup lang="ts">
const emit = defineEmits<{
  (e: "close"): void;
  (e: "minimize"): void;
  (e: "showSessions"): void;
}>();

const brandName = useRuntimeConfig().public.brandName;
const auth = useAuthStore();
const { isLoading } = storeToRefs(useChatStore());
const { isMinimized } = useMinimize();
const { isDark, toggle } = useTheme();

const iconButton =
  "grid h-8 w-8 cursor-pointer place-items-center rounded-full text-muted transition-colors hover:bg-paper-deep hover:text-ink";
</script>
