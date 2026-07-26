#!/usr/bin/env bash
# Restore persisted downloads into ./temp before the bot starts.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p temp

echo "Restoring workdir into temp/ …"

if git fetch origin bot-data:refs/remotes/origin/bot-data 2>/dev/null; then
  rm -rf /tmp/bot-data-restore
  mkdir -p /tmp/bot-data-restore
  git archive origin/bot-data | tar -x -C /tmp/bot-data-restore
  if [[ -d /tmp/bot-data-restore/temp ]]; then
    cp -a /tmp/bot-data-restore/temp/. temp/
    echo "Merged temp/ from origin/bot-data"
  fi
  rm -rf /tmp/bot-data-restore
else
  echo "No bot-data branch yet (first run)"
fi

echo "temp/ size:"
du -sh temp 2>/dev/null || true
find temp -type f 2>/dev/null | wc -l | xargs -I{} echo "files: {}"
