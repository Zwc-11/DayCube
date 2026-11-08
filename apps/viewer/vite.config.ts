import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

function daycubeNodeEphemeralPlugin(): Plugin {
  return {
    name: 'daycube-node-ephemeral-api',
    configureServer(server) {
      server.middlewares.use((req, res, next) => {
        const request = req as unknown as { url?: string; method?: string };
        const pathname = request.url?.split('?')[0];
        if (request.method !== 'GET' || pathname !== '/api/node-health') return next();
        res.setHeader('Cache-Control', 'no-store');
        res.setHeader('Content-Type', 'application/json; charset=utf-8');
        res.statusCode = 200;
        res.end(JSON.stringify({ status: 'ok', mode: 'standalone-sim' }));
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), daycubeNodeEphemeralPlugin()],
  // Imported only when a scan's mesh or a world opens: bundled up front, or the dev server finds
  // them on that first open and reloads the page.
  optimizeDeps: {
    include: [
      'three/examples/jsm/loaders/GLTFLoader.js', 'three/examples/jsm/controls/OrbitControls.js', '@sparkjsdev/spark',
      'three/examples/jsm/loaders/DRACOLoader.js', 'three/examples/jsm/utils/BufferGeometryUtils.js',
      '3d-tiles-renderer/three', '3d-tiles-renderer/three/plugins', '3d-tiles-renderer/core/plugins',
    ],
  },
  build: {
    rollupOptions: {
      output: {
        // By path, not by entry name: with entry lists, React itself landed in the three.js chunk,
        // so the calendar downloaded the whole 3D stack before it could paint.
        manualChunks(id) {
          // Vite's dynamic-import helper is shared by the entry and drei; left alone it lands in
          // the three.js chunk and drags that chunk into the entry.
          if (id.includes('vite/preload-helper')) return 'vendor-react';
          // Only a world opens these: left to the world's own chunk, not every 3D page's.
          if (/three[\\/]examples[\\/]jsm[\\/](loaders[\\/]DRACOLoader|utils[\\/]BufferGeometryUtils)/.test(id)) return undefined;
          if (/[\\/]node_modules[\\/](three|@react-three)[\\/]/.test(id)) return 'vendor-three';
          if (/[\\/]node_modules[\\/](react|react-dom|scheduler|framer-motion)[\\/]/.test(id)) return 'vendor-react';
        },
      },
    },
  },
  server: {
    port: 3000,
    host: '127.0.0.1',
    strictPort: true,
    headers: {
      'Cache-Control': 'no-store, no-cache, must-revalidate, max-age=0',
      Pragma: 'no-cache',
      Expires: '0',
    },
    proxy: {
      '/api': {
        target: loadEnv(mode, '.', 'DAYCUBE_').DAYCUBE_API_TARGET || 'http://127.0.0.1:7701',
        rewrite: (path) => path.replace(/^\/api/, ''),
      },
    },
  },
}));
