# Deployment

The production stack runs on one Hostinger VPS. GitHub Actions tests every commit to `main`, builds the app images, pushes them to GHCR and releases them on the VPS. Nothing is built on the VPS. The VPS itself is hardened and configured by Ansible, described in [infra/ansible/README.md](../infra/ansible/README.md).

## Stack

The VPS runs `compose.yaml` with `deploy/compose.prod.yaml` on top, from `/srv/junction26`. The override swaps every `build:` for an image from GHCR:

| Service | Image |
|---|---|
| `api`, `worker` | `ghcr.io/nlinnanen/junction26-backend:sha-<commit>` |
| `fingerprint` | `ghcr.io/nlinnanen/junction26-fingerprint:sha-<commit>` |
| `dashboard` | `ghcr.io/nlinnanen/junction26-dashboard:sha-<commit>` |
| `proxy-view` | `ghcr.io/nlinnanen/junction26-proxy-view:sha-<commit>` |
| `db` | `timescale/timescaledb-ha:pg17` |
| `caddy` | `caddy:2.11` |

Caddy is the only public service. It gets a certificate for `DOMAIN` from Let's Encrypt and routes `/api/*` to the API, with the `/api` prefix removed, `/proxy/*` to the demo proxy view, and everything else to the dashboard. The proxy view renders pages of allowlisted sites (`ALLOWED_DOMAINS` in `demo/proxy-view/viewer.py`) in Chromium; it runs on a network it shares only with Caddy and calls the API at its public address. The API and dashboard listen on 127.0.0.1 only; the database and the fingerprint service have no host ports.

The dashboard image carries `backend/app/eval/results/latest.json` at `/eval/latest.json`, passed in as the `eval` build context. In development, compose mounts the live folder over it.

## Release

A push to `main` runs `.github/workflows/release.yml`:

1. **ci** calls `.github/workflows/ci.yml`, which tests every package.
2. **images** builds the backend, fingerprint and dashboard images and pushes each as `sha-<commit>`. The backend image gets `APP_VERSION=<commit>` baked in.
3. **deploy** connects to the VPS over SSH as the `deploy` user and sends the commit sha.
4. On the VPS, the key's `authorized_keys` entry forces `sudo -n /usr/local/sbin/junction26-release "$SSH_ORIGINAL_COMMAND"`. Sudoers lets `deploy` run only that script, and only with a 40-character lowercase hex argument.
5. The release script pulls the four app services' images for `sha-<commit>`, records `IMAGE_TAG=sha-<commit>` in `/srv/junction26/.env`, runs `docker compose up -d --remove-orphans --wait`, and prunes old images.
6. The job checks that `https://DOMAIN/api/health` reports `"version": "<commit>"`.

A push during a release waits for it, and only the newest waiting push is released next. Migrations run when the API and worker start. The script holds a lock, so two releases never run on the VPS at once.

`IMAGE_TAG` stays in `.env`, so restarts, reboots and Ansible runs keep the released version. The database and Caddy images are never pulled by a release; they change only when `compose.yaml` or the override changes.

## Rollback

Run the release workflow by hand with the sha of an earlier `main` commit:

```sh
gh workflow run release.yml --repo nlinnanen/junction26 -f sha=<40-character sha>
```

That skips ci and images and releases the images already in GHCR for that commit. A commit whose images were never built fails at the pull.

## Security model

- The deploy key can run the release script with a commit sha, and nothing else. It gets no shell, no pty and no forwarding. `deploy` is not in the docker group and has no password.
- The release script runs as root, is owned by root and cannot be changed by `deploy`. It accepts only a sha and only pulls images tagged with it.
- Root cannot log in over SSH, and no one can log in with a password. People log in as the admin user with a key.
- Whoever can push images to `ghcr.io/nlinnanen/junction26-*` decides what runs on the VPS. Only the release workflow pushes them, with the job's `GITHUB_TOKEN`. Protecting `main` protects production.
- The VPS pulls images with a classic token that has only `read:packages`.

The deploy job runs in the `production` environment. Adding required reviewers to it under the repository's Settings, Environments makes every release wait for an approval.

## Secrets and variables

On your machine, with the VPS hostname from hPanel:

```sh
VPS_HOST=srv123456.hstgr.cloud
ssh-keyscan -t ed25519 $VPS_HOST > junction26-known-hosts
ssh-keygen -lf junction26-known-hosts
ssh admin@$VPS_HOST ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub
```

The two fingerprints must match. Then:

```sh
gh secret set VPS_HOST --repo nlinnanen/junction26 --body "$VPS_HOST"
gh secret set VPS_SSH_KEY --repo nlinnanen/junction26 < junction26-deploy
gh secret set VPS_KNOWN_HOSTS --repo nlinnanen/junction26 < junction26-known-hosts
gh variable set PUBLIC_URL --repo nlinnanen/junction26 --body "https://$VPS_HOST"
rm junction26-deploy junction26-known-hosts
```

`junction26-deploy` is the private half of the deploy key; [infra/ansible/README.md](../infra/ansible/README.md) creates it and installs the public half. The workflow fails early and names any missing value.

## CI

`.github/workflows/ci.yml` runs on every pull request, on pushes to `integration`, and inside each release:

| Job | Checks |
|---|---|
| client | `npm run compile`, `npm test`, the Playwright e2e test, `npm run zip` |
| fingerprint-service | `npm run compile`, `npm test` |
| backend | `ruff check` and `pytest` against a real Timescale database and fingerprint service started with `docker compose up --wait db fingerprint` |
| dashboard | `ruff check` |

The client job uploads the packaged Chrome extension as the `extension-chrome` artifact, downloadable from the run's summary page.

## Operations

[infra/ansible/README.md](../infra/ansible/README.md) covers the first setup, logs and tunnels.
