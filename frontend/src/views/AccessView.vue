<script setup lang="ts">
import { ref } from "vue";
import { authState, changePassword } from "../services/auth";

const currentPassword = ref("");
const newPassword = ref("");
const confirmation = ref("");
const busy = ref(false);
const error = ref("");

async function submit() {
  if (busy.value) return;
  error.value = "";
  if (newPassword.value !== confirmation.value) {
    error.value = "Passwords do not match";
    return;
  }
  busy.value = true;
  try {
    await changePassword(currentPassword.value, newPassword.value);
  } catch (cause) {
    error.value = cause instanceof Error ? cause.message : "Password change failed";
  } finally {
    currentPassword.value = "";
    newPassword.value = "";
    confirmation.value = "";
    busy.value = false;
  }
}
</script>

<template>
  <section class="page-shell">
    <header class="page-heading">
      <div><h1 class="page-title">Access</h1><p>{{ authState.session?.username }}</p></div>
    </header>
    <form class="auth-form password-form" @submit.prevent="submit">
      <h2 class="section-title">Change Password</h2>
      <v-alert v-if="error" type="error" variant="tonal" role="alert">{{ error }}</v-alert>
      <input :value="authState.session?.username" type="text" autocomplete="username" hidden />
      <v-text-field v-model="currentPassword" label="Current password" type="password" autocomplete="current-password" required maxlength="1024" :disabled="busy" />
      <v-text-field v-model="newPassword" label="New password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="busy" />
      <v-text-field v-model="confirmation" label="Confirm new password" type="password" autocomplete="new-password" required minlength="12" maxlength="1024" :disabled="busy" />
      <v-btn type="submit" color="primary" prepend-icon="mdi-lock-reset" :loading="busy" :disabled="!currentPassword || newPassword.length < 12 || !confirmation">Change Password</v-btn>
    </form>
  </section>
</template>
