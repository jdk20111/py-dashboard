#!/usr/bin/env bash
# Sync this deployment with the ha-dashboard repo and restart the dashboard
# only when something actually changed. Driven by ha-dashboard-update.timer;
# also safe to run manually with `sudo ./update.sh`.
#
# git runs as the repo owner (so pulled files keep correct ownership); the
# service restart needs root, so run the whole script as root (the timer does).
set -euo pipefail

REPO_DIR="${REPO_DIR:-/home/jdk201/ha-dashboard}"
RUN_AS="${RUN_AS:-jdk201}"

git_as() { runuser -u "$RUN_AS" -- git -C "$REPO_DIR" "$@"; }

BRANCH="$(git_as rev-parse --abbrev-ref HEAD)"
git_as fetch --quiet origin "$BRANCH"

local_rev="$(git_as rev-parse HEAD)"
remote_rev="$(git_as rev-parse "origin/$BRANCH")"

if [ "$local_rev" = "$remote_rev" ]; then
    echo "up to date ($local_rev)"
    exit 0
fi

echo "updating $local_rev -> $remote_rev"
git_as merge --ff-only "origin/$BRANCH"
systemctl restart ha-dashboard
echo "restarted ha-dashboard"
