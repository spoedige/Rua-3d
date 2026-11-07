import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

// base relativa: funciona no Vercel, no GitHub Pages (/Rua-3d/) e em preview local
export default defineConfig({ plugins: [react()], base: "./" });
