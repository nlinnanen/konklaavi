import { env, pipeline } from '@huggingface/transformers';
import { browser } from 'wxt/browser';
import {
  backendOrder,
  embeddingBackends,
  wasmThreads,
  type DeviceCapabilities,
  type EmbeddingBackend,
  type EmbeddingPath,
} from '@/lib/backends';
import { createEmbedder, type Embedder, type PipelineFactory } from '@/lib/embed';

interface GpuAdapter {
  features: { has(feature: string): boolean };
}
interface Gpu {
  requestAdapter(): Promise<GpuAdapter | null>;
}

/** This document's WebGPU adapter and isolation. Firefox stays on WASM. */
export async function deviceCapabilities(): Promise<DeviceCapabilities> {
  let gpu: DeviceCapabilities['gpu'] = null;
  const navGpu = (navigator as Navigator & { gpu?: Gpu }).gpu;
  if (!import.meta.env.FIREFOX && navGpu) {
    try {
      const adapter = await navGpu.requestAdapter();
      if (adapter) gpu = { shaderF16: adapter.features.has('shader-f16') };
    } catch (e) {
      console.warn('[konklaavi] no WebGPU adapter', e);
    }
  }
  return {
    gpu,
    crossOriginIsolated: globalThis.crossOriginIsolated === true,
    hardwareConcurrency: navigator.hardwareConcurrency || 1,
  };
}

/** The backends to try in this document: the default order, or only `path` for the benchmark. */
export async function deviceBackends(path?: EmbeddingPath): Promise<{ caps: DeviceCapabilities; backends: EmbeddingBackend[] }> {
  const caps = await deviceCapabilities();
  const backends = path ? embeddingBackends(caps).filter((b) => b.path === path) : backendOrder(caps);
  if (!backends.length) throw new Error(`${path} is not available here`);
  return { caps, backends };
}

/**
 * The embedder of `client/lib/embed.ts` running transformers.js on the device,
 * with the first of `deviceBackends(path)` that loads. The model weights
 * download from the Hugging Face hub on first use and stay in the browser's
 * cache.
 */
export function createDeviceEmbedder(path?: EmbeddingPath): Embedder {
  const ready = deviceBackends(path).then(({ caps, backends }) => {
    env.allowLocalModels = false;
    const wasm = env.backends.onnx.wasm!;
    // The ONNX runtime build that wxt.config.ts copies into the extension, since MV3 runs no remote code.
    // It runs WebGPU too, and its threads start as workers from the same file.
    const ort = (file: string) => browser.runtime.getURL(`/ort/${file}` as '/');
    wasm.wasmPaths = {
      mjs: ort('ort-wasm-simd-threaded.asyncify.mjs'),
      wasm: ort('ort-wasm-simd-threaded.asyncify.wasm'),
    };
    // Fixed when ONNX runtime starts, so it holds for every backend this document loads.
    wasm.numThreads = backends.some((b) => b.path === 'wasm-single') ? 1 : wasmThreads(caps);
    console.info('[konklaavi] embedding backends', backends.map((b) => b.path), 'wasm threads', wasm.numThreads);
    return createEmbedder(
      pipeline as unknown as PipelineFactory,
      backends.map(({ device, dtype }) => ({ device, dtype })),
    );
  });
  return async (texts) => (await ready)(texts);
}
