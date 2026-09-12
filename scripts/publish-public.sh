#!/usr/bin/env bash
# Publish the current tree (minus CLAUDE.md) to the public repo as one new commit on top of its history.
#   scripts/publish-public.sh "Describe the change"
set -euo pipefail
MSG=${1:?commit message}
PUB=${PUB:-git@github.com:arblazer2/stereo-tv.git}
SSHCMD=${GIT_SSH_COMMAND:-ssh}
SRC=$(cd "$(dirname "$0")/.." && pwd)
TMP=$(mktemp -d)
trap 'rm -rf "$TMP"' EXIT
GIT_SSH_COMMAND="$SSHCMD" git clone -q "$PUB" "$TMP/pub"
cd "$SRC"
git ls-files | grep -vE '^CLAUDE\.md$' > "$TMP/files"
rsync -a --delete --files-from="$TMP/files" ./ "$TMP/pub/"
if grep -rlE "192\.168\.|sansui|laymanstech|Batesville|Sansui|JLAM5" "$TMP/pub" --exclude-dir=.git --exclude=publish-public.sh; then
    echo "refusing: personal details found in the export (see above)"; exit 1
fi
cd "$TMP/pub"
git add -A
if git diff --cached --quiet; then echo "public repo already up to date"; exit 0; fi
git -c user.name="Justin" -c user.email="arblazer2@gmail.com" commit -q -m "$MSG

Co-Authored-By: Claude Fable 5.1 <noreply@anthropic.com>"
GIT_SSH_COMMAND="$SSHCMD" git push -q
echo "published: $(git log --oneline -1)"
