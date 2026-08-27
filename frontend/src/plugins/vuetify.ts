import { createVuetify } from "vuetify";
import * as components from "vuetify/components";
import * as directives from "vuetify/directives";

export const vuetify = createVuetify({
  components,
  directives,
  theme: {
    defaultTheme: "openNodeLight",
    themes: {
      openNodeLight: {
        dark: false,
        colors: {
          background: "#f6f8f7",
          surface: "#ffffff",
          primary: "#176b5b",
          secondary: "#4f5d75",
          accent: "#c27a22",
          success: "#277a46",
          warning: "#a45d13",
          error: "#ba324f",
          info: "#3469a6",
        },
      },
    },
  },
});
