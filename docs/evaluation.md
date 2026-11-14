# Evaluation

The narrative recognition benchmark measures one thing: whether HashTrust's fingerprints and embeddings recognise an article as carrying a narrative it tracks, and how often they match reliable reporting by mistake. It says nothing about whether a narrative is true or misleading, which is what context notes are for. EUvsDisinfo is used because each of its cases documents one claim and cites the articles across many sites that carried it, which makes it a ready-made set of pages known to share a narrative.

The benchmark runs offline against its own database, never the product's, and only when someone starts it by hand; [backend/app/eval/benchmark/README.md](../backend/app/eval/benchmark/README.md) says how. The numbers below come from a run on the full committed case file (400 cases) on 2026-11-14, and the raw output is [backend/app/eval/results/latest.json](../backend/app/eval/results/latest.json).

## Method

- Each EUvsDisinfo case documents a claim and cites the articles that published it.
- Cases are split deterministically. About 80% are seed cases, each imported as a narrative whose claim is the case's summary, as if a note had been written on it. The rest are held out whole as unseen narratives.
- Within each seed case, a third of the cited article URLs are held out and never fetched as seeds. These held-out pages are the positives for known narratives.
- Negatives are 313 article URLs from reliable outlets in several languages (Yle, Reuters, AP, BBC, ERR, DW and others). A match on one of them is a false positive of recognition.
- A page counts as recognised when any of its blocks matches a narrative. Metrics are per page, and the output calls a recognised page flagged.
- Exact and edited-copy recognition uses sentence hashes and MinHash LSH bands. Narrative-level recognition compares blocks of 8+ words to each narrative's vectors (up to 5 central blocks plus the claim text) by cosine similarity.

## Results

Exact and edited-copy recognition:

```
Known narratives: 73 pages of 106 held-out URLs and 0 mirrors from 82 seed cases (1 URLs also fetched as seeds left out); 17 flagged by their own case's narrative
precision 100.0%   recall 32.9%   false-positive rate 0.0%
Unseen narratives (held out whole, so low recall is expected): 108 pages of 122 URLs from 79 test cases (3 URLs also fetched as seeds left out)
precision 100.0%   recall 8.3%   false-positive rate 0.0%
Negatives: 312 pages of 313 reliable-outlet URLs
```

Narrative-level recognition, against 1473 vectors of 321 narratives:

```
 threshold     FPR   known P   known R  unseen P  unseen R
     0.700   29.2%     42.0%     90.4%     50.5%     86.1%
     0.725   22.1%     48.1%     87.7%     56.9%     84.3%
     0.750   17.6%     53.8%     87.7%     60.4%     77.8%
     0.775   11.5%     64.0%     87.7%     68.4%     72.2%
     0.800    6.4%     75.6%     84.9%     77.3%     63.0%
     0.825    2.2%     89.2%     79.5%     89.1%     52.8%
     0.850    0.0%    100.0%     72.6%    100.0%     36.1%
     0.875    0.0%    100.0%     64.4%    100.0%     22.2%
     0.900    0.0%    100.0%     56.2%    100.0%     16.7%
     0.925    0.0%    100.0%     52.1%    100.0%     12.0%
     0.950    0.0%    100.0%     43.8%    100.0%     10.2%
best known recall at a false-positive rate of at most 1.0%: 0.85
```

## Reading the numbers

- `NARRATIVE_MATCH_THRESHOLD` defaults to 0.825, which accepts a false-positive rate of a few percent for recall: 2.2% of reliable-outlet pages are recognised as carrying a narrative, and recall is 79.5% on known narratives and 52.8% on unseen ones.
- Narrative-level recognition raises recall on known narratives from 32.9% (copies and edited copies only) to 79.5% at 0.825, because most republications reword the claim rather than copy it.
- Unseen narratives are claims the benchmark never imported. 52.8% of their pages are still recognised at 0.825, because EUvsDisinfo often documents the same claim again as a new case.
- Below 0.825 the false-positive rate climbs quickly: 6.4% at 0.80 and 11.5% at 0.775.
- Remaining misses are mostly pages whose wording is too far from every vector of their narrative, and narratives whose seed pages failed to load.
- In the product, a recognised page carries whatever its narrative's notes say. A false positive here means a reader would see another narrative's notes on an unrelated article.
