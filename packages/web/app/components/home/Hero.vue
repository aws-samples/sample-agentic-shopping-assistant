<template>
  <section class="relative mx-auto grid max-w-[1280px] gap-14 px-5 pb-24 pt-14 sm:px-8 lg:grid-cols-[1.12fr_0.88fr] lg:gap-10 lg:pt-24">
    <div class="relative">
      <p class="kicker rise" style="--i: 0">{{ brandName }} · Winter Edit 2026</p>

      <h1
        class="rise mt-7 font-display text-[clamp(2.75rem,6.4vw,5.6rem)] font-[320] leading-[1.02] tracking-[-0.025em] text-ink"
        style="--i: 1"
      >
        말하면,<br />
        <span class="relative inline-block text-lingon">
          찾아
          <svg
            class="absolute -bottom-2 left-0 w-full text-lingon/60"
            viewBox="0 0 200 12"
            preserveAspectRatio="none"
            aria-hidden="true"
          >
            <path d="M2 8 C 50 2, 120 12, 198 4" stroke="currentColor" stroke-width="2.2" fill="none" stroke-linecap="round" />
          </svg>
        </span>드립니다.
      </h1>

      <p class="rise mt-8 max-w-[34rem] text-[1.06rem] leading-[1.75] text-muted" style="--i: 2">
        Aria는 {{ brandName }}의 쇼핑 어시스턴트예요. 찾는 옷을 평소 말투로 설명하면, 카탈로그에
        실제로 있는 상품만 골라 보여드려요.
      </p>

      <form class="rise mt-11" style="--i: 3" @submit.prevent="submit">
        <label for="hero-ask" class="label">Aria에게 물어보기</label>
        <div
          class="mt-3 flex items-center gap-4 border-b-2 border-ink pb-3 transition-colors focus-within:border-lingon"
        >
          <BrandMark :ring="false" class="h-6 w-6 shrink-0 text-lingon" />
          <input
            id="hero-ask"
            v-model="query"
            type="text"
            maxlength="8000"
            autocomplete="off"
            :placeholder="placeholder"
            class="min-w-0 flex-1 bg-transparent font-display text-[clamp(1.2rem,2.3vw,1.85rem)] font-[340] text-ink placeholder:text-muted focus:outline-none"
          />
          <button
            type="submit"
            class="shrink-0 cursor-pointer rounded-full bg-ink px-5 py-2.5 text-[0.9rem] font-medium text-paper transition-colors hover:bg-lingon"
          >
            물어보기
          </button>
        </div>
      </form>

      <div class="rise mt-6 flex flex-wrap items-center gap-2" style="--i: 4">
        <span class="label mr-1">예시</span>
        <button
          v-for="example in examples"
          :key="example"
          type="button"
          class="cursor-pointer rounded-full border border-line bg-card/60 px-3.5 py-1.5 text-[0.85rem] text-ink-soft transition-colors hover:border-ink hover:text-ink"
          @click="ask(example)"
        >
          {{ example }}
        </button>
      </div>
    </div>

    <!-- Fabric swatches: an editorial still life drawn entirely in CSS. -->
    <div class="relative mx-auto aspect-[4/5] w-full max-w-[460px] lg:max-w-none" aria-hidden="true">
      <p
        class="rise absolute -left-1 top-4 font-display text-[0.8rem] tracking-[0.18em] text-muted [writing-mode:vertical-rl]"
        style="--i: 3"
      >
        60°N — COLLECTION 2026
      </p>
      <div
        class="rise absolute right-0 top-0 h-[74%] w-[70%] rotate-[3deg] rounded-[3px] weave-twill shadow-[0_40px_70px_-40px_rgb(0_0_0/0.6)]"
        style="--i: 2"
      >
        <div class="absolute bottom-5 left-5 rounded-[2px] bg-card/95 px-3.5 py-2.5 shadow-sm">
          <p class="kicker !tracking-[0.18em]">No. 014</p>
          <p class="mt-0.5 font-display text-[1.05rem] text-ink">Wool · Pine</p>
        </div>
      </div>
      <div
        class="rise absolute bottom-[9%] left-[4%] h-[50%] w-[56%] -rotate-[5deg] rounded-[3px] weave-knit shadow-[0_40px_70px_-40px_rgb(0_0_0/0.55)]"
        style="--i: 3"
      >
        <div class="absolute right-4 top-4 rounded-[2px] bg-card/95 px-3.5 py-2.5 shadow-sm">
          <p class="kicker !tracking-[0.18em]">No. 027</p>
          <p class="mt-0.5 font-display text-[1.05rem] text-ink">Knit · Oat</p>
        </div>
      </div>
      <div
        class="rise absolute bottom-0 right-[6%] h-[30%] w-[36%] rotate-[9deg] rounded-[3px] weave-herringbone shadow-[0_30px_60px_-30px_rgb(0_0_0/0.6)]"
        style="--i: 4"
      >
        <p class="absolute bottom-3 left-3 font-display text-[0.9rem] text-[#f6e9e4]">Tweed · Lingon</p>
      </div>
      <BrandMark
        :ring="false"
        class="rise absolute left-[12%] top-[16%] h-9 w-9 text-lingon drop-shadow-sm"
        style="--i: 5"
      />
    </div>
  </section>
</template>

<script setup lang="ts">
const brandName = useRuntimeConfig().public.brandName;
const { ask } = useConcierge();

const examples = ["출근할 때 입을 남성 겨울 자켓", "봄에 신을 운동화", "여름 데이트룩 셔츠"];

const query = ref("");
const placeholderIndex = ref(0);
const placeholder = computed(() => `${examples[placeholderIndex.value]}…`);

let timer: ReturnType<typeof setInterval> | undefined;
onMounted(() => {
  timer = setInterval(() => {
    placeholderIndex.value = (placeholderIndex.value + 1) % examples.length;
  }, 3200);
});
onUnmounted(() => clearInterval(timer));

function submit() {
  const text = query.value.trim();
  if (!text) return;
  ask(text);
  query.value = "";
}
</script>
