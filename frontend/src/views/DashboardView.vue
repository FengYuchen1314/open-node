<script setup lang="ts">
import { licenseContract } from "../domain/license";

const statusTiles = [
  {
    label: "License",
    value: licenseContract.licenseRequired ? "Required" : "Not required",
    note: "No activation keys, paid entitlements, or upstream license server.",
    icon: "mdi-lock-open-check-outline",
    color: "success",
  },
  {
    label: "Backend",
    value: "FastAPI",
    note: "API-first control plane foundation under /api/v1.",
    icon: "mdi-api",
    color: "info",
  },
  {
    label: "Frontend",
    value: "Vue 3 + Vuetify",
    note: "Operational shell for server, agent, and probe workflows.",
    icon: "mdi-vuejs",
    color: "primary",
  },
] as const;

const migrationSlices = [
  "Control-plane inventory",
  "Agent registration",
  "Telemetry and probe APIs",
  "Xray runtime integration",
];
</script>

<template>
  <div class="page-shell">
    <section class="page-heading">
      <div>
        <div class="eyebrow">MMWX refactor</div>
        <h1 class="page-title">Open Node control plane</h1>
        <p class="page-copy">
          A clean FastAPI and Vue foundation for the free MMWX-compatible
          implementation.
        </p>
      </div>
      <v-btn color="primary" prepend-icon="mdi-source-branch" variant="flat">
        Main workspace
      </v-btn>
    </section>

    <section class="metric-grid" aria-label="Project status">
      <v-card
        v-for="tile in statusTiles"
        :key="tile.label"
        class="metric-card"
        variant="flat"
      >
        <v-icon :color="tile.color" :icon="tile.icon" size="28" />
        <div class="metric-label">{{ tile.label }}</div>
        <div class="metric-value">{{ tile.value }}</div>
        <div class="metric-note">{{ tile.note }}</div>
      </v-card>
    </section>

    <section class="work-grid">
      <v-sheet class="section-surface" border>
        <div class="section-title">Migration queue</div>
        <v-list lines="two">
          <v-list-item
            v-for="(slice, index) in migrationSlices"
            :key="slice"
            :subtitle="index === 0 ? 'Next implementation slice' : 'Queued'"
            :title="slice"
            prepend-icon="mdi-progress-check"
          />
        </v-list>
      </v-sheet>

      <v-sheet class="section-surface" border>
        <div class="section-title">Runtime contract</div>
        <v-list density="compact">
          <v-list-item prepend-icon="mdi-check-circle-outline" title="Free edition" />
          <v-list-item prepend-icon="mdi-check-circle-outline" title="No feature gates" />
          <v-list-item prepend-icon="mdi-check-circle-outline" title="No license server" />
        </v-list>
      </v-sheet>
    </section>
  </div>
</template>
