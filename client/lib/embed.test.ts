import { describe, expect, it, vi } from 'vitest';
import { cosine, createEmbedder, dequantize, EMBEDDING_DIM, embeddingUnits, type PipelineFactory, quantize } from './embed';
import { splitSentences } from './fingerprint';

/** A fake transformers.js pipeline whose vector for text t is filled with t's length. */
function fakePipeline(): PipelineFactory {
  return vi.fn(async () => async (texts: string[]) => ({
    dims: [texts.length, EMBEDDING_DIM],
    data: Float32Array.from(texts.flatMap((t) => Array<number>(EMBEDDING_DIM).fill(t.length))),
  }));
}

describe('createEmbedder', () => {
  it('loads the model on first use, once', async () => {
    const pipeline = fakePipeline();
    const embed = createEmbedder(pipeline);
    expect(pipeline).not.toHaveBeenCalled();
    await embed(['a']);
    await embed(['b']);
    expect(pipeline).toHaveBeenCalledTimes(1);
  });

  it('returns one vector per text, in order, across batches', async () => {
    const texts = Array.from({ length: 70 }, (_, i) => 'x'.repeat(i + 1));
    const vectors = await createEmbedder(fakePipeline())(texts);
    expect(vectors.map((v) => [v.length, v[0]])).toEqual(texts.map((t) => [EMBEDDING_DIM, t.length]));
  });

  it('retries loading the model after a failed load', async () => {
    const working = fakePipeline();
    const pipeline = vi.fn<PipelineFactory>().mockRejectedValueOnce(new Error('offline')).mockImplementation(working);
    const embed = createEmbedder(pipeline);
    await expect(embed(['a'])).rejects.toThrow('offline');
    expect(await embed(['a'])).toHaveLength(1);
  });
});

describe('quantize', () => {
  // backend/tests/pipeline/test_vectors.py expects the same encoding.
  it('encodes round(v * 127) as base64 int8, as the backend does', () => {
    expect(quantize([1, -1, 0.5, -0.25, 0, 0.01])).toBe('f4FA4AAB');
  });

  it('keeps cosine within 0.01 through dequantize', () => {
    const a = Array.from({ length: EMBEDDING_DIM }, (_, i) => Math.sin(i));
    const b = Array.from({ length: EMBEDDING_DIM }, (_, i) => Math.sin(i + 0.3));
    const unit = (v: number[]) => v.map((x) => x / Math.hypot(...v));
    const [ua, ub] = [unit(a), unit(b)];
    expect(cosine(dequantize(quantize(ua)), dequantize(quantize(ub)))).toBeCloseTo(cosine(ua, ub), 2);
  });
});

describe('embeddingUnits', () => {
  it('takes every sentence of 5+ words and every run of 3 of them', () => {
    const block = 'One two three four five. Too short here. Six seven eight nine ten. A b c d e. F g h i j.';
    expect(embeddingUnits(splitSentences(block)).map((u) => u.text)).toEqual([
      'One two three four five.',
      'Six seven eight nine ten.',
      'A b c d e.',
      'F g h i j.',
      'One two three four five. Six seven eight nine ten. A b c d e.',
      'Six seven eight nine ten. A b c d e. F g h i j.',
    ]);
  });

  it('has no windows when the block has fewer than 3 sentences', () => {
    expect(embeddingUnits(splitSentences('One two three four five. Six seven eight nine ten.'))).toHaveLength(2);
  });
});
