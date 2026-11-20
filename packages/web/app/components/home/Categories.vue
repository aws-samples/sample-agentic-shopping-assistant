<template>
  <section class="mx-auto max-w-[1280px] px-5 sm:px-8" aria-labelledby="categories-title">
    <div class="flex items-end justify-between gap-6 border-t border-line pt-10">
      <div>
        <p class="kicker">Shop by category</p>
        <h2 id="categories-title" class="mt-3 font-display text-[clamp(1.8rem,3.4vw,2.6rem)] font-[340] text-ink">
          카테고리에서 시작하기
        </h2>
      </div>
      <p class="hidden max-w-xs text-sm leading-relaxed text-muted md:block">
        카테고리를 고르면 Aria가 바로 대화를 시작해요. 원하는 조건을 이어서 말해보세요.
      </p>
    </div>

    <div class="mt-12 grid gap-8 md:grid-cols-3 md:gap-6">
      <button
        v-for="(category, index) in categories"
        :key="category.en"
        type="button"
        class="group cursor-pointer text-left md:[&:nth-child(2)]:translate-y-12"
        @click="ask(category.prompt)"
      >
        <div
          :class="[
            'relative aspect-[4/5] overflow-hidden rounded-[3px] shadow-[0_30px_60px_-40px_rgb(0_0_0/0.5)] transition-transform duration-500 group-hover:-translate-y-1',
            category.weave,
          ]"
        >
          <span :class="['absolute left-5 top-5 font-display text-[0.95rem]', category.numeral]">0{{ index + 1 }}</span>
          <span
            class="absolute bottom-5 right-5 grid h-11 w-11 place-items-center rounded-full bg-card/95 text-ink transition-transform duration-500 group-hover:rotate-45"
          >
            <i class="pi pi-arrow-up-right text-sm" aria-hidden="true" />
          </span>
        </div>
        <div class="mt-5 flex items-baseline justify-between gap-4">
          <h3 class="font-display text-[1.6rem] font-[360] text-ink">{{ category.ko }}</h3>
          <span class="kicker">{{ category.en }}</span>
        </div>
        <p class="mt-1.5 text-sm text-muted">{{ category.note }}</p>
      </button>
    </div>
  </section>
</template>

<script setup lang="ts">
const { ask } = useConcierge();

const categories = [
  { ko: "의류", en: "Apparel", note: "아우터 · 셔츠 · 니트", weave: "weave-twill", numeral: "text-white/80", prompt: "겨울 아우터 추천해줘" },
  { ko: "신발", en: "Footwear", note: "스니커즈 · 부츠 · 샌들", weave: "weave-knit", numeral: "text-[#1a211e]/80", prompt: "봄에 신을 운동화 보여줘" },
  { ko: "액세서리", en: "Accessories", note: "가방 · 시계 · 모자", weave: "weave-herringbone", numeral: "text-white/80", prompt: "데일리로 쓰기 좋은 가방 추천해줘" },
];
</script>
