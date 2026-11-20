<template>
  <article class="flex gap-3.5 rounded-[10px] border border-line bg-paper p-3 transition-colors hover:border-ink-soft">
    <!-- A fabric swatch in the product's own colour stands in for a photo. -->
    <div
      class="relative h-[84px] w-[66px] shrink-0 overflow-hidden rounded-[5px] shadow-inner"
      :style="{ backgroundColor: swatch }"
      aria-hidden="true"
    >
      <div class="absolute inset-0 bg-[repeating-linear-gradient(45deg,rgb(255_255_255/0.12)_0_2px,transparent_2px_8px)]" />
      <span class="absolute bottom-1.5 left-1.5 rounded-sm bg-card/90 px-1 text-xs font-medium tabular-nums text-ink">
        {{ product.year }}
      </span>
    </div>

    <div class="min-w-0 flex-1">
      <p class="kicker !tracking-[0.16em]">{{ product.masterCategory }} · {{ product.season }}</p>
      <h4 :id="nameId" class="mt-1 font-display text-[1rem] font-[400] leading-snug text-ink">{{ product.productDisplayName }}</h4>
      <p class="mt-1 truncate text-xs text-muted">{{ categoryPath }}</p>
      <div class="mt-2 flex flex-wrap items-center gap-x-2.5 gap-y-1 text-xs text-ink-soft">
        <span class="inline-flex items-center gap-1.5">
          <span class="h-2.5 w-2.5 rounded-full ring-1 ring-line" :style="{ backgroundColor: swatch }" aria-hidden="true" />
          {{ product.baseColour }}
        </span>
        <span class="text-line" aria-hidden="true">|</span>
        <span>{{ product.gender }}</span>
        <span class="rounded-full border border-line px-2 py-px">{{ product.usage }}</span>
      </div>
      <button
        type="button"
        class="mt-2.5 inline-flex cursor-pointer items-center gap-1 text-xs font-medium text-lingon underline-offset-4 hover:underline"
        :aria-describedby="nameId"
        @click="ask(COPY.prompts.similar(product.productDisplayName))"
      >
        비슷한 상품 찾기 <span aria-hidden="true">→</span>
      </button>
    </div>
  </article>
</template>

<script setup lang="ts">
import type { Product } from "~/types/domain";
import { COPY } from "~/utils/copy";

const props = defineProps<{ product: Product }>();
const { ask } = useConcierge();
// Every card has a "비슷한 상품 찾기" button; the product's name tells them apart.
const nameId = useId();

// Catalog colour names → swatch colours. Unknown names fall back to a neutral stone.
const SWATCHES: Record<string, string> = {
  black: "#1f2321",
  white: "#f1ede4",
  "off white": "#ece5d6",
  cream: "#e9dfc8",
  beige: "#d6c5a6",
  khaki: "#b3a37a",
  brown: "#7a5236",
  tan: "#b98c5f",
  grey: "#8d918d",
  "grey melange": "#a3a6a2",
  charcoal: "#3d4240",
  silver: "#b9bcbd",
  navy: "#243552",
  "navy blue": "#243552",
  blue: "#3a5f8a",
  "sea green": "#3f8a76",
  teal: "#2f6f6c",
  green: "#3f6b4d",
  olive: "#6b6a3a",
  red: "#a63a2a",
  maroon: "#6b2a2a",
  burgundy: "#6b2433",
  pink: "#d99aa4",
  peach: "#e8b496",
  orange: "#d07a3c",
  mustard: "#c9a13d",
  yellow: "#dcc155",
  gold: "#b8954a",
  purple: "#6b4f80",
  lavender: "#a99bc4",
  multi: "#9a8c7a",
};

const categoryPath = computed(() =>
  props.product.subCategory === props.product.articleType
    ? props.product.subCategory
    : `${props.product.subCategory} › ${props.product.articleType}`,
);

const swatch = computed(() => SWATCHES[props.product.baseColour.trim().toLowerCase()] ?? "#a8a294");
</script>
