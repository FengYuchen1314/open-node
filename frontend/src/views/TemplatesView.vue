<script setup lang="ts">
import TemplatesWorkspace from "../components/TemplatesWorkspace.vue";
</script>

<template>
  <main class="templates-page"><TemplatesWorkspace /></main>
</template>

<style scoped>
.templates-page { max-width: 1440px; margin: 0 auto; padding: 28px; }
@media (max-width: 600px) { .templates-page { padding: 20px 16px 40px; } }
</style>
