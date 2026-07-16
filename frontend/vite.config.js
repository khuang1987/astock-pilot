import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig({
  plugins: [react()],
  server: {
    allowedHosts: ["czops.site", "www.czops.site", "175.24.139.233"],
  },
  preview: {
    allowedHosts: ["czops.site", "www.czops.site", "175.24.139.233"],
  },
});
