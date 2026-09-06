import path from "path"
import { defineConfig } from "vite"
import react from "@vitejs/plugin-react"
import tailwindcss from "@tailwindcss/vite"

export default defineConfig({
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(import.meta.dirname, "./src") } },
  // Built assets are served by FastAPI, so the whole app runs from one command.
  build: { outDir: "../frontend", emptyOutDir: true },
  server: { proxy: { "/api": "http://localhost:8000" } },
})
