#!/bin/sh
#
# Tag the current HEAD as the start of a build day.
#
#   scripts/day-start.sh 1     ->  tags HEAD as day1-start
#
# The point is to be able to diff a whole day's work in one command:
#   git diff day1-start..HEAD
# and to be able to see, later, what the tree looked like before a day's
# changes went in.

set -eu

if [ $# -ne 1 ]; then
    echo "usage: scripts/day-start.sh N        (e.g. scripts/day-start.sh 1)"
    exit 1
fi

N="$1"

# Digits only, so a typo cannot create a junk tag.
case "$N" in
    ''|*[!0-9]*)
        echo "day-start: N must be a number, got '$N'"
        exit 1
        ;;
esac

ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"

TAG="day${N}-start"

if git rev-parse -q --verify "refs/tags/$TAG" >/dev/null; then
    echo "day-start: tag '$TAG' already exists, pointing at:"
    git log -1 --oneline "$TAG"
    echo "day-start: refusing to move it. Delete it first if you really mean to:"
    echo "    git tag -d $TAG"
    exit 1
fi

git tag -a "$TAG" -m "Start of day $N"

echo "day-start: tagged $(git rev-parse --short HEAD) as $TAG"
git log -1 --oneline "$TAG"
echo ""
echo "day-start: at the end of the day, see everything that changed with:"
echo "    git diff $TAG..HEAD"
