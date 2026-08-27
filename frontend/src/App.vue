<template>
  <v-app>
    <v-navigation-drawer width="248" permanent>
      <div class="brand-block">
        <div class="brand-mark">ON</div>
        <div>
          <div class="brand-title">Open Node</div>
          <div class="brand-subtitle">Control plane</div>
        </div>
      </div>

      <v-list density="compact" nav>
        <v-list-item
          prepend-icon="mdi-view-dashboard-outline"
          title="Overview"
          value="overview"
          active
        />
        <v-list-item prepend-icon="mdi-server-network" title="Servers" value="servers" />
        <v-list-item prepend-icon="mdi-lan-connect" title="Agents" value="agents" />
        <v-list-item prepend-icon="mdi-chart-line" title="Probe" value="probe" />
        <v-list-item prepend-icon="mdi-shield-check-outline" title="Access" value="access" />
      </v-list>
    </v-navigation-drawer>

    <v-app-bar flat border>
      <v-app-bar-title>Open Node</v-app-bar-title>
      <template #append>
        <v-chip color="success" variant="tonal" prepend-icon="mdi-lock-open-check-outline">
          Free edition
        </v-chip>
      </template>
    </v-app-bar>

    <v-main>
      <router-view />
    </v-main>
  </v-app>
</template>
