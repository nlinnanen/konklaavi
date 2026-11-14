# Crowd bots

LLM personas that read real news sites and write and rate Konklaavi notes through the public API, so that scoring, moderation and reputation run on realistic data. Each persona has a behaviour: `careful_fact_checker`, `casual_reader`, `camp_a` and `camp_b` are honest (the camps differ only in political framing), `troll` and `brigade` act in bad faith. The runner, not the model, does the bad part, since the model declines to deceive in character: a bad persona's note labels an article of a site marked `accurate` misleading and any other not misleading, and the model only writes the one-sided opinion in it. A troll rates bad personas' notes helpful and other notes on accurate sites not helpful; a brigade member rates fellow members' notes helpful and all others not helpful. Neither calls the model to rate. `crowd.yaml` is a trial of 8 personas on 4 sites and `crowd-overnight.yaml` an overnight run of 100 on 49.

A persona with `logged_in: true` signs in as `bot-<name>@crowd.hashtrust.test`, with the password in `CROWD_BOT_PASSWORD` or one generated into the state file; one without writes as an anonymous install. The state file `.crowd-state-<api host>.json` beside the config, named by the canonical host (`konklaavi.tech` for its alias `srv2160986.hstgr.cloud`, whose files a run takes over), holds those identities and every note and rating each persona made, which is the ground truth for checking whether reputation separates the good personas from the bad ones. It holds passwords and stays out of git; the run appends one line per action to `crowd-<api host>.log`.

## Sites

`crowd-overnight.yaml` lists the sites by kind: international public and mainstream outlets, Finnish and Nordic ones, US partisan and hyperpartisan outlets of both sides, state media, alternative and health-misinformation sites, and a blog and an aggregator, in English, Finnish, Swedish, Norwegian, Danish and German. The bots read their RSS feeds as `konklaavi-crowd`, obeying robots.txt and waiting at least 3 seconds between requests to a host. Pages that fail or yield under 800 characters of text are skipped, which drops video pages and the short items of TASS, Yle and NRK.

Left out:

- Robots.txt forbids the crawler: reuters.com (all automated collection), globalresearch.ca, thegrayzone.com, scheerpost.com, articles.mercola.com and feeds.elpais.com (robots.txt itself answers 403 or a Cloudflare challenge), nakedcapitalism.com (feeds and query URLs), rferl.org (its feed API).
- Blocked: apnews.com, news.sky.com and realclearpolitics.com answer articles with 403; thefederalist.com, nationalreview.com, dailysignal.com and brownstone.org with a Cloudflare 403; childrenshealthdefense.org answers its feed with 403; presstv.ir serves a TLS certificate for another host; truth.social answers its Mastodon-compatible API, robots.txt included, with a 403 from Cloudflare.
- Unreadable: lemonde.fr serves an ad-blocker wall in place of the text, spiegel.de truncates its paid articles, and mtvuutiset.fi renders its articles in the browser.
- No recent articles: the RSS feeds of globaltimes.cn, english.news.cn (Xinhua) and Spiegel International are months or years old, and infowars.com and theepochtimes.com serve HTML at their feed addresses.

## Running

Every command runs in this directory with the router key in `HOSTINGER_ROUTER_KEY` or `ANTHROPIC_API_KEY`, here from the worktree's `.env`. Sonnet costs about 0.57 credits per note decision (0.05 to 1.2) and 0.21 per batch of three ratings (mostly 0.13); trolls and brigade members rate without the model.

The 30-minute trial against production, stopping at 20 credits:

```sh
set -a; . ../../.env; set +a; uv run python -m crowd run --config crowd.yaml --api https://konklaavi.tech/api --minutes 30 --budget-credits 20
```

The overnight run of six hours at about 500 credits an hour runs on the VPS, as "Run on the server" below describes.

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

## Run on the server

The overnight run runs on the VPS as `admin`, in a tmux session that outlives the SSH login. The repository is private, so the clone uses the GitHub key forwarded from your machine:

```sh
ssh -A admin@srv2160986.hstgr.cloud
git clone git@github.com:nlinnanen/konklaavi.git ~/konklaavi-crowd
curl -LsSf https://astral.sh/uv/install.sh | sh
```

On your machine, copy the trial's state file over so the bots keep their accounts; the first run renames it after `konklaavi.tech`:

```sh
scp -p ~/code/junction26/main/tools/crowd/.crowd-state-srv2160986.hstgr.cloud.json admin@srv2160986.hstgr.cloud:konklaavi-crowd/tools/crowd/
```

Back on the server, take the router key from the app's `.env` and start the run:

```sh
export HOSTINGER_ROUTER_KEY="$(sudo sed -n 's/^HOSTINGER_ROUTER_KEY=//p' /srv/junction26/.env)"
cd ~/konklaavi-crowd/tools/crowd
tmux new-session -d -s crowd "~/.local/bin/uv run python -m crowd run --config crowd-overnight.yaml --api https://konklaavi.tech/api --minutes 360 --budget-credits 3000 --workers 48 --llm-concurrency 32 2>&1 | tee -a run.out"
```

Follow the actions, attach to the session (detach with Ctrl-B D), or stop the run as Ctrl-C does:

```sh
tail -f ~/konklaavi-crowd/tools/crowd/crowd-konklaavi.tech.log
tmux attach -t crowd
tmux send-keys -t crowd C-c
```

`report` and `remove` run in the same directory, with the state file the run left there.

## What to check in the admin panel

At https://konklaavi.tech/admin:

- Moderation: notes the moderation model held back, which should be few, since even the bad personas write civilly.
- Notes: the bots' notes moving from needs_more_ratings to helpful, not helpful or controversial, and whether troll and brigade notes end not helpful.
- Users: the `bot-*@crowd.hashtrust.test` accounts, where the good personas' reputation should rise above the troll's and the brigade's. `report` prints the same split from the state file.
- Narratives: each bot note on a new passage creates one; its page shows the passage, the copies the fetch found and the attached blocks.
