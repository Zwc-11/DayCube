import { spawn, spawnSync } from 'node:child_process';
import { rmSync } from 'node:fs';
import { fileURLToPath } from 'node:url';

const cwd = fileURLToPath(new URL('../', import.meta.url));
const env = { ...process.env, DAYCUBE_VAULT_ROOT: '.daycube-e2e-vault' };
// A fictional afternoon exported from a phone (scripts/build-phone-fixture.py), imported as 2026-10-21.
const phone = ['12 Example Ave-2026-10-21_18-00-00.zip', 'Example Ave 2.m4a', 'Example Ave 2.json', 'scan'].map(name => `fixtures/phone-day/${name}`);
rmSync(new URL('../.daycube-e2e-vault/2026-10-21', import.meta.url), { recursive: true, force: true }); // imported fresh each run
for (const args of [
  ['run', 'daycube', 'init'], ['run', 'daycube', 'seed', '--capture'], ['run', 'daycube', 'index', '--date', '2026-10-20'],
  ['run', 'daycube', 'import-phone', ...phone, '--no-transcribe'], ['run', 'daycube', 'index', '--date', '2026-10-21'],
]) {
  const result = spawnSync('uv', args, { cwd, env, stdio: 'inherit', windowsHide: true });
  if (result.status !== 0) process.exit(result.status ?? 1);
}
const child = spawn('uv', ['run', 'uvicorn', 'ingest.api:app', '--host', '127.0.0.1', '--port', '17701'], { cwd, env, stdio: 'inherit', windowsHide: true });
child.on('exit', code => process.exit(code ?? 0));
process.on('SIGTERM', () => child.kill('SIGTERM'));
process.on('SIGINT', () => child.kill('SIGINT'));
