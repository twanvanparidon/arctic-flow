#!/usr/bin/env bash
#
# release.sh: package the built binary for download.
#
#   packaging/release.sh                 # version taken from the binary
#   packaging/release.sh --tag v0.1.0    # and checked against it
#
# Produces, in dist/release/:
#
#   atf-<version>-linux-x86_64.tar.gz          the one-directory build
#   atf-<version>-linux-x86_64.tar.gz.sha256   verifiable with `sha256sum -c`
#   arctic_flow-<version>-*.whl, *.tar.gz     for anyone who has Python
#
# Uploading is not this script's job. It takes no credentials and touches no network, so
# it can be run and inspected locally. The workflow uploads what it leaves behind.

set -euo pipefail

REPO_ROOT=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd -P)
DIST="$REPO_ROOT/dist/atf"
OUT="$REPO_ROOT/dist/release"
PLATFORM="linux-x86_64"
# No default from the environment: CI's ref name is a branch on a branch build, which would
# fail the version check below for the wrong reason. The workflow passes --tag on a tag.
TAG=""

while [[ $# -gt 0 ]]; do
  case $1 in
    --tag)  TAG=${2:-}; shift 2 ;;
    --dist) DIST=${2:-}; shift 2 ;;
    --out)  OUT=${2:-}; shift 2 ;;
    *) echo "release.sh: unknown argument '$1'" >&2; exit 2 ;;
  esac
done

fail() { printf 'release.sh: %s\n' "$1" >&2; exit 1; }

[[ -x $DIST/atf ]] \
  || fail "no built binary at $DIST/atf, run the build first"

VERSION=$("$DIST/atf" --version | awk '{ print $2 }')
[[ -n $VERSION ]] || fail "could not read a version out of $DIST/atf --version"

# The tag is stamped into the binary during the build, so these agreeing is the proof that
# happened. When they differ the stamp did not run, and the release would be mislabelled:
# its name and its artefact claiming different versions, unnoticed until someone installs
# v0.2.0 and finds it reports 0.0.0.dev0.
if [[ -n $TAG ]]; then
  expected=${TAG#v}
  [[ $expected == "$VERSION" ]] || fail \
    "tag $TAG expects version $expected, but the binary reports $VERSION.
  The tag and src/cli/branding.py disagree; fix one of them and rebuild"
  echo "  tag $TAG matches the binary's version $VERSION"
fi

NAME="atf-$VERSION-$PLATFORM"
rm -rf "$OUT"
mkdir -p "$OUT"

# --sort/--mtime/--owner/--group make the archive reproducible: the same input produces
# the same bytes, so a checksum means something across rebuilds.
tar --create --gzip \
    --sort=name \
    --mtime="@${SOURCE_DATE_EPOCH:-0}" \
    --owner=0 --group=0 --numeric-owner \
    --file "$OUT/$NAME.tar.gz" \
    --directory "$(dirname -- "$DIST")" \
    "$(basename -- "$DIST")"
echo "  wrote $NAME.tar.gz ($(du -h "$OUT/$NAME.tar.gz" | cut -f1))"

# Bare filename in the checksum file, so `sha256sum -c` works from the same directory.
(cd "$OUT" && sha256sum "$NAME.tar.gz" > "$NAME.tar.gz.sha256")
echo "  wrote $NAME.tar.gz.sha256"

# A wheel as well, for anyone who already has Python and would rather `pip install` than
# download a 37MB bundle. Skipped rather than fatal if the build backend is unavailable,
# because the binary is the artefact that matters.
# `python3 -c "import build"` is not the check: an empty build/ directory in the
# working directory satisfies it as a namespace package, and then -m build fails. Ask
# whether it actually runs.
if python3 -m build --version >/dev/null 2>&1; then
  python3 -m build --outdir "$OUT" "$REPO_ROOT" >/dev/null
  # Globbed rather than filtered out of `ls`: the sdist is arctic_flow-<version>.tar.gz,
  # which is already distinct from the binary's atf-<version>-<platform>.tar.gz.
  built=()
  for artefact in "$OUT"/*.whl "$OUT"/arctic_flow-*.tar.gz; do
    [[ -e $artefact ]] && built+=("$(basename -- "$artefact")")
  done
  echo "  wrote ${built[*]}"
else
  echo "  skipped wheel and sdist: python3 -m build unavailable"
fi

echo
echo "release $VERSION, ready to upload from $(realpath --relative-to="$REPO_ROOT" "$OUT"):"
(cd "$OUT" && ls -1) | sed 's/^/  /'
