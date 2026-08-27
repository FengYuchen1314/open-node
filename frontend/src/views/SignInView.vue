<script setup lang="ts">
import { ref } from "vue";
import { authState, loadSession, signIn } from "../services/auth";

const username = ref("");
const password = ref("");
const busy = ref(false);
const error = ref("");

async function submit() {
  if (busy.value) return;
  busy.value = true;
  error.value = "";
  try {
    await signIn(username.value, password.value);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Sign-in failed";
  } finally {
    password.value = "";
    busy.value = false;
  }
}
</script>

<template>
  <section class="auth-page">
    <div class="auth-workspace">
      <div class="brand-mark" aria-hidden="true">ON</div>
      <h1>Open Node</h1>
      <h2>Administrator Sign-In</h2>
      <v-alert v-if="authState.error" type="error" variant="tonal">
        {{ authState.error }}
        <v-btn icon="mdi-refresh" title="Retry connection" aria-label="Retry connection" @click="loadSession()" />
      </v-alert>
      <v-alert v-else-if="authState.session?.configured === false" type="warning" variant="tonal">
        Administrator account is not configured.
      </v-alert>
      <form v-else class="auth-form" @submit.prevent="submit">
        <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
        <v-text-field v-model="username" label="Username" autocomplete="username" autofocus required maxlength="64" :disabled="busy" />
        <v-text-field v-model="password" label="Password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" />
        <v-btn type="submit" color="primary" prepend-icon="mdi-login" :loading="busy" :disabled="!username || !password">Sign In</v-btn>
      </form>
    </div>
  </section>
</template>
