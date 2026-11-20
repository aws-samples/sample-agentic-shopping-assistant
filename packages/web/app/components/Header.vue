<template>
  <header class="sticky top-0 z-30 border-b border-line bg-paper/85 backdrop-blur-md">
    <div class="mx-auto flex h-16 max-w-[1280px] items-center gap-6 px-5 sm:px-8">
      <nav aria-label="카테고리" class="hidden flex-1 items-center gap-7 text-[0.9rem] text-ink-soft md:flex">
        <button
          v-for="item in nav"
          :key="item.label"
          type="button"
          class="group relative cursor-pointer py-1 transition-colors hover:text-ink"
          @click="ask(item.prompt)"
        >
          {{ item.label }}
          <span
            class="absolute inset-x-0 -bottom-0.5 h-px origin-left scale-x-0 bg-lingon transition-transform duration-300 group-hover:scale-x-100"
          />
        </button>
      </nav>

      <HeaderLogo class="md:flex-none" />

      <div class="flex flex-1 items-center justify-end gap-2 sm:gap-4">
        <button
          type="button"
          class="group inline-flex cursor-pointer items-center gap-2 rounded-full border border-ink px-3.5 py-1.5 text-[0.85rem] font-medium text-ink transition-colors hover:bg-ink hover:text-paper"
          @click="open()"
        >
          <BrandMark :ring="false" class="h-3.5 w-3.5 text-lingon transition-transform duration-500 group-hover:rotate-45" />
          <span>Aria<span class="hidden sm:inline">에게 묻기</span></span>
        </button>
        <HeaderAccount />
      </div>
    </div>
  </header>
</template>

<script setup lang="ts">
const { ask, open } = useConcierge();

const nav = [
  { label: "여성", prompt: "여성 의류 추천해줘" },
  { label: "남성", prompt: "남성 의류 추천해줘" },
  { label: "신발", prompt: "신발 추천해줘" },
  { label: "액세서리", prompt: "액세서리 추천해줘" },
];
</script>
