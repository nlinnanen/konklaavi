import { createRequire } from 'node:module';
import { dirname, join } from 'node:path';
import { defineConfig } from 'wxt';

/** Set by compose.yaml: no browser to launch, and the dev server must be reachable from the host. */
const inDocker = process.env.WXT_DOCKER === '1';

/** The ONNX runtime files transformers.js loads; `entrypoints/offscreen/embedder.ts` points it at them. */
const ORT_FILES = ['ort-wasm-simd-threaded.asyncify.mjs', 'ort-wasm-simd-threaded.asyncify.wasm'];
const ortDist = dirname(createRequire(import.meta.url).resolve('onnxruntime-web'));

export default defineConfig({
  // Visible folder so it shows up in the browser's "Load unpacked" file picker.
  outDir: 'dist',
  manifest: ({ browser }) => ({
    name: 'HashTrust',
    description:
      'HashTrust: flag misinformation and manipulative content, and see what others have flagged.',
    permissions: browser === 'firefox' ? ['storage'] : ['storage', 'offscreen'],
    // The embedding model downloads from the Hugging Face hub, which redirects to its CDN.
    host_permissions: ['https://huggingface.co/*', 'https://*.hf.co/*'],
    // The ONNX runtime compiles WebAssembly.
    content_security_policy: { extension_pages: "script-src 'self' 'wasm-unsafe-eval'; object-src 'self';" },
    // Firefox keys an add-on's storage on its id; the highlights need the CSS Custom Highlight API of Firefox 140.
    browser_specific_settings: { gecko: { id: 'hashtrust@junction26', strict_min_version: '140.0' } },
  }),
  hooks: {
    'build:publicAssets': (_wxt, files) => {
      for (const file of ORT_FILES) files.push({ absoluteSrc: join(ortDist, file), relativeDest: `ort/${file}` });
    },
  },
  webExt: { disabled: inDocker },
  dev: inDocker ? { server: { host: '0.0.0.0', port: 3000, origin: 'http://localhost:3000' } } : undefined,
});
