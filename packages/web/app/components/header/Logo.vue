<script setup lang="ts">
const brandName = useRuntimeConfig().public.brandName;
</script>

<template>
  <NuxtLink to="/" :aria-label="`${brandName} 홈`" class="group flex items-center gap-2.5">
    <BrandMark class="h-5 w-5 text-ink transition-transform duration-700 group-hover:rotate-90" />
    <span class="font-display text-[1.3rem] font-[360] tracking-[0.34em] text-ink">{{ brandName }}</span>
  </NuxtLink>
</template>
