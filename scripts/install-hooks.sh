#!/bin/sh
#
# Install the repo's git hooks into .git/hooks/.
#
# .git/hooks is not version-controlled, so every clone has to run this once.
# Re-running it is safe; it overwrites.

set -eu

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

mkdir -p .git/hooks

for HOOK in pre-commit pre-push; do
    SRC="scripts/$HOOK"
    DEST=".git/hooks/$HOOK"
    if [ ! -f "$SRC" ]; then
        echo "install-hooks: $SRC not found. Run this from inside the repo."
        exit 1
    fi
    cp "$SRC" "$DEST"
    chmod +x "$DEST"
    echo "install-hooks: installed $SRC -> $DEST"
done

echo "install-hooks: 'git commit' now runs the FAST gate  (~35s, code gates)."
echo "install-hooks: 'git push'   now runs the FULL suite (~80s, adds S2/S3)."
echo "install-hooks: bypass is --no-verify -- log it in NOTES.md."
