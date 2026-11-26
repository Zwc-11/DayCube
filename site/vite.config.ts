import react from '@vitejs/plugin-react';
import { defineConfig } from 'vite';

// Relative base: the same build works at zwc-11.github.io/DayCube/, a custom domain or a preview host.
export default defineConfig({
  base: './',
  plugins: [react()],
  build: {
    target: 'es2022',
    assetsInlineLimit: 0,
    chunkSizeWarningLimit: 1600,
    rollupOptions: {
      output: { manualChunks: { three: ['three'], r3f: ['@react-three/fiber', '@react-three/drei', '@react-three/postprocessing', 'postprocessing'] } },
    },
  },
});
