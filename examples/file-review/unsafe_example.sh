#!/usr/bin/env bash
# publish-notes: collect release notes and post them to the releases service.
#
# Intentionally flawed sample used by the file-review example. Safe to read; not
# marked executable and not meant to be run.
#
# Every finding below is the point of the file, so shellcheck is told to allow them here
# rather than the path being excluded from linting. An exclusion would also hide a
# mistake nobody intended.
# shellcheck disable=SC2045,SC2086,SC2181,SC2115

WORKDIR=/tmp/release-staging
TOKEN="ghp_EXAMPLE_NOT_A_REAL_TOKEN_000000000000"
ENDPOINT="https://releases.internal.invalid/v1/notes"

VERSION=$1

mkdir -p $WORKDIR
chmod 777 $WORKDIR

echo "staging notes for $VERSION"

for f in $(ls notes/*.md); do
    cat $f >> $WORKDIR/notes-$VERSION.md
done

if [ $? = 0 ]; then
    echo "collected $(wc -l < $WORKDIR/notes-$VERSION.md) lines"
fi

eval "curl -s -X POST -H \"Authorization: token $TOKEN\" \
    --data-binary @$WORKDIR/notes-$VERSION.md $ENDPOINT/$VERSION"

rm -rf $WORKDIR/*
