# Deployment

The production stack is `compose.yaml` with `deploy/compose.prod.yaml` on top. Caddy is the only public service: it gets a certificate for `DOMAIN` from Let's Encrypt and routes `/api/*` to the API, with the `/api` prefix removed, and everything else to the dashboard. The database and the fingerprint service have no host ports; the API and dashboard listen on localhost only.

## Requirements

- An Ubuntu VPS with Docker Engine and Docker Compose 2.24.4 or newer (`docker compose version`), since the override uses `!reset` and `!override`.
- A DNS A record (and AAAA record if the VPS has IPv6) pointing the domain at the VPS.
- Ports 80 and 443 open to the internet, TCP, and 443 UDP for HTTP/3.

## First setup

```sh
git clone https://github.com/nlinnanen/junction26.git
cd junction26
cp deploy/.env.example .env
openssl rand -hex 24
nano .env
```

In `.env`, set `DOMAIN` to the domain without a scheme, `POSTGRES_PASSWORD` to the output of `openssl rand -hex 24`, and `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` to the Hostinger AI Router key and URL. `ANALYSIS_MODEL` picks the model the router serves; empty means `claude-opus-5-5`. `COMPOSE_FILE` makes every `docker compose` command in this directory use the production override. `POSTGRES_PASSWORD` takes effect only when the database volume is created.

Start the stack and import the EUvsDisinfo seed cases:

```sh
docker compose up -d --build
docker compose exec api python -m app.seed.euvsdisinfo
```

The import enqueues a fetch job per case, which the worker works through over the next minutes (`docker compose logs -f worker`).

Check it from anywhere:

```sh
export $(grep ^DOMAIN= .env)
curl https://$DOMAIN/api/health
```

The dashboard is at `https://$DOMAIN/`. In the extension, set the backend URL to `https://$DOMAIN/api`.

## Update

```sh
cd junction26
git pull
docker compose up -d --build
```

Migrations run when the API and worker start. Rerunning the seed import adds only cases it has not imported.

## Operations

```sh
docker compose ps
docker compose logs -f api worker caddy
docker compose exec db psql -U postgres
```

The API and dashboard are reachable from a laptop through an SSH tunnel, for example `ssh -L 8501:localhost:8501 root@$DOMAIN`.
