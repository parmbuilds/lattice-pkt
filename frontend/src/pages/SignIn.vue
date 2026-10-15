<script setup lang="ts">
import { useRouter } from 'vue-router'
import AuthFrame from '../components/AuthFrame.vue'
import SignInForm from '../components/SignInForm.vue'
import SignUpForm from '../components/SignUpForm.vue'

defineProps<{ mode: 'signin' | 'signup' }>()
const router = useRouter()
const done = () => router.push('/')
</script>

<template>
  <AuthFrame>
    <template v-if="mode === 'signin'">
      <SignInForm @done="done" />
      <p class="mt-6 text-sm text-graphite">New to Lattice? <RouterLink to="/signup">Create an account</RouterLink></p>
    </template>
    <template v-else>
      <SignUpForm @done="done" />
      <p class="mt-6 text-sm text-graphite">Already have an account? <RouterLink to="/signin">Sign in</RouterLink></p>
    </template>
  </AuthFrame>
</template>
