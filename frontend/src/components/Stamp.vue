<script setup lang="ts">
import type { StampKind } from '../session'

defineProps<{ kind: StampKind; large?: boolean }>()
</script>

<template>
  <span class="stamp" :class="[`stamp-${kind}`, { 'stamp-lg': large }]"><slot /></span>
</template>
