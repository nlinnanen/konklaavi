# VPS setup with Ansible

Two playbooks set up the Hostinger VPS. `harden.yml` locks it down; `app.yml` installs what the release pipeline needs. `site.yml` runs both. How releases work is in [docs/deployment.md](../../docs/deployment.md).

## Requirements

- A Hostinger VPS installed with the "Ubuntu 24.04 with Docker" template. A plain Ubuntu 24.04 also works; the docker role then installs Ubuntu's `docker.io` and `docker-compose-v2`. Docker Compose must be 2.24.4 or newer.
- Ports 22, 80 and 443 open over TCP, and 443 over UDP, in the VPS firewall in hPanel if one is enabled. ufw on the VPS filters on top of it.
- On your machine, ansible-core and the collections:

```sh
pipx install ansible-core   # or: uv tool install ansible-core
cd infra/ansible
ansible-galaxy collection install -r requirements.yml
```

All commands below run in `infra/ansible`.

## Configuration

```sh
cp inventory.example.yml inventory.yml
cp group_vars/all/vault.example.yml group_vars/all/vault.yml
```

In `inventory.yml`, set `ansible_host` to the VPS hostname from hPanel, of the form `srv123456.hstgr.cloud`.

In `group_vars/all/main.yml`, set:

- `admin_github_users`: GitHub usernames whose public keys (`https://github.com/<user>.keys`) may log in as `admin_user`. They are fetched on every run, so a key added or removed on GitHub reaches the VPS on the next run, and removing a user removes their access. Anyone who can add a key to one of these accounts gets sudo on the VPS, so the accounts should have 2FA.
- `admin_ssh_keys`: extra public keys, for anyone not on GitHub. Root login is turned off, so these two lists are the only way in.
- `deploy_ssh_public_key`: the public half of the deploy key, created below.
- `ghcr_user`: the GitHub user that owns the GHCR token.

In `group_vars/all/vault.yml`, set the secrets and encrypt the file:

- `vault_postgres_password`: `openssl rand -hex 24`. It takes effect only when the database volume is created.
- `vault_ghcr_token`: a classic personal access token with only `read:packages`, created under GitHub Settings, Developer settings.
- `vault_anthropic_api_key`: the Hostinger AI Router key. `anthropic_base_url` and `analysis_model` go in `main.yml`; an empty model means `claude-opus-5-5`.

```sh
ansible-vault encrypt group_vars/all/vault.yml
```

`inventory.yml`, `vault.yml` and `.vault-pass` are gitignored.

### Deploy key

The key GitHub Actions deploys with:

```sh
ssh-keygen -t ed25519 -N "" -C junction26-deploy -f junction26-deploy
cat junction26-deploy.pub
```

Put the public key in `deploy_ssh_public_key`. The private key goes to GitHub as `VPS_SSH_KEY`, as [docs/deployment.md](../../docs/deployment.md) describes.

## First run

A fresh VPS only lets root in, so the first hardening run connects as root:

```sh
ansible-playbook harden.yml -e ansible_user=root --ask-vault-pass
```

From then on, Ansible connects as the admin user:

```sh
ansible-playbook site.yml --ask-vault-pass
```

Rerunning either playbook is safe. A run that changes the compose files or `.env` restarts the running release with them.

## Roles

`harden.yml`:

- **base**: upgrades packages, installs ufw, fail2ban, jq and curl, sets the timezone and turns on daily unattended security upgrades. `auto_reboot` reboots at `auto_reboot_time` when an upgrade needs it; it is off by default.
- **admin_user**: creates `admin_user` in the sudo group with no password and passwordless sudo. Its keys are the keys of `admin_github_users` plus `admin_ssh_keys`, installed exclusively, so any other key is removed. It fails if a listed GitHub user has no keys, or if there are no keys at all.
- **sysctl**: network and kernel hardening in `/etc/sysctl.d/60-hardening.conf`: no redirects or source routing, SYN cookies, logged martians, loose reverse-path filtering, restricted kernel pointers and dmesg. IP forwarding is left to Docker.
- **docker**: checks the Compose version and merges log rotation and `live-restore` into `/etc/docker/daemon.json`, keeping settings already there.
- **fail2ban**: bans an IP for an hour after five failed SSH logins in ten minutes.
- **firewall**: ufw denies incoming traffic except SSH (rate limited, from `ssh_allowed_sources`), 80/tcp, 443/tcp and 443/udp. The SSH rule goes in before ufw is enabled.
- **ssh**: runs last. It turns off root and password logins, allows only `admin_user` and `deploy_user`, and keeps local forwarding for tunnels. It checks the admin's keys exist and validates the config before restarting sshd. It also removes the old pipeline's root key, the one forced to run `deploy/release.sh`.

`app.yml`:

- **app**: copies `compose.yaml`, `deploy/compose.prod.yaml` and `deploy/Caddyfile` from this checkout to `/srv/junction26`, writes `.env`, logs Docker in to GHCR, installs `/usr/local/sbin/junction26-release`, and creates the `deploy` user with its restricted key and sudoers rule. `.env` keeps the `IMAGE_TAG` of the running release.

## Notes

- Docker publishes container ports past ufw. Only Caddy publishes ports to the internet; the API and dashboard bind to 127.0.0.1, and the database and fingerprint service publish none.
- Turning on `live-restore` takes a Docker restart, which stops running containers that one time. They come back with `restart: unless-stopped`. Later Docker restarts leave containers running.
- GitHub Actions connects from changing IPs, so `ssh_allowed_sources` is `any` by default. Restricting it also blocks releases.

## Moving from the old setup

The old setup ran a git checkout at `/opt/junction26` and released over SSH as root. On the VPS, as root, before the first Ansible run:

```sh
cd /opt/junction26
docker compose down
```

Without `-v`, the volumes stay. The new stack uses the same compose project name, `junction26`, so it finds the same database, models and certificates. Keep `/opt/junction26/.env` until its values are in `group_vars`, then remove the directory.

The ssh role removes the old root CI key. Afterwards, on your machine:

```sh
gh secret delete VPS_USER --repo nlinnanen/junction26
git push origin --delete deploy
```

## First release

After `site.yml`, push to `main` or run the release workflow by hand. The first start downloads the embedding model and initialises the database, so it takes several minutes. Then import the EUvsDisinfo seed cases:

```sh
ssh admin@$VPS_HOST
cd /srv/junction26
sudo docker compose exec api python -m app.seed.euvsdisinfo
```

The import enqueues a fetch job per case, which the worker works through over the next minutes. Rerunning it adds only cases it has not imported.

The dashboard is at `https://$VPS_HOST/`. In the extension, set the backend URL to `https://$VPS_HOST/api`.

## Operations

On the VPS, as the admin user, in `/srv/junction26`:

```sh
sudo docker compose ps
sudo docker compose logs -f api worker caddy
sudo docker compose exec db psql -U postgres
journalctl -t junction26-release
sudo ufw status verbose
sudo fail2ban-client status sshd
```

The API and dashboard are reachable from a laptop through an SSH tunnel:

```sh
ssh -L 8501:localhost:8501 -L 8000:localhost:8000 admin@$VPS_HOST
```
