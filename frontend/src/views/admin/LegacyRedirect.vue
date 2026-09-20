<script setup lang="ts">
import { onMounted, ref } from 'vue'
import { useI18n } from '../../composables/useI18n'

const { t } = useI18n()
const currentPath = ref(window.location.pathname)

onMounted(() => {
  // Redirect to legacy admin.html for pages not yet migrated
  window.location.href = '/admin/legacy.html#' + currentPath.value
})
</script>

<template>
  <div class="py-12 text-center">
    <p class="text-xs text-[var(--ink-3)]">{{ t('admin.legacy.redirecting') }}</p>
  </div>
</template>
