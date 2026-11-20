<template>
  <footer class="mt-28 border-t border-line">
    <div class="mx-auto grid max-w-[1280px] gap-12 px-5 py-14 sm:px-8 md:grid-cols-[1.4fr_repeat(3,1fr)]">
      <div>
        <HeaderLogo />
        <p class="mt-5 max-w-xs font-display text-[1.05rem] leading-relaxed text-ink-soft">
          북쪽의 빛처럼,<br />조용하고 오래가는 옷.
        </p>
      </div>
      <div v-for="column in columns" :key="column.title">
        <h2 class="kicker">{{ column.title }}</h2>
        <ul class="mt-4 space-y-2.5 text-[0.9rem] text-ink-soft">
          <li v-for="link in column.links" :key="link">
            <a href="#" class="underline-offset-4 transition-colors hover:text-ink hover:underline">{{ link }}</a>
          </li>
        </ul>
      </div>
    </div>
    <div class="border-t border-line">
      <div
        class="mx-auto flex max-w-[1280px] flex-col gap-2 px-5 py-6 text-xs text-muted sm:flex-row sm:items-center sm:justify-between sm:px-8"
      >
        <p>© 2026 {{ brandName }}. All rights reserved.</p>
        <p>ARIA 쇼핑 어시스턴트 데모 스토어입니다. 상품과 정보는 예시입니다.</p>
      </div>
    </div>
  </footer>
</template>

<script setup lang="ts">
const brandName = useRuntimeConfig().public.brandName;

const columns = [
  { title: "Help", links: ["배송 안내", "교환 · 반품", "문의하기"] },
  { title: "About", links: ["브랜드 이야기", "지속 가능성", "채용"] },
  { title: "Policy", links: ["이용약관", "개인정보처리방침"] },
];
</script>
