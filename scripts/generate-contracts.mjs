import { compile } from 'json-schema-to-typescript';
import { readFile, readdir, writeFile, mkdir } from 'node:fs/promises';
import { fileURLToPath } from 'node:url';
import path from 'node:path';

const root = fileURLToPath(new URL('../', import.meta.url));
const folder = path.join(root, 'packages/schemas');
const files = (await readdir(folder)).filter(file => file.endsWith('.schema.json')).sort();
const schemas = {};
let types = '// Generated from *.schema.json. Run npm run schemas.\n';
for (const file of files) {
  const key = file.replace('.schema.json', '');
  schemas[key] = JSON.parse(await readFile(path.join(folder, file), 'utf8'));
  types += await compile(schemas[key], schemas[key].title, {
    bannerComment: '', additionalProperties: false, declareExternallyReferenced: true,
  });
}
const output = {
  'types.ts': types,
  'contracts.json': `${JSON.stringify(schemas, null, 2)}\n`,
};
await mkdir(path.join(folder, 'src'), { recursive: true });
for (const [file, content] of Object.entries(output)) {
  const target = path.join(folder, 'src', file);
  if (process.argv.includes('--check')) {
    const existing = (await readFile(target, 'utf8').catch(() => '')).replace(/\r\n/g, '\n');
    if (existing !== content.replace(/\r\n/g, '\n')) throw new Error(`Stale contract: ${file}`);
  } else await writeFile(target, content);
}
console.log(`Contracts: ${files.length} canonical JSON schemas`);
