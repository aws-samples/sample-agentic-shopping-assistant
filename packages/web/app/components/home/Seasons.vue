<template>
  <section class="mt-36 bg-paper-deep" aria-labelledby="seasons-title">
    <div class="mx-auto max-w-[1280px] px-5 py-20 sm:px-8">
      <p class="kicker">Seasonal edit</p>
      <h2 id="seasons-title" class="mt-3 font-display text-[clamp(1.8rem,3.4vw,2.6rem)] font-[340] text-ink">
        계절로 고르기
      </h2>

      <ul class="mt-12 grid grid-cols-2 border-t border-line md:grid-cols-4">
        <li v-for="season in seasons" :key="season.en" class="border-b border-line md:border-b-0 md:border-r md:last:border-r-0">
          <button
            type="button"
            class="group relative flex w-full cursor-pointer flex-col gap-6 overflow-hidden px-1 py-8 text-left md:px-6"
            @click="ask(season.prompt)"
          >
            <span
              class="absolute inset-x-0 bottom-0 h-1 origin-left scale-x-0 transition-transform duration-500 group-hover:scale-x-100"
              :style="{ backgroundColor: season.tone }"
            />
            <span class="kicker">{{ season.en }}</span>
            <span class="font-display text-[clamp(2.4rem,4.8vw,3.6rem)] font-[300] leading-none text-ink">{{ season.ko }}</span>
            <span class="text-sm text-muted transition-colors group-hover:text-ink">{{ season.note }} →</span>
          </button>
        </li>
      </ul>
    </div>
  </section>
</template>

<script setup lang="ts">
const { ask } = useConcierge();

const seasons = [
  { ko: "봄", en: "Spring", note: "가벼운 레이어드", tone: "#8fb3a6", prompt: "봄에 어울리는 옷 추천해줘" },
  { ko: "여름", en: "Summer", note: "시원한 린넨과 코튼", tone: "#d9b56f", prompt: "여름에 입기 좋은 옷 추천해줘" },
  { ko: "가을", en: "Fall", note: "따뜻한 톤의 니트", tone: "#b5653f", prompt: "가을 코디 추천해줘" },
  { ko: "겨울", en: "Winter", note: "울과 다운 아우터", tone: "#24413a", prompt: "겨울 아우터 추천해줘" },
];
</script>
