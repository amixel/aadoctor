#!/usr/bin/env bash
#
# AAD-007 verification: building a release, installing it from a server,
# rejecting a corrupted one, and updating.
#
# DESTRUCTIVE, container-only, same guard as lifecycle.sh.
#
# Usage, from the repository root:
#
#   docker run --rm -v "$PWD:/work:ro" python:3.8-slim bash /work/tests/integration/release.sh
#
# Needs curl; the script installs it if the image does not ship one.
#
set -u

if [ ! -f /.dockerenv ] && [ "${AADOCTOR_INTEGRATION_ALLOW:-0}" != "1" ]; then
    echo "refusing to run outside a container: this script removes aaDoctor's paths." >&2
    exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
readonly SOURCE_DIR

PASS=0
FAIL=0

check() {
    local label="$1"; shift
    if "$@" >/dev/null 2>&1; then
        echo "  PASS  ${label}"
        PASS=$((PASS + 1))
    else
        echo "  FAIL  ${label}"
        FAIL=$((FAIL + 1))
    fi
}

absent()  { [ ! -e "$1" ]; }
present() { [ -e "$1" ]; }

# --- prerequisites -------------------------------------------------------

if ! command -v curl >/dev/null 2>&1; then
    echo "installing curl..."
    apt-get update -qq >/dev/null 2>&1
    apt-get install -y -qq --no-install-recommends curl >/dev/null 2>&1
fi
command -v curl >/dev/null 2>&1 || { echo "curl unavailable; cannot run" >&2; exit 1; }

mkdir -p /stub
cat > /stub/systemctl <<'STUB'
#!/bin/sh
echo "systemctl $*" >> /tmp/systemctl.log
case "$1" in
    is-active)  if [ -f /tmp/unit-enabled ]; then echo active;  exit 0; fi; echo inactive; exit 3 ;;
    is-enabled) if [ -f /tmp/unit-enabled ]; then echo enabled; exit 0; fi; echo disabled; exit 1 ;;
    enable)  touch /tmp/unit-enabled; exit 0 ;;
    disable) rm -f /tmp/unit-enabled;  exit 0 ;;
    *) exit 0 ;;
esac
STUB
chmod +x /stub/systemctl
export PATH="/stub:${PATH}"
: > /tmp/systemctl.log

rm -rf /src && cp -a "${SOURCE_DIR}" /src
cd /src
VERSION="$(tr -d '[:space:]' < /src/VERSION)"

echo
echo "=== 1. build the release artifact ==="
bash ./tools/package.sh --output /serve
echo "exit: $?"
check "tarball built"  present "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz"
check "checksum built" present "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz.sha256"

echo "--- the artifact must not carry bytecode ---"
tar -tzf "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz" | grep -c '__pycache__' > /tmp/pyc.count
check "no __pycache__ inside the artifact" grep -qx '0' /tmp/pyc.count

echo "--- build twice: the checksum must be identical ---"
cp "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz.sha256" /tmp/checksum.first
bash ./tools/package.sh --output /serve > /dev/null 2>&1
check "build is reproducible" diff -q /tmp/checksum.first "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz.sha256"

echo
echo "=== 2. serve it locally ==="
( cd /serve && python3 -m http.server 8000 > /tmp/http.log 2>&1 ) &
SERVER_PID=$!
sleep 2
export AADOCTOR_BASE_URL="http://127.0.0.1:8000"
check "server is up" curl -fsS -o /dev/null "${AADOCTOR_BASE_URL}/v${VERSION}/aadoctor-${VERSION}.tar.gz"

echo
echo "=== 3. install from the release, from a directory with no source ==="
mkdir -p /elsewhere && cp /src/install.sh /elsewhere/
cd /elsewhere
bash ./install.sh --version "${VERSION}"
echo "exit: $?"

check "installed"                     present /opt/aadoctor
check "install.sh installed for update" present /opt/aadoctor/install.sh
check "uninstall.sh installed"        present /opt/aadoctor/uninstall.sh
check "configuration created"         present /etc/aadoctor/config.toml
check "download directory cleaned up" absent /opt/.aadoctor.download
# Checked before the CLI runs: Python writes its own bytecode on first import,
# which is fine. What must never happen is the installer copying bytecode in.
check "installer copied no bytecode"  absent /opt/aadoctor/src/aadoctor/__pycache__
check "CLI works"                     /usr/local/bin/aadoctor --version

echo
echo "=== 4. same version again is a no-op ==="
printf '[monitor]\nenabled = false\n' > /etc/aadoctor/config.toml
sha256sum /etc/aadoctor/config.toml > /tmp/config.before
bash ./install.sh --version "${VERSION}" > /tmp/again.log 2>&1
echo "exit: $?"
cat /tmp/again.log | tail -3
check "reports it is already installed" grep -q "already installed" /tmp/again.log
check "configuration untouched"         sha256sum -c --status /tmp/config.before

echo
echo "=== 5. --force reinstalls the same version ==="
bash ./install.sh --version "${VERSION}" --force > /tmp/forced.log 2>&1
echo "exit: $?"
check "forced reinstall succeeded"   grep -q "installed to /opt/aadoctor" /tmp/forced.log
check "configuration still preserved" sha256sum -c --status /tmp/config.before

echo
echo "=== 6. a corrupted artifact is rejected before anything is replaced ==="
cp "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz" /tmp/artifact.good
printf 'corrupted' >> "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz"
sha256sum /opt/aadoctor/VERSION > /tmp/installed.before

bash ./install.sh --version "${VERSION}" --force > /tmp/corrupt.log 2>&1
code=$?
echo "exit: ${code}"
grep -i "mismatch" /tmp/corrupt.log || true

check "install aborted"                    test "${code}" -ne 0
check "existing installation intact"       present /opt/aadoctor/aadoctor
check "installed VERSION untouched"        sha256sum -c --status /tmp/installed.before
check "corrupted download removed"         absent /opt/.aadoctor.download
check "nothing was extracted into place"   absent /opt/.aadoctor.stage
check "configuration still preserved"      sha256sum -c --status /tmp/config.before

cp /tmp/artifact.good "/serve/v${VERSION}/aadoctor-${VERSION}.tar.gz"

echo
echo "=== 7. a missing release is a clear error, not a crash ==="
bash ./install.sh --version 9.9.9 > /tmp/missing.log 2>&1
code=$?
echo "exit: ${code}"
tail -2 /tmp/missing.log
check "install aborted"              test "${code}" -ne 0
check "existing installation intact" present /opt/aadoctor/aadoctor
check "no leftover download"         absent /opt/.aadoctor.download

echo
echo "=== 8. aadoctor update delegates to the installer ==="
/usr/local/bin/aadoctor update --version "${VERSION}" > /tmp/update.log 2>&1
echo "exit: $?"
tail -3 /tmp/update.log
check "update reports it is current" grep -q "already installed" /tmp/update.log
/usr/local/bin/aadoctor update --version "${VERSION}" --force > /tmp/update2.log 2>&1
check "forced update reinstalls"     grep -q "installed to /opt/aadoctor" /tmp/update2.log
check "configuration survived the update" sha256sum -c --status /tmp/config.before

echo
echo "=== 9. no published release yet: the real repository ==="
unset AADOCTOR_BASE_URL
bash ./install.sh --release > /tmp/latest.log 2>&1
code=$?
echo "exit: ${code}"
tail -3 /tmp/latest.log
check "aborts instead of guessing"   test "${code}" -ne 0
check "existing installation intact" present /opt/aadoctor/aadoctor

echo
echo "=== 10. clean up ==="
kill "${SERVER_PID}" 2>/dev/null
bash /opt/aadoctor/uninstall.sh --purge --yes > /tmp/purge.log 2>&1
check "purged" absent /opt/aadoctor

echo
echo "=== systemctl calls ==="
sort -u /tmp/systemctl.log
foreign="$(grep -cv 'aadoctor\.service\|daemon-reload' /tmp/systemctl.log || true)"
check "no foreign unit touched" test "${foreign}" -eq 0

echo
echo "========================================"
echo "PASS: ${PASS}   FAIL: ${FAIL}"
echo "========================================"
[ "${FAIL}" -eq 0 ]
