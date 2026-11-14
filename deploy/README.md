# Deployment

This folder holds the production compose override and the Caddyfile. How releases work is in [docs/deployment.md](../docs/deployment.md); setting up the VPS is in [infra/ansible/README.md](../infra/ansible/README.md).

## Local check

The production override uses the images from GHCR and builds nothing, so a local run pulls a released commit's images. Log in once with a token that has `read:packages`:

```sh
echo "$GHCR_TOKEN" | docker login ghcr.io -u <github-user> --password-stdin
DOMAIN=localhost POSTGRES_PASSWORD=local IMAGE_TAG=sha-<commit> COMPOSE_PROJECT_NAME=junction26-prod \
  docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --wait
curl -k https://localhost/api/health
```

Caddy issues a certificate for `localhost` from its own local CA. `https://localhost/` serves the dashboard. `docker compose -p junction26-prod -f compose.yaml -f deploy/compose.prod.yaml down -v` removes the stack and its volumes.
