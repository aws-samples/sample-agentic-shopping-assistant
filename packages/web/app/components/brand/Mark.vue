<!--
  The NORDLY / Aria mark: a four-point north star inside a ring.
  Drawn with currentColor so it follows the surrounding text colour.
-->
<template>
  <svg viewBox="0 0 32 32" fill="none" aria-hidden="true" focusable="false">
    <circle v-if="ring" cx="16" cy="16" r="14.5" stroke="currentColor" stroke-width="1.2" />
    <path
      d="M16 4.5 L18.1 13.9 L27.5 16 L18.1 18.1 L16 27.5 L13.9 18.1 L4.5 16 L13.9 13.9 Z"
      fill="currentColor"
    />
  </svg>
</template>

<script setup lang="ts">
withDefaults(defineProps<{ ring?: boolean }>(), { ring: true });
</script>
