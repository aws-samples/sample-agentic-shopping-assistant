<template>
  <section class="mx-auto mt-28 max-w-[1280px] px-5 sm:px-8" aria-labelledby="how-title">
    <div class="grid gap-14 lg:grid-cols-[0.8fr_1.2fr]">
      <div>
        <p class="kicker">How Aria works</p>
        <h2 id="how-title" class="mt-3 font-display text-[clamp(1.8rem,3.4vw,2.6rem)] font-[340] leading-tight text-ink">
          말 한마디로 시작하는<br />조용한 쇼핑
        </h2>
        <p class="mt-6 max-w-sm text-sm leading-relaxed text-muted">
          Aria는 AI 어시스턴트예요. 답변이 틀릴 수 있으니, 구매 전에는 상품 정보를 한 번 더 확인해주세요.
        </p>
      </div>

      <ol class="grid gap-10 sm:grid-cols-3 sm:gap-6">
        <li v-for="(step, index) in steps" :key="step.title" class="border-t border-ink pt-6">
          <span class="font-display text-[2.6rem] font-[300] leading-none text-lingon">0{{ index + 1 }}</span>
          <h3 class="mt-6 font-display text-[1.2rem] font-[400] text-ink">{{ step.title }}</h3>
          <p class="mt-3 text-sm leading-relaxed text-muted">{{ step.body }}</p>
        </li>
      </ol>
    </div>
  </section>
</template>

<script setup lang="ts">
const steps = [
  { title: "평소 말투로 설명하세요", body: "‘출근할 때 입을 따뜻한 남성 자켓’처럼 말하면 충분해요." },
  { title: "카탈로그에서 찾아요", body: "Aria가 시즌, 성별, 카테고리를 골라 카탈로그를 직접 검색해요." },
  { title: "있는 상품만 추천해요", body: "검색 결과에 없는 상품은 지어내지 않아요. 맞는 상품이 없으면 그렇다고 알려드려요." },
];
</script>
