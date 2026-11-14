# North star

Network security such as WAFs and bot filtering stops bad requests before they reach a site. It cannot see bad content that arrives through valid, encrypted requests, because that content only exists in readable form inside the browser. Konklaavi is the content layer that sits there: a browser extension that recognises passages belonging to a tracked narrative as the user reads, and shows what other readers have said about that narrative.

## Fingerprints as antivirus for narratives

Antivirus software recognises a known program by its signature wherever it turns up, without deciding anew each time what the program does. Konklaavi does the same for narratives. A sentence hash catches copies, MinHash bands catch edited copies, and multilingual embeddings catch the same claim in other words, so one narrative is recognised on every site and in every rewording. The backend fetches the public pages where the narrative is seen and records where it appeared first and how far it has spread.

Recognition is not a judgement. Konklaavi says that a passage belongs to a narrative seen on N sites since a given date, and nothing more on its own authority.

## Context notes

The judgement comes from readers. A reader selects a passage and adds a note that says whether the narrative is misleading or not misleading, explains the context and links sources. That act creates the narrative, and from then on the fingerprints carry the note to every copy of the passage.

Other readers rate notes as helpful, somewhat helpful or not helpful. The open-source Community Notes bridging algorithm decides which notes are shown: a note becomes helpful only when raters who usually disagree with each other both find it helpful, and a note that splits raters along that divide is shown as controversial. The highlight on a page follows the shown note, so readers see a misleading warning, added context, a dispute, or just the tracking line.

## Reputation

Signing in is optional. Reputation belongs to a verified account and follows its track record; a reader without an account writes and rates at the reputation of a new account, which does not grow. Writers gain it when their notes become helpful and lose it when they become not helpful. Raters gain it when their early ratings agree with the eventual outcome. Moderator rights come from high reputation or from being named by the operators.

## Moderation and its limits

Every submitted note goes through an LLM review for abuse, spam and off-topic content, and a flagged note waits for a human. Readers can report a note, which sends it back to the review queue. Volunteer moderators work through that queue in the dashboard and approve or hide notes.

These measures have limits, and we state them openly. The bridging algorithm resists one-sided brigades but needs enough raters with different views before any note is shown, so a new narrative often has none. Sign-up does not verify emails, so only rate limits slow mass sign-ups. The LLM review catches abuse and spam, not subtle falsehoods. Source links are optional, so readers judge a note partly on whether it cites any. Moderators are volunteers and can be wrong or slow. Konklaavi shows what a broad set of readers found helpful, which is a signal about a narrative, not a verdict on truth.

## Privacy

Page text leaves the browser only as the passage a user submits with a note. In remote mode the extension sends sentence hashes and LSH bands; in local index mode it downloads the index and matches on the device, embeddings included. Detection hits say only that a tracked narrative was seen at a public URL, which the backend checks by fetching that URL itself.

## How we know it works

An offline benchmark built from EUvsDisinfo cases measures narrative recognition: how often the fingerprints and embeddings recognise articles carrying the same claim on other sites, and how often they match reliable reporting by mistake. It measures recognition only. Whether a narrative is misleading is for the notes to say. The method and numbers are in [evaluation.md](evaluation.md).
