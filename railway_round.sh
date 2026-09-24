#!/usr/bin/env bash
# The hourly luggage round, as Railway runs it (board item E-93).
#
# Railway is the engine because GitHub's free scheduled Actions are dropped under load: on the
# night of 23/09/2026 one round out of ten actually started. A Railway cron service runs when it
# says it will, and its logs say why when it does not.
#
# The service is a cron service: Railway starts the container on the schedule, this script runs
# once and exits. It clones the repository fresh each time, so it always works on the newest data
# whatever else has written in the meantime, and pushes the readings back.
#
# Variables the service needs (set in Railway, never in the repository):
#   GITHUB_TOKEN   a GitHub token with write access to this repository
#   GITHUB_REPO    Giacbotta/nutrie-new-business-tracking   (default, can be left out)
#
# Schedule to set in Railway: 6 * * * *
set -u
export PYTHONIOENCODING=utf8
REPO="${GITHUB_REPO:-Giacbotta/nutrie-new-business-tracking}"
WORK="/tmp/tracking"

if [ -z "${GITHUB_TOKEN:-}" ]; then
  echo "GITHUB_TOKEN is not set: the round can read but could not push its readings back." >&2
  exit 1
fi

rm -rf "$WORK"
git clone --depth 1 "https://x-access-token:${GITHUB_TOKEN}@github.com/${REPO}.git" "$WORK" || exit 1
cd "$WORK" || exit 1
git config user.name "nutrie-railway"
git config user.email "actions@users.noreply.github.com"

# One provider failing must not cost the others their reading.
{ python radical_occupancy.py scan && python radical_occupancy.py daily; } || echo "RADICAL FAILED"
{ python bounce_occupancy.py scan  && python bounce_occupancy.py daily;  } || echo "BOUNCE FAILED"
{ python stow_occupancy.py scan    && python stow_occupancy.py daily;    } || echo "STOW FAILED"
python areas.py fill || echo "AREAS FAILED"
python dashboard.py  || echo "DASHBOARD FAILED"

git add data/ docs/
if git diff --cached --quiet; then
  echo "nothing new to save"
  exit 0
fi
git commit -q -m "data: railway round $(TZ=Europe/Rome date '+%Y-%m-%d %H:%M')"
for attempt in 1 2 3; do            # another writer may have pushed while the round was running
  git pull --rebase --autostash -q && git push -q && { echo "readings pushed"; exit 0; }
  sleep 10
done
echo "could not push after three attempts" >&2
exit 1
