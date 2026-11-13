import { resolve } from "node:path"
import tailwindcss from "@tailwindcss/vite"
import react from "@vitejs/plugin-react"
import { defineConfig } from "vite"
import { viteSingleFile } from "vite-plugin-singlefile"

// One HTML file with the scripts and styles inlined. ouncemcp fills in the report
// data and the Content-Security-Policy when it writes a report.
export default defineConfig({
  plugins: [react(), tailwindcss(), viteSingleFile()],
  resolve: { alias: { "@": resolve(import.meta.dirname, "./src") } },
  build: {
    outDir: resolve(import.meta.dirname, "../../src/ouncemcp/ui/static"),
    emptyOutDir: false,
    reportCompressedSize: false,
  },
})
