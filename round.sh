#!/usr/bin/env bash
# The hourly luggage round itself (board item E-93), run from inside a fresh clone of this repository.
#
# This lives in the repository on purpose: railway_round.sh, the part baked into the Railway image,
# only clones the repo and calls this. So adding or changing a provider is a push to this file, never
# a Railway redeploy. Everything that needs the newest code and data is here.
#
# It expects to run in the repository root, with GITHUB_TOKEN in the environment and the remote
# already set to an authenticated URL (railway_round.sh clones with the token in the URL).
set -u
export PYTHONIOENCODING=utf8

git config user.name "nutrie-railway"
git config user.email "actions@users.noreply.github.com"

# One provider failing must not cost the others their reading.
{ python radical_occupancy.py scan && python radical_occupancy.py daily; } || echo "RADICAL FAILED"
{ python bounce_occupancy.py scan  && python bounce_occupancy.py daily;  } || echo "BOUNCE FAILED"
{ python stow_occupancy.py scan    && python stow_occupancy.py daily;    } || echo "STOW FAILED"
{ python litc_occupancy.py scan    && python litc_occupancy.py daily;    } || echo "LITC FAILED"
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
