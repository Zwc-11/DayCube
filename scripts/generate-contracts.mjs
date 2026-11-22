import { compile } from 'json-schema-to-typescript';
import Ajv2020 from 'ajv/dist/2020.js';
import standaloneCode from 'ajv/dist/standalone/index.js';
import addFormats from 'ajv-formats';
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
// The browser's validators, compiled here once instead of by ajv at every page load (about 150 ms
// before the first paint, and code generation at run time). Same ajv, same options: same checks.
const ajv = new Ajv2020({ allErrors: true, strict: false, code: { source: true, esm: true } });
addFormats(ajv);
for (const [key, schema] of Object.entries(schemas)) ajv.addSchema(schema, key);
// ajv's standalone code reaches its two helpers through CommonJS require(), which an ES module in
// the browser does not have: they become a named import and a local copy (ucs2length.mjs).
const helpers = {
  'require("ajv-formats/dist/formats").fullFormats': ["import { fullFormats } from 'ajv-formats/dist/formats.js';", 'fullFormats'],
  'require("ajv/dist/runtime/ucs2length").default': ["import { ucs2length } from './ucs2length.mjs';", 'ucs2length'],
};
let validators = standaloneCode(ajv, Object.fromEntries(Object.keys(schemas).map(key => [key, key])));
const imports = [];
for (const [call, [line, name]] of Object.entries(helpers)) {
  if (!validators.includes(call)) continue;
  validators = validators.replaceAll(call, name);
  imports.push(line);
}
if (/\brequire\(/.test(validators)) throw new Error('validators.mjs: an ajv helper is still required at run time');
validators = validators.replace(/^"use strict";/, '');
const output = {
  'types.ts': types,
  'contracts.json': `${JSON.stringify(schemas, null, 2)}\n`,
  'validators.mjs': `// Generated from *.schema.json by ajv (standalone). Run npm run schemas.\n${imports.join('\n')}\n${validators}\n`,
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
