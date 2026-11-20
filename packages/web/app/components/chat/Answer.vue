<template>
  <div class="space-y-3">
    <template v-for="(part, index) in parts" :key="index">
      <!-- renderAnswer() runs with HTML, images and links off, and the citation plugin escapes
           product names, so neither model output nor catalog fields can inject markup or URLs. -->
      <div
        v-if="part.kind === 'text'"
        class="nd-prose text-[0.94rem] leading-[1.7] text-ink"
        v-html="renderAnswer(part.text, env)"
      />
      <ChatProductCard v-else-if="part.product" :product="part.product" />
      <!-- An id the search did not return. Restored history has no product data, so its
           citations are dropped quietly instead. -->
      <p
        v-else-if="!restored"
        class="rounded-[10px] border border-dashed border-line px-3 py-2 text-xs text-muted"
      >
        검색 결과에서 확인할 수 없는 상품이에요.
      </p>
    </template>
  </div>
</template>

<script setup lang="ts">
import type { Product } from "~/types/domain";
import { splitAnswer, type CitationEnv } from "~/utils/citations";
import { renderAnswer } from "~/utils/markdown";

const props = defineProps<{
  text: string;
  products: ReadonlyMap<string, Product>;
  restored: boolean;
  streaming: boolean;
}>();

type Part = { kind: "text"; text: string } | { kind: "card"; id: string; product?: Product };

const parts = computed<Part[]>(() =>
  splitAnswer(props.text, props.streaming).map((segment) =>
    segment.kind === "card" ? { ...segment, product: props.products.get(segment.id) } : segment,
  ),
);

const env = computed<CitationEnv>(() => ({ products: props.products, restored: props.restored }));
</script>
