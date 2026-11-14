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
/** The shape of transformers.js's `pipeline` used here. */
export type PipelineFactory = (
  task: 'feature-extraction',
  model: string,
  options: { dtype: typeof EMBEDDING_DTYPE },
) => Promise<FeatureExtractor>;

export type Embedder = (texts: string[]) => Promise<number[][]>;

/**
 * An embedder that loads the model on first use and returns one mean-pooled,
 * L2-normalized 384-float vector per text.
 */
export function createEmbedder(pipeline: PipelineFactory): Embedder {
  let extractor: Promise<FeatureExtractor> | null = null;
  return async (texts) => {
    extractor ??= pipeline('feature-extraction', EMBEDDING_MODEL, { dtype: EMBEDDING_DTYPE });
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
