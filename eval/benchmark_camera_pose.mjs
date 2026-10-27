// Synthetic CPU interpolation benchmark; no vault, renderer or recordings.
import { readFile, writeFile } from 'node:fs/promises';
import { createHash } from 'node:crypto';
import { execFileSync } from 'node:child_process';
import os from 'node:os';
import ts from 'typescript';

const sourcePath = process.argv[2];
const outputPath = process.argv[3];
if (!sourcePath || !outputPath) throw new Error('Usage: node eval/benchmark_camera_pose.mjs <source.ts[x]> <report.json>');
const source = await readFile(sourcePath, 'utf8');
const extracted = source.includes('/** The corner')
  ? 'const SETTLE_S = 0.5;\n' + source.slice(source.indexOf('export function poseAt(cams:'), source.indexOf('/** The corner'))
  : source;
const compiled = ts.transpileModule(extracted, { compilerOptions: { target: ts.ScriptTarget.ES2022, module: ts.ModuleKind.ES2022 } }).outputText;
const { poseAt } = await import(`data:text/javascript;base64,${Buffer.from(compiled).toString('base64')}`);
const cameras = { t: [], position: [], forward: [] };
for (let i = 0; i < 12000; i++) {
  cameras.t.push(i / 10);
  cameras.position.push([i / 100, Math.sin(i), Math.cos(i)]);
  cameras.forward.push([Math.cos(i / 10), .2, Math.sin(i / 10)]);
}
const output = { position: [0, 0, 0], forward: [0, 0, 0] };
function run() {
  let checksum = 0;
  for (let i = 0; i < 200000; i++) {
    const pose = poseAt(cameras, (i % 120000) / 100, output);
    checksum += pose.position[0] + pose.position[1] + pose.forward[2];
  }
  return checksum;
}
run(); run();
const wall = [], cpu = [], sums = [];
for (let i = 0; i < 9; i++) {
  const startCpu = process.cpuUsage(), start = performance.now();
  sums.push(run());
  wall.push(performance.now() - start);
  const used = process.cpuUsage(startCpu);
  cpu.push((used.user + used.system) / 1000);
}
const rows = [], identities = new Set(), arrays = new Set();
for (let i = 0; i < 1000; i++) {
  const pose = poseAt(cameras, i / 3, output);
  rows.push([[...pose.position], [...pose.forward]]);
  identities.add(pose); arrays.add(pose.position); arrays.add(pose.forward);
}
const sorted = [...wall].sort((a, b) => a - b);
const report = { revision: execFileSync('git', ['rev-parse', 'HEAD'], { encoding: 'utf8' }).trim(),
  node: process.version, os: os.release(), cpu: os.cpus()[0].model, samples: 9, warmups: 2,
  poses: 12000, evaluations_per_sample: 200000, wall_ms: wall, cpu_ms: cpu,
  median_ms: sorted[4], sampled_p95_ms: sorted[8], checksums: sums,
  output_sha256: createHash('sha256').update(JSON.stringify(rows)).digest('hex'),
  retained_probe_objects: identities.size, retained_probe_arrays: arrays.size,
  sourcePath, source_sha256: createHash('sha256').update(source).digest('hex'),
  benchmark_sha256: createHash('sha256').update(await readFile(new URL(import.meta.url))).digest('hex') };
await writeFile(outputPath, JSON.stringify(report, null, 2) + '\n');
console.log(JSON.stringify(report, null, 2));
