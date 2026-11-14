# Evaluation

How well HashTrust flags pages that carry known propaganda narratives, and how often it flags reliable reporting by mistake. The numbers come from `python -m app.eval` on the full committed EUvsDisinfo seed (400 cases), run on 2026-11-14. The raw output is [backend/app/eval/results/latest.json](../backend/app/eval/results/latest.json).

## Method

- Ground truth is the EUvsDisinfo database: each case documents a disinformation claim and cites the articles that published it.
- Cases are split deterministically. About 80% are seed cases, which become narratives. The rest are held out whole as unseen narratives.
- Within each seed case, a third of the cited article URLs are held out and never fetched as seeds. These held-out pages are the positives for known narratives.
- Negatives are 313 article URLs from reliable outlets in several languages (Yle, Reuters, AP, BBC, ERR, DW and others).
- A page counts as flagged when any of its blocks matches a narrative. Metrics are per page.
- Exact and edited-copy matching uses sentence hashes and MinHash LSH bands. Narrative-level matching compares blocks of 8+ words to each narrative's vectors (up to 5 central blocks plus the claim text) by cosine similarity.

## Results

Exact and edited-copy matching:

```
Known narratives: 73 pages of 106 held-out URLs and 0 mirrors from 82 seed cases (1 URLs also fetched as seeds left out); 17 flagged by their own case's narrative
precision 100.0%   recall 32.9%   false-positive rate 0.0%
Unseen narratives (held out whole, so low recall is expected): 108 pages of 122 URLs from 79 test cases (3 URLs also fetched as seeds left out)
precision 100.0%   recall 8.3%   false-positive rate 0.0%
Negatives: 312 pages of 313 reliable-outlet URLs
```

Narrative-level matching, 1473 vectors of 321 active narratives:

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

- The production threshold is 0.85: the highest recall on known narratives with a false-positive rate of at most 1%. At 0.85 no reliable-outlet page is flagged.
- Narrative-level matching raises recall on known narratives from 32.9% (copies and edited copies only) to 72.6%, because most republications reword the claim rather than copy it.
- Unseen narratives are claims HashTrust was never given. 36.1% of them are still caught at 0.85, because EUvsDisinfo often documents the same claim again as a new case.
- Below 0.825 the false-positive rate climbs quickly; the threshold trades recall for zero false positives on purpose.
- Remaining misses are mostly pages whose wording is too far from every vector of their narrative, and narratives whose seed pages failed to load.
