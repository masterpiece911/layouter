#!/usr/bin/env node
/** One module import, two JSON-line requests. No desktop access in this process. */
import { createInterface } from 'node:readline';
import { createRequire } from 'node:module';
import { fileURLToPath, pathToFileURL } from 'node:url';
import { mkdtemp, writeFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { Console } from 'node:console';

const write = process.stdout.write.bind(process.stdout);
globalThis.console = new Console(process.stderr, process.stderr);
const require = createRequire(import.meta.url);
const reply = value => write(JSON.stringify({ protocol: 1, ...value }, (_key, item) => {
  if (['function', 'symbol', 'bigint'].includes(typeof item) ||
      (typeof item === 'number' && !Number.isFinite(item))) {
    throw new Error('Workflow metadata and props must be JSON values');
  }
  return item;
}) + '\n');
let build, React, evaluateWorkflow;
let startupError;
try {
  if (Number(process.versions.node.split('.')[0]) < 22) throw new Error('Node.js 22 or newer is required');
  ({ build } = await import('esbuild'));
  React = await import('react');
  ({ evaluateWorkflow } = await import('./dist/renderer.js'));
} catch (error) { startupError = error; }

let definition, selected;
for await (const line of createInterface({ input: process.stdin, crlfDelay: Infinity })) {
  let kind = 'protocol/JSON';
  try {
    if (startupError) { kind = 'runtime missing'; throw startupError; }
    const request = JSON.parse(line);
    if (request.protocol !== 1) throw new Error('Protocol version mismatch; update Python and the runtime together');
    if (request.operation === 'metadata') {
      if (definition) throw new Error('Metadata already loaded');
      kind = 'TS/TSX transformation';
      const result = await build({
        entryPoints: [request.workflow], bundle: true, write: false,
        platform: 'node', format: 'esm', target: 'node22', jsx: 'automatic',
        sourcemap: 'inline', logLevel: 'silent',
        plugins: [{
          name: 'layouter-runtime-singletons',
          setup(build) {
            build.onResolve({ filter: /^(@layouter\/react|react(?:\/.*)?)$/ }, args => ({
              path: args.path === '@layouter/react'
                ? fileURLToPath(new URL('./dist/index.js', import.meta.url))
                : require.resolve(args.path),
              external: true,
            }));
          },
        }],
      });
      kind = 'workflow module execution';
      const temporary = await mkdtemp(join(tmpdir(), 'layouter-react-'));
      try {
        const filename = join(temporary, 'workflow.mjs');
        await writeFile(filename, result.outputFiles[0].text);
        const module = await import(pathToFileURL(filename).href);
        definition = module.default;
      } finally { await rm(temporary, { recursive: true, force: true }); }
      if (!definition || typeof definition.component !== 'function') {
        throw new Error('Default export must be defineWorkflow({ args, component })');
      }
      selected = request.workflow;
      reply({ ok: true, result: { args: definition.args ?? {} } });
    } else if (request.operation === 'render') {
      if (!definition || request.workflow !== selected) throw new Error('Read metadata for this workflow first');
      kind = 'workflow render';
      const result = evaluateWorkflow(React.createElement(definition.component, { args: request.context.args }), request.context);
      reply({ ok: true, result });
      break;
    } else { throw new Error(`Unknown operation: ${request.operation}`); }
  } catch (error) {
    reply({ ok: false, kind, message: error instanceof Error ? error.message : String(error) });
    break;
  }
}
// A selected workflow may leave timers behind; this is a one-shot evaluator.
process.exitCode = 0;
