/**
 * Sentence embeddings with transformers.js. The caller passes transformers.js's
 * `pipeline` function in, so this module runs wherever the caller has
 * `@huggingface/transformers` installed: the fingerprint service in Node and
 * the extension in the browser.
 */

export const EMBEDDING_MODEL = 'Xenova/paraphrase-multilingual-MiniLM-L12-v2';
/** Quantized weights, transformers.js's default in the browser, so Node and the browser compute the same vectors. */
export const EMBEDDING_DTYPE = 'q8';
export const EMBEDDING_DIM = 384;
const BATCH_SIZE = 32;

interface Tensor {
  dims: number[];
  data: ArrayLike<number | bigint>;
}
type FeatureExtractor = (texts: string[], options: { pooling: 'mean'; normalize: boolean }) => Promise<Tensor>;
/** How the model loads: transformers.js's `pipeline` options. */
export interface ModelOptions {
  dtype: typeof EMBEDDING_DTYPE | 'fp16' | 'fp32';
  device?: 'webgpu' | 'wasm';
}
/** The shape of transformers.js's `pipeline` used here. */
export type PipelineFactory = (
  task: 'feature-extraction',
  model: string,
  options: ModelOptions,
) => Promise<FeatureExtractor>;

export type Embedder = (texts: string[]) => Promise<number[][]>;

/** Loads the model with the first of `candidates` that loads, rethrowing the last failure when none does. */
async function loadFirst(pipeline: PipelineFactory, candidates: ModelOptions[]): Promise<FeatureExtractor> {
  let failure: unknown;
  for (const options of candidates) {
    try {
      return await pipeline('feature-extraction', EMBEDDING_MODEL, options);
    } catch (error) {
      failure = error;
      if (candidates.length > 1) console.warn('[konklaavi] loading the embedding model failed', options, error);
    }
  }
  throw failure;
}

/**
 * An embedder that loads the model on first use, with the first of
 * `candidates` that loads, and returns one mean-pooled, L2-normalized
 * 384-float vector per text.
 */
export function createEmbedder(
  pipeline: PipelineFactory,
  candidates: ModelOptions[] = [{ dtype: EMBEDDING_DTYPE }],
): Embedder {
  let extractor: Promise<FeatureExtractor> | null = null;
  return async (texts) => {
    extractor ??= loadFirst(pipeline, candidates);
    let extract: FeatureExtractor;
    try {
      extract = await extractor;
    } catch (error) {
      extractor = null; // a failed download is retried on the next call
      throw error;
    }
    const out: number[][] = [];
    for (let i = 0; i < texts.length; i += BATCH_SIZE) {
      const tensor = await extract(texts.slice(i, i + BATCH_SIZE), { pooling: 'mean', normalize: true });
      const data = Array.from(tensor.data, Number);
      for (let row = 0; row < (tensor.dims[0] ?? 0); row++) {
        out.push(data.slice(row * EMBEDDING_DIM, (row + 1) * EMBEDDING_DIM));
      }
    }
    return out;
  };
}

const INT8_SCALE = 127;

/**
 * A unit vector as base64 of one int8 per value, round(v * 127) with halves rounded up.
 * `backend/app/vectors.py` encodes the same bytes.
 */
export function quantize(vector: ArrayLike<number>): string {
  let binary = '';
  for (let i = 0; i < vector.length; i++) {
    const q = Math.max(-INT8_SCALE, Math.min(INT8_SCALE, Math.floor((vector[i] ?? 0) * INT8_SCALE + 0.5)));
    binary += String.fromCharCode(q & 0xff);
  }
  return btoa(binary);
}

/** The vector a `quantize` string encodes, at most 1/254 off per value. */
export function dequantize(encoded: string): Float32Array {
  const binary = atob(encoded);
  const out = new Float32Array(binary.length);
  for (let i = 0; i < binary.length; i++) {
    out[i] = ((binary.charCodeAt(i) << 24) >> 24) / INT8_SCALE;
  }
  return out;
}

/** Cosine similarity; 0 when either vector is zero. */
export function cosine(a: ArrayLike<number>, b: ArrayLike<number>): number {
  let dot = 0;
  let aa = 0;
  let bb = 0;
  for (let i = 0; i < a.length; i++) {
    const x = a[i] ?? 0;
    const y = b[i] ?? 0;
    dot += x * y;
    aa += x * x;
    bb += y * y;
  }
  return aa === 0 || bb === 0 ? 0 : dot / Math.sqrt(aa * bb);
}

/** Consecutive sentences per window unit. */
export const UNIT_WINDOW = 3;

/** An embedding unit: sentences `from` to `to` (exclusive) of a block, and the text that is embedded. */
export interface EmbeddingUnit {
  from: number;
  to: number;
  text: string;
}

/**
 * The units of a block whose sentences come from `splitSentences`: every
 * sentence, then every run of UNIT_WINDOW consecutive sentences. A unit's text
 * is its sentences' text joined by a space. The extension and POST /check
 * embed the same units.
 */
export function embeddingUnits(sentences: { text: string }[]): EmbeddingUnit[] {
  const unit = (from: number, to: number) => ({
    from,
    to,
    text: sentences.slice(from, to).map((s) => s.text).join(' '),
  });
  const units = sentences.map((_, i) => unit(i, i + 1));
  for (let i = 0; i + UNIT_WINDOW <= sentences.length; i++) units.push(unit(i, i + UNIT_WINDOW));
  return units;
}
