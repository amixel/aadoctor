#!/usr/bin/env bash
#
# Build a release artifact and its checksum.
#
# Produces, from the current checkout:
#
#   dist/v<version>/aadoctor-<version>.tar.gz
#   dist/v<version>/aadoctor-<version>.tar.gz.sha256
#
# The directory layout mirrors what GitHub serves under
# /releases/download/, so `install.sh` can be pointed at dist/ with
# AADOCTOR_BASE_URL for testing without publishing anything.
#
# The tarball is built deterministically - sorted entries, fixed timestamps,
# numeric root ownership - so the same checkout always yields the same
# checksum. Requires GNU tar.
#
# Usage:
#   ./tools/package.sh
#   ./tools/package.sh --output /tmp/somewhere
#
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SCRIPT_DIR
REPO_DIR="$(cd "${SCRIPT_DIR}/.." && pwd)"
readonly REPO_DIR

# What a release contains. install.sh needs all of these.
readonly CONTENTS="src aadoctor VERSION LICENSE README.md install.sh uninstall.sh config.example.toml systemd"

OUTPUT_DIR="${REPO_DIR}/dist"

info() { printf '%s\n' "$*"; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

while [ "$#" -gt 0 ]; do
    case "$1" in
        --output) shift; [ "$#" -gt 0 ] || fail "--output needs a directory"; OUTPUT_DIR="$1" ;;
        -h|--help) sed -n '3,20p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) fail "unknown option: $1" ;;
    esac
    shift
done

command -v tar >/dev/null 2>&1 || fail "tar not found"
command -v sha256sum >/dev/null 2>&1 || fail "sha256sum not found"

[ -f "${REPO_DIR}/VERSION" ] || fail "missing VERSION"
VERSION="$(tr -d '[:space:]' < "${REPO_DIR}/VERSION")"
[ -n "${VERSION}" ] || fail "VERSION is empty"
readonly VERSION

for item in ${CONTENTS}; do
    [ -e "${REPO_DIR}/${item}" ] || fail "missing ${item}; cannot build a release"
done

readonly PREFIX="aadoctor-${VERSION}"
readonly ARTIFACT="${PREFIX}.tar.gz"
TARGET_DIR="${OUTPUT_DIR}/v${VERSION}"

mkdir -p "${TARGET_DIR}"

# Stage under the versioned prefix the archive should unpack into.
STAGE="$(mktemp -d)"
trap 'rm -rf -- "${STAGE}"' EXIT

mkdir -p "${STAGE}/${PREFIX}"
for item in ${CONTENTS}; do
    cp -a "${REPO_DIR}/${item}" "${STAGE}/${PREFIX}/${item}"
done

# Bytecode from a previous run must never reach a release artifact.
find "${STAGE}" -name '__pycache__' -type d -prune -exec rm -rf -- {} + 2>/dev/null || true

tar --sort=name \
    --mtime="@${SOURCE_DATE_EPOCH:-0}" \
    --owner=0 --group=0 --numeric-owner \
    -czf "${TARGET_DIR}/${ARTIFACT}" \
    -C "${STAGE}" "${PREFIX}"

( cd "${TARGET_DIR}" && sha256sum "${ARTIFACT}" > "${ARTIFACT}.sha256" )

info "built ${TARGET_DIR}/${ARTIFACT}"
info "      ${TARGET_DIR}/${ARTIFACT}.sha256"
info ""
cat "${TARGET_DIR}/${ARTIFACT}.sha256"
info ""
info "Publish both files on the GitHub release tagged v${VERSION}."
