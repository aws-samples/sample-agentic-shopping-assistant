<template>
  <div>
    <HomeHero />
    <HomeCategories />
    <HomeSeasons />
    <HomeHowItWorks />
  </div>
</template>

<script lang="ts" setup>
const brandName = useRuntimeConfig().public.brandName;

useHead({
  title: `${brandName} — Aria와 함께하는 쇼핑`,
  meta: [
    {
      name: "description",
      content: `${brandName}의 쇼핑 어시스턴트 Aria에게 찾는 옷을 말로 설명해보세요.`,
    },
  ],
});
</script>
