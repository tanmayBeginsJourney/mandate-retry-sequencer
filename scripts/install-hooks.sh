#!/bin/sh
#
# Install the repo's git hooks into .git/hooks/.
#
# .git/hooks is not version-controlled, so every clone has to run this once.
# Re-running it is safe; it overwrites.

set -eu

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

SRC="scripts/pre-commit"
DEST=".git/hooks/pre-commit"

if [ ! -f "$SRC" ]; then
    echo "install-hooks: $SRC not found. Run this from inside the repo."
    exit 1
fi

mkdir -p .git/hooks
cp "$SRC" "$DEST"
chmod +x "$DEST"

echo "install-hooks: installed $SRC -> $DEST"
echo "install-hooks: the gate now runs on every 'git commit'."
echo "install-hooks: bypass is 'git commit --no-verify' -- log it in NOTES.md."
