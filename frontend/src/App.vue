<script setup lang="ts">
import { computed, ref, watch } from "vue";
import { useRoute } from "vue-router";
import { useDisplay } from "vuetify";
import { authState, loadSession, signOut } from "./services/auth";
import SignInView from "./views/SignInView.vue";

const { mobile } = useDisplay();
const drawer = ref(!mobile.value);
const logoutError = ref("");
const route = useRoute();
const subscriber = computed(() => route.meta.subscriber === true);
watch(subscriber, (value) => { if (!value && !authState.ready) void loadSession(); }, { immediate: true });
watch(mobile, (value) => { drawer.value = !value; });

async function logout() {
  logoutError.value = "";
  try {
    await signOut();
  } catch (cause) {
    logoutError.value = cause instanceof Error ? cause.message : "Sign-out failed";
    await loadSession();
  }
}
</script>

<template>
  <v-app>
    <v-navigation-drawer v-if="!subscriber && authState.session?.authenticated" v-model="drawer" width="248" :permanent="!mobile" :temporary="mobile">
      <div class="brand-block">
        <div class="brand-mark">ON</div>
        <div>
          <div class="brand-title">Open Node</div>
          <div class="brand-subtitle">Control plane</div>
        </div>
        <v-btn v-if="mobile" icon="mdi-close" size="small" variant="text" title="Close navigation" aria-label="Close navigation" @click="drawer = false" />
      </div>

      <v-list density="compact" nav>
        <v-list-item
          prepend-icon="mdi-view-dashboard-outline"
          title="Overview"
          value="overview"
          to="/"
          exact
        />
        <v-list-item
          prepend-icon="mdi-package-variant-closed"
          title="Subscriptions"
          value="subscriptions"
          to="/subscriptions"
        />
        <v-list-item
          prepend-icon="mdi-clipboard-text-clock-outline"
          title="Changes"
          value="changes"
          to="/changes"
        />
        <v-list-item prepend-icon="mdi-file-cog-outline" title="Config" value="config" to="/config" />
        <v-list-item prepend-icon="mdi-certificate-outline" title="Certificates" value="certificates" to="/certificates" />
        <v-list-item prepend-icon="mdi-chart-line" title="Probe" value="probe" to="/probe" />
        <v-list-item prepend-icon="mdi-shield-check-outline" title="Access" value="access" to="/access" />
      </v-list>
    </v-navigation-drawer>

    <v-app-bar v-if="!subscriber && authState.session?.authenticated" flat border>
      <v-app-bar-nav-icon v-if="mobile" aria-label="Toggle navigation" @click="drawer = !drawer" />
      <v-app-bar-title>Open Node</v-app-bar-title>
      <template #append>
        <v-chip class="edition-badge" color="success" variant="tonal" prepend-icon="mdi-lock-open-check-outline"
          title="Free edition" aria-label="Free edition">
          <span class="edition-label">Free edition</span>
        </v-chip>
        <v-btn icon="mdi-logout" title="Sign out" aria-label="Sign out" @click="logout" />
      </template>
    </v-app-bar>

    <v-main>
      <router-view v-if="subscriber" />
      <div v-else-if="!authState.ready" class="auth-page" role="status" aria-label="Loading session">
        <v-progress-circular indeterminate color="primary" />
      </div>
      <template v-else-if="authState.session?.authenticated">
        <v-alert v-if="logoutError" type="error" role="alert">{{ logoutError }}</v-alert>
        <router-view />
      </template>
      <SignInView v-else />
    </v-main>
  </v-app>
</template>

<style scoped>
@media (max-width: 400px) {
  .edition-label { display: none; }
  .edition-badge :deep(.v-chip__prepend) { margin-inline: 0; }
}
</style>
