# Crowd bots

LLM personas that read real news sites and write and rate Konklaavi notes through the public API, so that scoring, moderation and reputation run on realistic data. Each persona has a behaviour: `careful_fact_checker`, `casual_reader`, `camp_a` and `camp_b` are honest (the camps differ only in political framing), `troll` and `brigade` act in bad faith. `crowd.yaml` is a trial of 8 personas on 4 sites and `crowd-overnight.yaml` an overnight run of 100 on 49.

A persona with `logged_in: true` signs in as `bot-<name>@crowd.hashtrust.test`, with the password in `CROWD_BOT_PASSWORD` or one generated into the state file; one without writes as an anonymous install. The state file `.crowd-state-<api host>.json` beside the config holds those identities and every note and rating each persona made, which is the ground truth for checking whether reputation separates the good personas from the bad ones. It holds passwords and stays out of git; the run appends one line per action to `crowd-<api host>.log`.

## Sites

`crowd-overnight.yaml` lists the sites by kind: international public and mainstream outlets, Finnish and Nordic ones, US partisan and hyperpartisan outlets of both sides, state media, alternative and health-misinformation sites, and a blog and an aggregator, in English, Finnish, Swedish, Norwegian, Danish and German. The bots read their RSS feeds as `konklaavi-crowd`, obeying robots.txt and waiting at least 3 seconds between requests to a host. Pages that fail or yield under 800 characters of text are skipped, which drops video pages and the short items of TASS, Yle and NRK.

Left out:

- Robots.txt forbids the crawler: reuters.com (all automated collection), globalresearch.ca, thegrayzone.com, scheerpost.com, articles.mercola.com and feeds.elpais.com (robots.txt itself answers 403 or a Cloudflare challenge), nakedcapitalism.com (feeds and query URLs), rferl.org (its feed API).
- Blocked: apnews.com, news.sky.com and realclearpolitics.com answer articles with 403; thefederalist.com, nationalreview.com, dailysignal.com and brownstone.org with a Cloudflare 403; childrenshealthdefense.org answers its feed with 403; presstv.ir serves a TLS certificate for another host; truth.social answers its Mastodon-compatible API, robots.txt included, with a 403 from Cloudflare.
- Unreadable: lemonde.fr serves an ad-blocker wall in place of the text, spiegel.de truncates its paid articles, and mtvuutiset.fi renders its articles in the browser.
- No recent articles: the RSS feeds of globaltimes.cn, english.news.cn (Xinhua) and Spiegel International are months or years old, and infowars.com and theepochtimes.com serve HTML at their feed addresses.

## Running

Every command runs in this directory with the router key from the worktree's `.env`. Sonnet costs about 0.57 credits per note decision (0.05 to 1.2) and 0.21 per batch of three ratings (mostly 0.13); brigade members rate without the model.

The 30-minute trial against production, stopping at 20 credits:

```sh
set -a; . ../../.env; set +a; uv run python -m crowd run --config crowd.yaml --api https://konklaavi.tech/api --minutes 30 --budget-credits 20
```

The overnight run, ten hours at about 280 credits an hour:

```sh
set -a; . ../../.env; set +a; uv run python -m crowd run --config crowd-overnight.yaml --api https://konklaavi.tech/api --minutes 600 --budget-credits 3000 --workers 32 --llm-concurrency 32
```

Due actions run on a pool of `--workers` threads (default 16). A persona has at most one action in flight, so its hourly caps hold, while different personas act at once. `--llm-concurrency` (default 16) caps the model calls in flight; a call the router turns away (rate limit, overload, server error) or drops is retried a few times with jittered backoff. The 3-second interval per news host holds across all workers.

Ctrl-C stops scheduling, lets the actions in flight finish and saves the state; a second Ctrl-C saves and quits at once. The run also stops scheduling when the credits the router reports reach the budget, so the actions in flight can overshoot it by up to one call each.

Per persona, the notes written, their statuses and moderation, the ratings cast and the current `/me` reputation, with the mean reputation of signed-in good and bad personas:

```sh
uv run python -m crowd report --config crowd.yaml --api https://konklaavi.tech/api
```

Removal runs on the server, since anonymous installs can only be named by their ids. `remove` prints the command, with the anonymous installs from the state file, to run in `/srv/junction26`:

```sh
uv run python -m crowd remove --config crowd.yaml --api https://konklaavi.tech/api
```

## What to check in the admin panel

At https://konklaavi.tech/admin:

- Moderation: notes the moderation model held back, which should be few, since even the bad personas write civilly.
- Notes: the bots' notes moving from needs_more_ratings to helpful, not helpful or controversial, and whether troll and brigade notes end not helpful.
- Users: the `bot-*@crowd.hashtrust.test` accounts, where the good personas' reputation should rise above the troll's and the brigade's. `report` prints the same split from the state file.
- Narratives: each bot note on a new passage creates one; its page shows the passage, the copies the fetch found and the attached blocks.
