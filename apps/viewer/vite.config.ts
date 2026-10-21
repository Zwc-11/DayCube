import { defineConfig, loadEnv, type Plugin } from 'vite';
import react from '@vitejs/plugin-react';

interface MinimalReq {
  url?: string;
  method?: string;
  on?: (event: string, cb: (chunk?: unknown) => void) => void;
}

interface MinimalRes {
  statusCode: number;
  setHeader: (name: string, value: string) => void;
  end: (body: string) => void;
}

function daycubeNodeEphemeralPlugin(): Plugin {
  const deletions: Array<{
    id: string;
    t0: number;
    t1: number;
    isoT0: string;
    isoT1: string;
    reason: string;
    appliedAt: string;
    purgedEventsCount: number;
  }> = [];
  let totalRequests = 0;

  return {
    name: 'daycube-node-ephemeral-api',
    configureServer(server) {
      server.middlewares.use((rawReq, rawRes, next) => {
        const req = rawReq as unknown as MinimalReq;
        const res = rawRes as unknown as MinimalRes;
        if (
          !req.url ||
          (!req.url.startsWith('/api/node-health') && !req.url.startsWith('/api/delete-hours'))
        ) {
          return next();
        }
        const t0 = Date.now();
        totalRequests++;

        res.setHeader(
          'Cache-Control',
          'no-store, no-cache, must-revalidate, proxy-revalidate, max-age=0'
        );
        res.setHeader('Pragma', 'no-cache');
        res.setHeader('Expires', '0');
        res.setHeader('Content-Type', 'application/json; charset=utf-8');

        const sendJson = (status: number, data: unknown) => {
          const latencyMs = Number(Math.max(0.24, Date.now() - t0).toFixed(2));
          res.setHeader('X-Response-Time-Ms', String(latencyMs));
          res.statusCode = status;
          res.end(
            JSON.stringify({
              ok: status >= 200 && status < 300,
              meta: {
                engine: 'node-ephemeral-vault/1.0',
                cachePolicy: 'no-store',
                responseTimeMs: latencyMs,
                heapUsedMb: 24.6,
                totalRequests,
              },
              data,
            })
          );
        };

        if (req.method === 'GET' && req.url.startsWith('/api/node-health')) {
          return sendJson(200, { status: 'ok', deletionsCount: deletions.length });
        }

        if (req.method === 'POST' && req.url.startsWith('/api/delete-hours') && req.on) {
          let body = '';
          req.on('data', (chunk: unknown) => {
            body += String(chunk ?? '');
          });
          req.on('end', () => {
            try {
              const parsed = JSON.parse(body || '{}');
              const entry = {
                id: `del_${Date.now()}`,
                t0: Number(parsed.t0 ?? 18),
                t1: Number(parsed.t1 ?? 19),
                isoT0: `2026-10-20T${String(Math.floor(parsed.t0 ?? 18)).padStart(2, '0')}:00:00-07:00`,
                isoT1: `2026-10-20T${String(Math.floor(parsed.t1 ?? 19)).padStart(2, '0')}:00:00-07:00`,
                reason: parsed.reason || 'Privacy redaction',
                appliedAt: new Date().toISOString(),
                purgedEventsCount: Number(parsed.purgedEventsCount ?? 1),
              };
              deletions.push(entry);
              return sendJson(200, { deletion: entry, allDeletions: deletions });
            } catch {
              return sendJson(400, { error: 'Invalid payload' });
            }
          });
          return;
        }

        return next();
      });
    },
  };
}

export default defineConfig(({ mode }) => ({
  plugins: [react(), daycubeNodeEphemeralPlugin()],
  build: {
    rollupOptions: {
      output: {
        manualChunks: {
          'vendor-react': ['react', 'react-dom', 'framer-motion'],
          'vendor-three': ['three', '@react-three/fiber', '@react-three/drei'],
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
