import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  root: '.',
  base: '/EpicVM/Dashboard/',
  build: {
    outDir: 'dist',
    emptyOutDir: true
  }
})
