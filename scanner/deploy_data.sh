#!/usr/bin/env bash
# Upload dist/ (the search data) to the Cloudflare Pages project py-code-data, served at
# data.py-code.org. Run after scanner/build.sh. Unchanged files are skipped by hash, so a
# monthly refresh uploads only what changed.
#
# Local settings go in .deploy.env (not committed), for example:
#   CLOUDFLARE_ACCOUNT_ID=<account id>
#   XDG_CONFIG_HOME=$HOME/.config/wrangler-py-code   # a wrangler login kept apart from others
set -euo pipefail
cd "$(dirname "$0")/.."
if [ -f .deploy.env ]; then set -a; . ./.deploy.env; set +a; fi
: "${CLOUDFLARE_ACCOUNT_ID:?set CLOUDFLARE_ACCOUNT_ID in .deploy.env}"
PROJECT=py-code-data
[ -s dist/api/status.json ] || { echo "dist/api is empty; run scanner/build.sh first" >&2; exit 1; }
npx --yes wrangler@4 whoami | grep -q "$CLOUDFLARE_ACCOUNT_ID" || { echo "wrangler is not logged into account $CLOUDFLARE_ACCOUNT_ID" >&2; exit 1; }
npx --yes wrangler@4 pages project list | grep -q "│ $PROJECT " ||
  npx --yes wrangler@4 pages project create "$PROJECT" --production-branch=main --force  # --force: classic Pages, not Workers
npx --yes wrangler@4 pages deploy dist --project-name="$PROJECT" --branch=main --commit-dirty=true
