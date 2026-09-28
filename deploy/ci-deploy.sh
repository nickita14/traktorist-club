#!/usr/bin/env bash
# The forced command of the CI deploy key (docs/deploy.md, section 12). sshd runs it instead of
# whatever the client asked for, and puts the request in SSH_ORIGINAL_COMMAND: it must be exactly
# a 40-character lowercase commit SHA, nothing else. Then, under the deploy lock that
# deploy/deploy.sh also takes:
#
#   refuse if the checkout has local edits, fetch, refuse a commit that is not on origin/main,
#   skip (exit 0) a commit older than the one deployed, check it out, run deploy/server-deploy.sh.
#
# So the key can deploy commits of main and nothing else: no shell, no other branch, no rollback
# (rollbacks are manual, with deploy/deploy.sh). The same commit again deploys again, so a failed
# deploy can be re-run. Exit codes: 2 for a malformed request, 1 for a refusal.
#
# APP_DIR and LOGGER exist for the tests (deploy/tests/test_ci_deploy.py); sshd passes no client
# environment to a forced command, so the key cannot set them.
set -euo pipefail
export LC_ALL=C

APP_DIR=${APP_DIR:-/srv/traktorist}
request=${SSH_ORIGINAL_COMMAND:-}
client=${SSH_CONNECTION:-local}
client=${client%% *}

log() { "${LOGGER:-logger}" -t traktorist-ci-deploy -- "$*" 2>/dev/null || true; }
refuse() {
    echo "refused: $1" >&2
    log "refused ($1) from $client"
    exit 1
}

# Checked before anything else runs. The request is never echoed back: it could carry escape
# sequences meant for the job log.
if [[ ! $request =~ ^[0-9a-f]{40}$ ]]; then
    echo "refused: expected a 40-character commit SHA" >&2
    log "refused a malformed request (${#request} characters) from $client"
    exit 2
fi
sha=$request
log "deploy of $sha requested from $client"

cd "$APP_DIR"
# Held until server-deploy.sh exits (exec keeps the descriptor).
exec 9>.git/traktorist-deploy.lock
flock -w 900 9 || refuse "another deploy has held the lock for 15 minutes"

if [[ -n $(git status --porcelain --untracked-files=no) ]]; then
    git status --short >&2
    refuse "the server checkout has local changes"
fi
git fetch --quiet --prune origin
git cat-file -e "$sha^{commit}" 2>/dev/null || refuse "$sha is not a commit in origin"
git merge-base --is-ancestor "$sha" origin/main || refuse "$sha is not on origin/main"

head=$(git rev-parse HEAD)
if [[ $sha != "$head" ]] && git merge-base --is-ancestor "$sha" "$head"; then
    echo "==> The server is already at a newer commit ($(git log -1 --format='%h %s')), skipping $sha"
    log "skipped $sha: already at $head"
    exit 0
fi

git checkout --quiet --detach "$sha"
[[ $(git rev-parse HEAD) == "$sha" ]] || refuse "checkout did not land on $sha"
echo "==> At $(git log -1 --format='%h %s')"
log "deploying $sha"
# The freshly checked-out version of the script does the rest.
exec deploy/server-deploy.sh
