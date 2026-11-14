# Deployment

The production stack is `compose.yaml` with `deploy/compose.prod.yaml` on top, running from a checkout at `/opt/junction26` on one Hostinger VPS. Caddy is the only public service: it gets a certificate for `DOMAIN` from Let's Encrypt and routes `/api/*` to the API, with the `/api` prefix removed, and everything else to the dashboard. The database and the fingerprint service have no host ports; the API and dashboard listen on localhost only.

The `deploy` branch is what runs. A push to it runs `.github/workflows/deploy.yml`, which connects to the VPS with a key that can only run `deploy/release.sh <commit>`. The script checks that the commit is on `origin/deploy`, checks it out, rebuilds and restarts the stack, and waits until every service is healthy. The job then checks `https://DOMAIN/api/health` from GitHub.

## Requirements

- A Hostinger VPS installed with the "Ubuntu 24.04 with Docker" template, which brings Docker Engine and Docker Compose 2.24.4 or newer, since the override uses `!reset` and `!override`.
- Ports 22, 80 and 443 open to the internet over TCP, and 443 over UDP for HTTP/3, in the VPS firewall in hPanel if one is enabled.

The domain is the VPS hostname Hostinger assigns, of the form `srv123456.hstgr.cloud`, shown in hPanel under the VPS overview and by `hostname -f` on the VPS. It already points at the VPS.

## One-time setup

On your machine, set the hostname from hPanel once for the commands below:

```sh
VPS_HOST=srv123456.hstgr.cloud
```

### Repository access from the VPS

The repository is private, so the VPS reads it with its own read-only deploy key. On the VPS, as root:

```sh
ssh-keygen -t ed25519 -N "" -C junction26-vps -f /root/.ssh/id_ed25519
curl -s https://api.github.com/meta | python3 -c 'import json, sys; [print("github.com", k) for k in json.load(sys.stdin)["ssh_keys"]]' >> /root/.ssh/known_hosts
```

On your machine:

```sh
ssh root@$VPS_HOST cat /root/.ssh/id_ed25519.pub > junction26-vps.pub
gh repo deploy-key add junction26-vps.pub --repo nlinnanen/junction26 --title "VPS $VPS_HOST"
rm junction26-vps.pub
```

### Checkout and configuration

On the VPS:

```sh
git clone --branch integration git@github.com:nlinnanen/junction26.git /opt/junction26
cd /opt/junction26
cp deploy/.env.example .env
openssl rand -hex 24
nano .env
```

In `.env`, set `DOMAIN` to the VPS hostname without a scheme, `POSTGRES_PASSWORD` to the output of `openssl rand -hex 24`, and `ANTHROPIC_API_KEY` and `ANTHROPIC_BASE_URL` to the Hostinger AI Router key and URL. `ANALYSIS_MODEL` picks the model the router serves; empty means `claude-opus-5-5`. `COMPOSE_FILE` makes every `docker compose` command in this directory use the production override. `POSTGRES_PASSWORD` takes effect only when the database volume is created.

### Deploy key for GitHub Actions

On your machine, create the key, install it on the VPS restricted to the release script, and record the VPS host key:

```sh
ssh-keygen -t ed25519 -N "" -C junction26-ci -f junction26-ci
printf 'restrict,command="/opt/junction26/deploy/release.sh" %s\n' "$(cat junction26-ci.pub)" | ssh root@$VPS_HOST 'cat >> /root/.ssh/authorized_keys'
ssh-keyscan -t ed25519 $VPS_HOST > junction26-known-hosts
ssh-keygen -lf junction26-known-hosts
ssh root@$VPS_HOST ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

The two fingerprints must match. Then store the secrets and the public URL, and delete the local copies:

```sh
gh secret set VPS_HOST --repo nlinnanen/junction26 --body "$VPS_HOST"
gh secret set VPS_USER --repo nlinnanen/junction26 --body root
gh secret set VPS_SSH_KEY --repo nlinnanen/junction26 < junction26-ci
gh secret set VPS_KNOWN_HOSTS --repo nlinnanen/junction26 < junction26-known-hosts
gh variable set PUBLIC_URL --repo nlinnanen/junction26 --body "https://$VPS_HOST"
rm junction26-ci junction26-ci.pub junction26-known-hosts
```

### First start

Releasing (below) creates the `deploy` branch and starts the stack. The first start downloads the embedding model and initialises the database, so it takes several minutes. Once the workflow has passed, import the EUvsDisinfo seed cases on the VPS:

```sh
cd /opt/junction26
docker compose exec api python -m app.seed.euvsdisinfo
```

The import enqueues a fetch job per case, which the worker works through over the next minutes (`docker compose logs -f worker`). Rerunning it adds only cases it has not imported.

The dashboard is at `https://$VPS_HOST/`. In the extension, set the backend URL to `https://$VPS_HOST/api`.

## Release

Fast-forward `deploy` to a commit on `integration`, here its newest:

```sh
git fetch origin
git push origin origin/integration:refs/heads/deploy
```

`git push origin <sha>:deploy` releases any other commit that `deploy` fast-forwards to. The run shows under Actions as "deploy". A push while a deploy runs waits for it, and only the newest waiting push is deployed next. Migrations run when the API and worker start.

To go back to an earlier commit, force the branch to it:

```sh
git push --force origin <sha>:deploy
```

## Local check

The same stack runs on a laptop with `DOMAIN=localhost`, where Caddy issues a certificate from its own local CA instead of Let's Encrypt:

```sh
DOMAIN=localhost POSTGRES_PASSWORD=local COMPOSE_PROJECT_NAME=junction26-prod docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --build --wait
curl -k https://localhost/api/health
```

`https://localhost/` serves the dashboard. `docker compose -p junction26-prod -f compose.yaml -f deploy/compose.prod.yaml down -v` removes the stack and its volumes.

## Operations

On the VPS, in `/opt/junction26`:

```sh
docker compose ps
docker compose logs -f api worker caddy
docker compose exec db psql -U postgres
```

The API and dashboard are reachable from a laptop through an SSH tunnel, for example `ssh -L 8501:localhost:8501 root@$VPS_HOST`.
