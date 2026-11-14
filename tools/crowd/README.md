# Crowd bots

LLM personas that read real news sites and write and rate Konklaavi notes through the public API, so that scoring, moderation and reputation run on realistic data. Each persona has a behaviour: `careful_fact_checker`, `casual_reader`, `camp_a` and `camp_b` are honest (the camps differ only in political framing), `troll` and `brigade` act in bad faith. `crowd.yaml` is a trial of 8 personas and `crowd-overnight.yaml` an overnight run of 30.

A persona with `logged_in: true` signs in as `bot-<name>@crowd.hashtrust.test`, with the password in `CROWD_BOT_PASSWORD` or one generated into the state file; one without writes as an anonymous install. The state file `.crowd-state-<api host>.json` beside the config holds those identities and every note and rating each persona made, which is the ground truth for checking whether reputation separates the good personas from the bad ones. It holds passwords and stays out of git; the run appends one line per action to `crowd-<api host>.log`.

## Sites

The bots read RSS feeds of rt.com (served from rt-english.com), sputnikglobe.com, Yle News and BBC News, as `konklaavi-crowd`, obeying robots.txt and waiting at least 3 seconds between requests to a host. Pages that fail or yield under 800 characters of text are skipped.

Left out: apnews.com answers the crawler's requests with 403, reuters.com's robots.txt forbids automated collection, and truth.social answers its Mastodon-compatible API, robots.txt included, with a 403 from Cloudflare, so its public posts can't be read without a browser session.

## Running

Every command runs in this directory with the router key from the worktree's `.env`. Sonnet costs roughly 0.5 to 1 credit per note decision and about 0.3 per batch of three ratings; brigade members rate without the model.

The 30-minute trial against production, stopping at 20 credits:

```sh
set -a; . ../../.env; set +a; uv run python -m crowd run --config crowd.yaml --api https://srv2160986.hstgr.cloud/api --minutes 30 --budget-credits 20
```

The overnight run, eight hours at about 50 credits an hour:

```sh
set -a; . ../../.env; set +a; uv run python -m crowd run --config crowd-overnight.yaml --api https://srv2160986.hstgr.cloud/api --minutes 480 --budget-credits 400
```

Ctrl-C stops after the current action. The run also stops when the credits the router reports reach the budget, so it can overshoot by one call.

Per persona, the notes written, their statuses and moderation, the ratings cast and the current `/me` reputation, with the mean reputation of signed-in good and bad personas:

```sh
uv run python -m crowd report --config crowd.yaml --api https://srv2160986.hstgr.cloud/api
```

Removal runs on the server, since anonymous installs can only be named by their ids. `remove` prints the command, with the anonymous installs from the state file, to run in `/srv/junction26`:

```sh
uv run python -m crowd remove --config crowd.yaml --api https://srv2160986.hstgr.cloud/api
```

## What to check in the admin panel

At https://srv2160986.hstgr.cloud/api/admin:

- Moderation: notes the moderation model held back, which should be few, since even the bad personas write civilly.
- Notes: the bots' notes moving from needs_more_ratings to helpful, not helpful or controversial, and whether troll and brigade notes end not helpful.
- Users: the `bot-*@crowd.hashtrust.test` accounts, where the good personas' reputation should rise above the troll's and the brigade's. `report` prints the same split from the state file.
- Narratives: each bot note on a new passage creates one; its page shows the passage, the copies the fetch found and the attached blocks.
