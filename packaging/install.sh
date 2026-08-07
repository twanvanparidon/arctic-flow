#!/usr/bin/env bash
#
# install.sh: install a released atf binary.
#
#   curl -fsSL https://raw.githubusercontent.com/twanvanparidon/arctic-flow/main/packaging/install.sh | bash
#   packaging/install.sh --version v0.1.0 --prefix ~/.local
#
# Downloads the release archive and its .sha256, checks it, and installs the whole
# directory under <prefix>/lib with a link to the binary in <prefix>/bin. The binary
# carries its own interpreter beside it, so the directory is what moves and only the
# executable is linked.
#
# Reads nothing from stdin and prompts for nothing, so piping it into bash behaves the
# same as running it from a checkout. Every flag has an environment equivalent, because
# the piped form cannot take arguments.

set -euo pipefail

REPO="twanvanparidon/arctic-flow"
PLATFORM="linux-x86_64"
VERSION="${ATF_VERSION:-}"
PREFIX="${ATF_PREFIX:-$HOME/.local}"

usage() {
  cat <<'EOF'
install.sh: install a released atf binary.

  --version <tag>   the release to install, default the latest   ($ATF_VERSION)
                    the default skips prereleases; name one to get it (v0.2.0-rc.1)
  --prefix <dir>    install root, default ~/.local               ($ATF_PREFIX)

Installs <prefix>/lib/atf and links <prefix>/bin/atf. Uninstalling is removing those two.
EOF
}

while [[ $# -gt 0 ]]; do
  case $1 in
    --version) VERSION=${2:-}; shift 2 ;;
    --prefix)  PREFIX=${2:-}; shift 2 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "install.sh: unknown argument '$1'" >&2; usage >&2; exit 2 ;;
  esac
done

fail() { printf 'install.sh: %s\n' "$1" >&2; exit 1; }

for tool in curl tar sha256sum; do
  command -v "$tool" >/dev/null || fail "$tool is required and is not on PATH"
done

# PyInstaller cannot cross-compile, so a build covers one platform and there is nothing to
# fall back to. Refused here rather than at the download, which would 404 without saying why.
[[ $(uname -s) == Linux && $(uname -m) == x86_64 ]] \
  || fail "no binary for $(uname -s) $(uname -m): the release carries $PLATFORM only"

if [[ -z $VERSION ]]; then
  # The redirect off /releases/latest, not the JSON API: it needs no parser and no token,
  # and unauthenticated API calls are rate limited per IP address. GitHub leaves a release
  # marked prerelease out of this redirect, which is the only thing keeping a -rc tag off
  # the default install. Asking for one by name still works.
  latest=$(curl -fsSLI -o /dev/null -w '%{url_effective}' "https://github.com/$REPO/releases/latest") \
    || fail "could not reach github.com, or nothing is released yet: pass --version <tag>"
  VERSION=${latest##*/}
  [[ $VERSION == v* ]] || fail "could not read a version out of '$latest'"
fi

# The tag carries the v, the asset name does not.
TAG="v${VERSION#v}"
NAME="atf-${TAG#v}-$PLATFORM"
BASE="https://github.com/$REPO/releases/download/$TAG"

work=$(mktemp -d)
trap 'rm -rf "$work"' EXIT

echo "  fetching $NAME.tar.gz"
curl -fsSL -o "$work/$NAME.tar.gz" "$BASE/$NAME.tar.gz" \
  || fail "no such release asset: $BASE/$NAME.tar.gz"
curl -fsSL -o "$work/$NAME.tar.gz.sha256" "$BASE/$NAME.tar.gz.sha256" \
  || fail "no checksum published for $TAG, refusing to install an unverified archive"

# Before unpacking, not after: an archive that fails this is never written to disk.
(cd "$work" && sha256sum -c "$NAME.tar.gz.sha256" >/dev/null) \
  || fail "checksum mismatch on $NAME.tar.gz, refusing to install it"
echo "  checksum ok"

tar xzf "$work/$NAME.tar.gz" -C "$work"
[[ -x $work/atf/atf ]] || fail "$NAME.tar.gz does not contain atf/atf"

target="$PREFIX/lib/atf"
bin="$PREFIX/bin"

# Replaced whole, not merged. A file dropped between builds would otherwise survive beside
# the new bundle, and the interpreter would still load it.
if [[ -e $target ]]; then
  [[ -x $target/atf ]] || fail "$target exists and is not an atf install, move it aside first"
  rm -rf "$target"
fi

mkdir -p "$PREFIX/lib" "$bin"
mv "$work/atf" "$target"
ln -sfn "$target/atf" "$bin/atf"

echo "  installed $("$bin/atf" --version) to $target"

case ":$PATH:" in
  *":$bin:"*) ;;
  *) echo
     echo "  $bin is not on your PATH. Add it:"
     echo
     echo "    export PATH=\"$bin:\$PATH\"" ;;
esac
