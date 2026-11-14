// Bundles src/overlay.ts, with the extension's modules from client/, into overlay.bundle.js.
//   node demo/proxy-view/build.mjs        (esbuild from client/node_modules or the current directory)
import { createRequire } from 'node:module';
import { dirname, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const client = process.env.CLIENT_DIR ?? resolve(here, '../../client');
const require = createRequire(import.meta.url);
const esbuild = require(require.resolve('esbuild', { paths: [process.cwd(), client, here] }));

await esbuild.build({
  entryPoints: [resolve(here, 'src/overlay.ts')],
  outfile: resolve(here, 'overlay.bundle.js'),
  bundle: true,
  format: 'iife',
  target: 'es2022',
  minify: true,
  alias: { '@': client },
  // The extension's tsconfig extends WXT's generated one, which a plain checkout lacks.
  tsconfigRaw: {},
  define: { 'import.meta.env.DEV': 'false' },
  logLevel: 'warning',
});
