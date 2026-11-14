import { describe, expect, it } from 'vitest';
import { backendOrder, type DeviceCapabilities } from './backends';

const caps = (over: Partial<DeviceCapabilities>): DeviceCapabilities => ({
  gpu: null,
  crossOriginIsolated: false,
  hardwareConcurrency: 8,
  ...over,
});
const paths = (c: DeviceCapabilities) => backendOrder(c).map((b) => `${b.path}:${b.dtype}`);

describe('backendOrder', () => {
  it('tries WebGPU first, then threaded WASM when isolated, falling back to single-threaded WASM', () => {
    expect(paths(caps({ gpu: { shaderF16: true }, crossOriginIsolated: true }))).toEqual(['webgpu:fp16', 'wasm-threaded:q8']);
    expect(paths(caps({ gpu: { shaderF16: false } }))).toEqual(['webgpu:fp32', 'wasm-single:q8']);
    expect(paths(caps({ crossOriginIsolated: true, hardwareConcurrency: 1 }))).toEqual(['wasm-single:q8']);
  });
});
