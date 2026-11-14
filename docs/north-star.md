# North star

Network-level security such as WAFs and bot filtering stops bad requests before they reach a site. It cannot see bad content that arrives through valid requests, because that content is encrypted in transit and only exists in readable form inside the browser. We add the layer that sits there: a browser extension that recognises known propaganda narratives as the user reads, and a backend that tracks how those narratives spread and mutate across the web.

## How it works

The system works like antimalware, applied to narratives instead of programs.

- The backend keeps an index of known narrative signatures, each with a verdict, the manipulation techniques it uses and a plain-language explanation.
- The extension splits every page into chunks (posts, comments, paragraphs) and fingerprints each one locally.
- The extension downloads the signature index and matches against it locally. Page content never leaves the browser.
- On a match, the content is highlighted with the explanation and a link to the narrative's history: where it first appeared, how it was edited, and which accounts and domains spread it.
- Users report content they find manipulative. A report is an explicit act, and only then is the selected text sent to the backend.
- The backend analyses reports with an LLM, finds the narrative's other variants in the public pages it has fetched, and promotes it into the index once enough evidence agrees.

## What makes it different

Fingerprints work at three levels. An exact hash catches copies, shingles catch edited copies, and embeddings catch the same claim in different words. Together they let one report cover every copy and paraphrase of a narrative on every site.

The narrative graph is the product, and verdicts are labels on it. It records which account posted a narrative first, how each variant derives from the one before, and how narratives split and merge over time. That lets a reader see where a claim came from, not just that it was flagged.

Each verdict explains itself. The highlight names the techniques used, such as loaded language, fear appeals or fabricated authority, so readers learn why something is propaganda.

## Privacy

The client never uploads fingerprints of what it renders, because embeddings and shingle hashes can be inverted back into text. The server builds the graph from content it can fetch itself and from explicit reports. Detection telemetry only says that a known variant was seen at a public URL, which the server verifies by fetching that URL.

## Who it serves

- Readers get an in-place warning with an explanation and a source trail.
- Site owners such as news sites and blogs get a dashboard of which flagged narratives land in their comment sections, from which accounts, and when a brigade arrives.
- Researchers and journalists get a queryable graph of narrative spread.

## How we know it works

We report precision and false-positive rate, not just a demo. Ground truth comes from documented disinformation cases such as the EUvsDisinfo database and the Pravda network, and from reliable outlets, which serve as negatives. A simulation of honest, lazy, partisan and coordinated users measures how much a brigade can move a verdict, and how often reliable content gets flagged.
