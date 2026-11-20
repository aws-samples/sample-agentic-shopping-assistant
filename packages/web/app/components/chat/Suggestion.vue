<template>
  <div class="flex flex-wrap gap-2">
    <button
      v-for="(suggestion, index) in suggestions"
      :key="index"
      type="button"
      :class="[
        'cursor-pointer rounded-full border px-3.5 py-1.5 text-[0.85rem] transition-colors',
        suggestion.highlighted
          ? 'border-ink/25 bg-card text-ink hover:border-ink'
          : 'border-line text-ink-soft hover:border-ink hover:text-ink',
      ]"
      @click="emit('select', suggestion)"
    >
      {{ suggestion.text }}
    </button>
  </div>
</template>

<script setup lang="ts">
interface Suggestion {
  text: string;
  highlighted?: boolean;
}

defineProps<{
  suggestions: Suggestion[];
}>();

const emit = defineEmits<{
  (e: "select", suggestion: Suggestion): void;
}>();
</script>
