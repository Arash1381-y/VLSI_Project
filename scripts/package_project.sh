#!/usr/bin/env bash

set -euo pipefail

PROJECT_ROOT="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
PROJECT_NAME="$(basename -- "$PROJECT_ROOT")"
destination="$PROJECT_ROOT/../$PROJECT_NAME.zip"
force=false

usage() {
    cat <<EOF
Usage:
  ./scripts/package_project.sh [OUTPUT.zip] [--force]

Create a reproducible ZIP from the current Git commit. The archive contains a
$PROJECT_NAME/ top-level directory and excludes Git metadata, generated
outputs, caches, and existing distribution archives.

Arguments:
  OUTPUT.zip  destination (default: ../$PROJECT_NAME.zip)
  --force     replace an existing destination archive
  -h, --help  show this help
EOF
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        -h|--help)
            usage
            exit 0
            ;;
        --force)
            force=true
            shift
            ;;
        -* )
            echo "error: unsupported option: $1" >&2
            usage >&2
            exit 2
            ;;
        *)
            destination=$1
            shift
            ;;
    esac
done

if ! command -v git >/dev/null 2>&1; then
    echo "error: git is not available in PATH" >&2
    exit 127
fi
if ! command -v zipinfo >/dev/null 2>&1; then
    echo "error: zipinfo is not available in PATH" >&2
    exit 127
fi
if [[ -n "$(git -C "$PROJECT_ROOT" status --porcelain --untracked-files=all)" ]]; then
    echo "error: commit or discard working-tree changes before packaging" >&2
    exit 2
fi

if [[ "$destination" != /* ]]; then
    destination="$(pwd)/$destination"
fi
if [[ -e "$destination" && "$force" != true ]]; then
    echo "error: destination already exists: $destination" >&2
    echo "Use --force to replace it." >&2
    exit 2
fi

destination_parent="$(dirname -- "$destination")"
mkdir -p -- "$destination_parent"
package_temp_dir="$(mktemp -d "$destination_parent/.${PROJECT_NAME}-package.XXXXXX")"
cleanup() {
    find "$package_temp_dir" -depth -delete
}
trap cleanup EXIT

temporary_archive="$package_temp_dir/$PROJECT_NAME.zip"
git -C "$PROJECT_ROOT" archive \
    --format=zip \
    --prefix="$PROJECT_NAME/" \
    --output="$temporary_archive" \
    HEAD
mv -f -- "$temporary_archive" "$destination"

entry_count="$(zipinfo -1 "$destination" | wc -l)"
archive_size="$(stat -c '%s' "$destination")"
checksum="$(sha256sum "$destination" | cut -d ' ' -f 1)"

echo "Created $destination"
echo "Entries: $entry_count"
echo "Bytes: $archive_size"
echo "SHA-256: $checksum"
