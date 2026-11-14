/**
 * Which ONNX runtime path embeds on the device. The order is WebGPU, then
 * multithreaded WASM, then single-threaded WASM; the benchmark page measures
 * each path on real hardware to check that order.
 */

export type EmbeddingPath = 'webgpu' | 'wasm-threaded' | 'wasm-single';

export interface EmbeddingBackend {
  path: EmbeddingPath;
  device: 'webgpu' | 'wasm';
  dtype: 'fp16' | 'fp32' | 'q8';
}

/** What the document offers: a WebGPU adapter (and whether it has `shader-f16`) and cross-origin isolation. */
export interface DeviceCapabilities {
  gpu: { shaderF16: boolean } | null;
  crossOriginIsolated: boolean;
  hardwareConcurrency: number;
}

/** More WASM threads than this gain little for a model this small and take cores from the browser. */
export const MAX_WASM_THREADS = 4;

/** WASM threads for ONNX runtime: shared memory needs cross-origin isolation. */
export function wasmThreads(caps: DeviceCapabilities): number {
  return caps.crossOriginIsolated ? Math.max(1, Math.min(caps.hardwareConcurrency, MAX_WASM_THREADS)) : 1;
}

/** Every path the document can run, fastest expected first. WASM keeps the q8 weights the backend embeds with. */
export function embeddingBackends(caps: DeviceCapabilities): EmbeddingBackend[] {
  const out: EmbeddingBackend[] = [];
  if (caps.gpu) out.push({ path: 'webgpu', device: 'webgpu', dtype: caps.gpu.shaderF16 ? 'fp16' : 'fp32' });
  if (wasmThreads(caps) > 1) out.push({ path: 'wasm-threaded', device: 'wasm', dtype: 'q8' });
  out.push({ path: 'wasm-single', device: 'wasm', dtype: 'q8' });
  return out;
}

/**
 * The paths the embedder tries in order, falling back when a model fails to
 * load. ONNX runtime fixes its WASM thread count when it starts, so only one
 * WASM path can follow WebGPU.
 */
export function backendOrder(caps: DeviceCapabilities): EmbeddingBackend[] {
  const all = embeddingBackends(caps);
  const gpu = all.filter((b) => b.device === 'webgpu');
  const wasm = all.find((b) => b.device === 'wasm')!;
  return [...gpu, wasm];
}
