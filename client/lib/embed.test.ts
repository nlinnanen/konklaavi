import { describe, expect, it, vi } from 'vitest';
import { createEmbedder, EMBEDDING_DIM, type PipelineFactory } from './embed';

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
