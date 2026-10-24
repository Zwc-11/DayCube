import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';

const cwd = fileURLToPath(new URL('../', import.meta.url));
const env = { ...process.env, DAYCUBE_VAULT_ROOT: '.daycube-e2e-vault' };
for (const args of [['run', 'daycube', 'init'], ['run', 'daycube', 'seed', '--capture'], ['run', 'daycube', 'index', '--date', '2026-10-20']]) {
  const result = spawnSync('uv', args, { cwd, env, stdio: 'inherit', windowsHide: true });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
const child = spawn('uv', ['run', 'uvicorn', 'ingest.api:app', '--host', '127.0.0.1', '--port', '17701'], { cwd, env, stdio: 'inherit', windowsHide: true });
child.on('exit', code => process.exit(code ?? 0));
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
