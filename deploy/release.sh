#!/usr/bin/env bash
# Deploys one commit of origin/deploy: release.sh <sha>. The CI key's
# authorized_keys entry forces this script, so the commit arrives as
# SSH_ORIGINAL_COMMAND and the key can do nothing else.
set -euo pipefail

# The body is one function so bash has parsed all of it before git rewrites this file.
main() {
  local sha=${1:-${SSH_ORIGINAL_COMMAND:-}}
  if [[ ! $sha =~ ^[0-9a-f]{40}$ ]]; then
    echo "usage: release.sh <40-character commit sha>" >&2
    exit 2
  fi
  cd "$(dirname "$(readlink -f "$0")")/.."
  git fetch --quiet origin deploy
  if ! git merge-base --is-ancestor "$sha" FETCH_HEAD; then
    echo "$sha is not on origin/deploy" >&2
    exit 1
  fi
  git reset --hard "$sha"
  docker compose -f compose.yaml -f deploy/compose.prod.yaml up -d --build --wait --wait-timeout 900
  docker image prune -f
}

main "$@"
exit
