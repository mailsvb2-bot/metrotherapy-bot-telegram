#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="/root/metrotherapy"
BRANCH="feature/max-messenger-canonical"

cd "$PROJECT_DIR"

echo "=== Metrotherapy: push server changes to GitHub ==="

echo "=== Current branch ==="
CURRENT_BRANCH="$(git branch --show-current)"
echo "$CURRENT_BRANCH"

if [ "$CURRENT_BRANCH" != "$BRANCH" ]; then
  echo "ERROR: expected branch '$BRANCH', got '$CURRENT_BRANCH'"
  exit 1
fi

echo "=== Check git remote ==="
git remote -v

echo "=== Remove local trash ==="
find "$PROJECT_DIR" \
  -type d \( -name "__pycache__" -o -name ".pytest_cache" -o -name ".mypy_cache" -o -name ".ruff_cache" \) \
  -prune -exec rm -rf {} + 2>/dev/null || true

find "$PROJECT_DIR" \
  -type f \( -name "*.pyc" -o -name "*.pyo" -o -name "*.bak" -o -name "*.tmp" -o -name ".DS_Store" \) \
  -delete 2>/dev/null || true

echo "=== Git status before add ==="
git status --short

if [ -z "$(git status --porcelain)" ]; then
  echo "No changes to commit."
  exit 0
fi

echo "=== Pull latest remote state with rebase ==="
git fetch origin "$BRANCH"
git pull --rebase origin "$BRANCH"

echo "=== Add changes ==="
git add -A

echo "=== Git status after add ==="
git status --short

COMMIT_MESSAGE="${1:-server: sync production changes}"

echo "=== Commit ==="
git commit -m "$COMMIT_MESSAGE"

echo "=== Push to GitHub ==="
git push origin "$BRANCH"

echo "=== Done: server changes pushed to GitHub ==="
