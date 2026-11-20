<template>
  <div class="flex min-h-screen flex-col">
    <a
      href="#main"
      class="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-[70] focus:rounded-full focus:bg-ink focus:px-4 focus:py-2 focus:text-paper"
      >본문으로 건너뛰기</a
    >
    <Header />
    <main id="main" class="flex-1">
      <slot />
    </main>
    <Footer />
    <ChatLauncher />
    <ChatBot />
  </div>
</template>

<script setup lang="ts">
const { init } = useTheme();
onMounted(init);

</script>
