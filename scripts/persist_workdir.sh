#!/usr/bin/env bash
# Persist ./temp so the next Action run can resume downloads.
# Large files (>90MB) are NOT pushed to git (GitHub limit) — use Actions cache.
set -euo pipefail
cd "$(dirname "$0")/.."
mkdir -p temp

echo "Persisting temp/ …"
du -sh temp 2>/dev/null || true

STAGE=$(mktemp -d)
mkdir -p "$STAGE/temp"
copied=0
skipped=0
while IFS= read -r -d '' f; do
  rel="${f#temp/}"
  size=$(stat -c%s "$f" 2>/dev/null || stat -f%z "$f")
  if [[ "$size" -ge 90000000 ]]; then
    skipped=$((skipped + 1))
    continue
  fi
  mkdir -p "$STAGE/temp/$(dirname "$rel")"
  cp -a "$f" "$STAGE/temp/$rel"
  copied=$((copied + 1))
done < <(find temp -type f -print0 2>/dev/null || true)

echo "Git bot-data: $copied files (<90MB), skipped $skipped large (Actions cache)"

if [[ "$copied" -eq 0 ]]; then
  echo "Nothing small to push to bot-data"
  rm -rf "$STAGE"
  exit 0
fi

git config --global user.name "github-actions[bot]"
git config --global user.email "github-actions[bot]@users.noreply.github.com"

PUSH_DIR=$(mktemp -d)
cd "$PUSH_DIR"
git init -q
git checkout -b bot-data
cp -a "$STAGE/temp" .
echo "# Resumable bot workdir (files <90MB). Larger partials use Actions cache." > README.md
git add -A
if git diff --cached --quiet; then
  echo "No changes for bot-data"
else
  git commit -qm "chore: persist temp workdir $(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [[ -n "${GITHUB_REPOSITORY:-}" && -n "${GITHUB_TOKEN:-}" ]]; then
    git remote add origin "https://x-access-token:${GITHUB_TOKEN}@github.com/${GITHUB_REPOSITORY}.git"
    if ! git push -f origin bot-data 2>push.err; then
      echo "WARN: git push bot-data failed (often HTTP 403 if Actions token is read-only):"
      cat push.err || true
      echo "Fix: Repo Settings → Actions → General → Workflow permissions → Read and write permissions"
      echo "Or add secret REPO_PAT (PAT with 'repo' scope)."
      exit 0
    fi
    echo "Pushed origin/bot-data"
  else
    echo "Skip push (no GITHUB_TOKEN/REPOSITORY)"
  fi
fi

rm -rf "$STAGE" "$PUSH_DIR"
