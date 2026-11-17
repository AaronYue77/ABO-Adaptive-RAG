#!/bin/sh
set -eu

scripts=$(CDPATH='' cd -- "$(dirname "$0")" && pwd)
root=$(dirname "$scripts")

usable() {
    "$1" -I -c 'import ensurepip, sqlite3, struct, sys, venv; assert sys.version_info >= (3, 11) and sys.prefix == sys.base_prefix and struct.calcsize("P") == 8; sqlite3.connect(":memory:").execute("CREATE VIRTUAL TABLE probe USING fts5(text)")' 2>/dev/null
}

run() {
    python=$1
    shift
    exec "$python" "$scripts/start_web.py" "$@"
}

for config in "$root/.venv-web/pyvenv.cfg" "$root/.venv/pyvenv.cfg"; do
    if [ -f "$config" ]; then
        python_home=$(sed -n 's/^home *= *//p' "$config")
        if [ -n "$python_home" ] && usable "$python_home/python3"; then
            run "$python_home/python3" "$@"
        fi
    fi
done
for python in python3 python; do
    if usable "$python"; then run "$python" "$@"; fi
done

case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) platform=macos-arm64 ;;
    Darwin-x86_64) platform=macos-x64 ;;
    Linux-aarch64|Linux-arm64) platform=linux-arm64 ;;
    Linux-x86_64) platform=linux-x64 ;;
    *) printf '%s\n' 'ABO needs 64-bit macOS or Linux, or an existing Python 3.11+ with FTS5.' >&2; exit 1 ;;
esac
read -r key filename checksum <<EOF
$(awk -v target="$platform" '$1 == target { print; exit }' "$scripts/web_python_assets.txt")
EOF
target="$root/.cache/abo-web-python/${filename%.tar.gz}"
python="$target/python/bin/python3"
if [ -f "$target/.ready" ] && usable "$python"; then run "$python" "$@"; fi

digest() {
    if command -v sha256sum >/dev/null 2>&1; then sha256sum "$1"; else shasum -a 256 "$1"; fi
}

mkdir -p "$target"
archive="$target/$filename"
if [ ! -f "$archive" ] || [ "$(digest "$archive" | awk '{print $1}')" != "$checksum" ]; then
    url="https://github.com/astral-sh/python-build-standalone/releases/download/20261110/$filename"
    pending="$archive.$$.part"
    trap 'rm -f -- "$pending"' EXIT
    printf '%s\n' 'Downloading Python 3.11 for this project...'
    if command -v curl >/dev/null 2>&1; then curl -fL --retry 2 --connect-timeout 20 --max-time 150 -o "$pending" "$url"
    elif command -v wget >/dev/null 2>&1; then wget -O "$pending" "$url"
    else printf '%s\n' 'Install curl or wget to download the project Python.' >&2; exit 1
    fi
    if [ "$(digest "$pending" | awk '{print $1}')" != "$checksum" ]; then
        printf '%s\n' 'Downloaded Python checksum did not match.' >&2
        exit 1
    fi
    mv -- "$pending" "$archive"
fi
tar -xzf "$archive" -C "$target"
if ! usable "$python"; then
    printf '%s\n' 'The project Python could not start.' >&2
    exit 1
fi
: > "$target/.ready"
run "$python" "$@"
