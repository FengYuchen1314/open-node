import "@mdi/font/css/materialdesignicons.css";
import "vuetify/styles";
import "../src/styles.css";

import { createApp } from "vue";

import { vuetify } from "../src/plugins/vuetify";
import PublicProbeApp from "./PublicProbeApp.vue";

createApp(PublicProbeApp).use(vuetify).mount("#app");
