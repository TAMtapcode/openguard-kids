import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// Khi phát triển: `npm run dev` và chuyển /api tới máy chủ OGK đang chạy (mặc định https://127.0.0.1:8443).
export default defineConfig({
  plugins: [react()],
  server: { proxy: { "/api": { target: process.env.OGK_API || "https://127.0.0.1:8443", secure: false, changeOrigin: true } } },
  build: { outDir: "dist", sourcemap: false },
});
