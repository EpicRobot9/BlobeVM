import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// Public EpicVM beta front door. Mounted under /EpicVM/ (the BrowserRouter
// basename is baked at build time, so this app uses its own build).
export default defineConfig({
  plugins: [react()],
  root: '.',
  base: '/EpicVM/',
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
