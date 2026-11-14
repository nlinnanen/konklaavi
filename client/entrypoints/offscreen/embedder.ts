import { env, pipeline } from '@huggingface/transformers';
import { browser } from 'wxt/browser';
import { createEmbedder, type Embedder, type PipelineFactory } from '@/lib/embed';

/**
 * The embedder of `client/lib/embed.ts` running transformers.js on the device.
 * The model weights download from the Hugging Face hub on first use and stay in
 * the browser's cache.
 */
export function createDeviceEmbedder(): Embedder {
  env.allowLocalModels = false;
  const wasm = env.backends.onnx.wasm!;
  // The ONNX runtime build that wxt.config.ts copies into the extension, since MV3 runs no remote code.
  const ort = (file: string) => browser.runtime.getURL(`/ort/${file}` as '/');
  wasm.wasmPaths = {
    mjs: ort('ort-wasm-simd-threaded.asyncify.mjs'),
    wasm: ort('ort-wasm-simd-threaded.asyncify.wasm'),
  };
  wasm.numThreads = 1;
  return createEmbedder(pipeline as unknown as PipelineFactory);
}
