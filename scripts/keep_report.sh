#!/usr/bin/env bash
# Keeps a text report on a branch of this repository of its own (the branch is replaced by one commit each time), so the report can
# be read without opening the workflow run. The repository is public, so secrets and the contact address are blanked out first.
#
# usage: scripts/keep_report.sh BRANCH FILE [MAX_LINES]      (run inside a checkout; MAX_LINES keeps the end of a long log, default 3000)
# KEEP_REPORT_REMOTE overrides where the branch is pushed (default: this repository on github.com); the tests use it.
set -euo pipefail

branch="${1:?usage: keep_report.sh BRANCH FILE [MAX_LINES]}"
file="${2:?usage: keep_report.sh BRANCH FILE [MAX_LINES]}"
max_lines="${3:-3000}"

if [ ! -s "$file" ]; then
  echo "$file is empty or missing: nothing to keep"
  exit 0
fi

remote="${KEEP_REPORT_REMOTE:-https://github.com/${GITHUB_REPOSITORY:?GITHUB_REPOSITORY is not set}.git}"
# the checkout's own credential, reused for the push (absent when running locally)
header="$(git config --local --get http.https://github.com/.extraheader || true)"
auth=()
if [ -n "$header" ]; then
  auth=(-c "http.https://github.com/.extraheader=${header}")
fi

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
name="$(basename "$file")"
tail -n "$max_lines" "$file" | python3 -c '
import os, sys
text = sys.stdin.read()
for name in ("COINGECKO_API_KEY", "ACUMEN_PASSPHRASE", "ACUMEN_CONTACT"):
    value = os.environ.get(name, "")
    if value:
        text = text.replace(value, "(" + name.lower() + ")")
sys.stdout.write(text)
' > "$work/$name"

cd "$work"
git init -q -b "$branch"
git add "$name"
git -c user.name="github-actions[bot]" -c user.email="41898282+github-actions[bot]@users.noreply.github.com" \
  commit -q -m "$name $(date -u +%Y-%m-%dT%H:%MZ)"
git ${auth[@]+"${auth[@]}"} push --force "$remote" "$branch:refs/heads/$branch"
echo "Kept $name on the $branch branch"
