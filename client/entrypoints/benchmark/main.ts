import { backendOrder, embeddingBackends, wasmThreads, type EmbeddingPath } from '@/lib/backends';
import { createDeviceEmbedder, deviceCapabilities } from '../offscreen/embedder';

/**
 * Times each embedding path the extension can run here. ONNX runtime fixes its
 * thread count once per document, so every path runs in its own frame of this
 * page, `benchmark.html?path=<path>`, which posts its result to the parent.
 */

const SUBJECTS = ['The city council', 'A new study', 'The health ministry', 'Local farmers', 'The central bank', 'Researchers in Helsinki', 'A viral post', 'The energy company'];
const CLAIMS = [
  'says the vaccine programme cut hospital admissions by half last winter.',
  'reports that electricity prices will fall once the new reactor comes online.',
  'claims the election results were changed after the polls had closed.',
  'warns that the river will flood again unless the dam is repaired this year.',
  'found no link between the mobile network upgrade and the reported headaches.',
  'announced that bread prices rose because of the drought in the south.',
  'denies that the water supply was contaminated during the summer festival.',
  'estimates that a third of the forest burned in fires that were set on purpose.',
];
/** 64 fixed sentences, the size of two embedding batches. */
const SAMPLES = SUBJECTS.flatMap((s) => CLAIMS.map((c) => `${s} ${c}`));

interface Result {
  path: EmbeddingPath;
  loadMs?: number;
  runMs?: number;
  error?: string;
}

async function runPath(path: EmbeddingPath): Promise<Result> {
  const embed = createDeviceEmbedder(path);
  try {
    let start = performance.now();
    await embed(SAMPLES);
    const loadMs = performance.now() - start;
    const runs: number[] = [];
    for (let i = 0; i < 3; i++) {
      start = performance.now();
      await embed(SAMPLES);
      runs.push(performance.now() - start);
    }
    return { path, loadMs, runMs: runs.sort((a, b) => a - b)[1] };
  } catch (e) {
    return { path, error: e instanceof Error ? e.message : String(e) };
  }
}

function inFrame(path: EmbeddingPath): Promise<Result> {
  return new Promise((resolve) => {
    const frame = document.createElement('iframe');
    const onMessage = (e: MessageEvent<Result>) => {
      if (e.source !== frame.contentWindow || e.origin !== location.origin) return;
      removeEventListener('message', onMessage);
      frame.remove();
      resolve(e.data);
    };
    addEventListener('message', onMessage);
    frame.src = `${location.pathname}?path=${path}`;
    document.body.append(frame);
  });
}

const ms = (n?: number) => (n === undefined ? '' : n.toFixed(0));

async function main() {
  const path = new URLSearchParams(location.search).get('path') as EmbeddingPath | null;
  if (path) {
    parent.postMessage(await runPath(path), location.origin);
    return;
  }
  const caps = await deviceCapabilities();
  const paths = embeddingBackends(caps);
  const active = backendOrder(caps)[0]!.path;
  const summary = document.querySelector('#summary')!;
  const rows = document.querySelector<HTMLTableSectionElement>('#results')!;
  document.querySelector('table')!.hidden = false;
  const describe = `Active path: ${active}. Cross-origin isolated: ${caps.crossOriginIsolated ? 'yes' : 'no'}, WASM threads: ${wasmThreads(caps)}, WebGPU: ${caps.gpu ? (caps.gpu.shaderF16 ? 'yes, with fp16' : 'yes, fp32 only') : 'no'}.`;
  for (const backend of paths) {
    summary.textContent = `${describe} Running ${backend.path}…`;
    const r = await inFrame(backend.path);
    const row = rows.insertRow();
    const cells = r.error
      ? [`${r.path}${r.path === active ? ' (active)' : ''}`, backend.dtype, `failed: ${r.error}`, '', '']
      : [`${r.path}${r.path === active ? ' (active)' : ''}`, backend.dtype, ms(r.loadMs), ms(r.runMs), (r.runMs! / SAMPLES.length).toFixed(1)];
    cells.forEach((text, i) => {
      const cell = row.insertCell();
      cell.textContent = text;
      if (i >= 2 && !r.error) cell.className = 'num';
    });
  }
  summary.textContent = describe;
}

void main();
