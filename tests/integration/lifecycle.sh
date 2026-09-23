#!/usr/bin/env bash
#
# SPEC-001 lifecycle verification.
#
# Installs, reinstalls, enables, disables, uninstalls and purges aaDoctor, then
# checks that a synthetic aaPanel tree came through byte-identical.
#
# DESTRUCTIVE. It removes /opt/aadoctor, /etc/aadoctor, /var/lib/aadoctor and
# /var/log/aadoctor. It refuses to run outside a container unless
# AADOCTOR_INTEGRATION_ALLOW=1 is set explicitly.
#
# Usage, from the repository root:
#
#   docker run --rm -v "$PWD:/work:ro" python:3.8-slim bash /work/tests/integration/lifecycle.sh
#
# systemd is not available in a container, so systemctl is replaced by a stub
# that records every call. What this proves is which units aaDoctor would act
# on, not that the unit starts - that needs a host with real systemd.
#
set -u

if [ ! -f /.dockerenv ] && [ "${AADOCTOR_INTEGRATION_ALLOW:-0}" != "1" ]; then
    echo "refusing to run outside a container: this script removes aaDoctor's paths." >&2
    echo "Set AADOCTOR_INTEGRATION_ALLOW=1 only on a disposable machine." >&2
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

# --- systemctl stub, recording every call --------------------------------

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
rm -f /tmp/unit-enabled

# --- synthetic aaPanel tree ----------------------------------------------
# Nothing aaDoctor does may alter a single byte or permission bit here.

mkdir -p /www/server/panel/vhost/nginx /www/wwwlogs /www/wwwroot/example.com
echo 'server { server_name example.com; }' > /www/server/panel/vhost/nginx/example.com.conf
echo '127.0.0.1 - - [22/Sep/2026:12:00:00 +0000] "GET / HTTP/1.1" 200 12' > /www/wwwlogs/example.com.log
echo '<?php echo 1;' > /www/wwwroot/example.com/index.php

find /www -type f -exec sha256sum {} \; | sort > /tmp/www.before
find /www -printf '%m %u %g %p\n' | sort > /tmp/www.perms.before

# Work from a copy so a read-only mount of the repository still works.
rm -rf /src
cp -a "${SOURCE_DIR}" /src
cd /src

# A developer checkout usually carries bytecode from a local run. It must not
# be carried into the installation.
mkdir -p /src/src/aadoctor/__pycache__
echo 'stale' > /src/src/aadoctor/__pycache__/paths.cpython-38.pyc

echo
echo "=== 1. first install ==="
bash ./install.sh
echo "exit: $?"

check "/opt/aadoctor exists"        present /opt/aadoctor
check "src/ installed"              present /opt/aadoctor/src
check "VERSION installed"           present /opt/aadoctor/VERSION
check "uninstall.sh installed"      present /opt/aadoctor/uninstall.sh
check "configuration created"       present /etc/aadoctor/config.toml
check "incidents dir created"       present /var/lib/aadoctor/incidents
check "offsets dir created"         present /var/lib/aadoctor/offsets
check "log dir created"             present /var/log/aadoctor
check "CLI entry point created"     present /usr/local/bin/aadoctor
check "unit installed"              present /etc/systemd/system/aadoctor.service
check "staging dir cleaned up"      absent  /opt/.aadoctor.stage
check "previous dir cleaned up"     absent  /opt/.aadoctor.previous
check "install did not enable the unit" absent /tmp/unit-enabled
# Checked before the CLI runs: Python writes bytecode on first import, which is
# fine. The installer must not copy the checkout's stale bytecode in.
check "checkout bytecode not installed" absent /opt/aadoctor/src/aadoctor/__pycache__

echo "--- installed unit ---"
cat /etc/systemd/system/aadoctor.service
echo "--- permissions ---"
stat -c '%a %n' /etc/aadoctor /etc/aadoctor/config.toml /var/lib/aadoctor /var/log/aadoctor

echo
echo "=== 2. CLI through the installed entry point ==="
/usr/local/bin/aadoctor --version
check "CLI runs from /usr/local/bin" /usr/local/bin/aadoctor --version

echo
echo "=== 3. idempotency ==="
# Bytecode is pruned: Python writes it on first import, so it is runtime state,
# not something the installer put there.
snapshot() {
    find /opt/aadoctor /etc/aadoctor /var/lib/aadoctor /usr/local/bin/aadoctor \
         /etc/systemd/system/aadoctor.service \
         -name '__pycache__' -prune -o -print | sort
}

snapshot > /tmp/state.1
bash ./install.sh > /tmp/install.2.log 2>&1
echo "exit: $?"
snapshot > /tmp/state.2
check "second install yields the same file set" diff -q /tmp/state.1 /tmp/state.2

echo
echo "=== 4. configuration preservation ==="
printf '[monitor]\nenabled = false\n' > /etc/aadoctor/config.toml
sha256sum /etc/aadoctor/config.toml > /tmp/config.before
bash ./install.sh > /tmp/install.3.log 2>&1
check "existing configuration untouched" sha256sum -c --status /tmp/config.before
check "installer reports the preservation" grep -q "existing configuration preserved" /tmp/install.3.log
/usr/local/bin/aadoctor status | sed -n '/Configuration:/,+1p'

echo
echo "=== 5. enable and disable are idempotent ==="
/usr/local/bin/aadoctor enable
check "enable succeeds"          /usr/local/bin/aadoctor enable
check "unit now enabled"         present /tmp/unit-enabled
/usr/local/bin/aadoctor disable
check "disable succeeds"         /usr/local/bin/aadoctor disable
check "unit now disabled"        absent /tmp/unit-enabled

echo
echo "=== 6. uninstall without purge ==="
bash /opt/aadoctor/uninstall.sh --yes
echo "exit: $?"
check "/opt/aadoctor removed"    absent  /opt/aadoctor
check "CLI removed"              absent  /usr/local/bin/aadoctor
check "unit removed"             absent  /etc/systemd/system/aadoctor.service
check "configuration preserved"  present /etc/aadoctor/config.toml
check "state preserved"          present /var/lib/aadoctor
check "custom configuration intact" sha256sum -c --status /tmp/config.before

echo
echo "=== 7. reinstall, then purge ==="
bash ./install.sh > /tmp/install.4.log 2>&1
check "configuration survived the reinstall" sha256sum -c --status /tmp/config.before
bash /opt/aadoctor/uninstall.sh --purge --yes
echo "exit: $?"
check "/opt/aadoctor removed"       absent /opt/aadoctor
check "/etc/aadoctor removed"       absent /etc/aadoctor
check "/var/lib/aadoctor removed"   absent /var/lib/aadoctor
check "/var/log/aadoctor removed"   absent /var/log/aadoctor
check "CLI removed"                 absent /usr/local/bin/aadoctor
check "unit removed"                absent /etc/systemd/system/aadoctor.service

echo
echo "=== 8. purge again: absence is not an error ==="
bash /src/uninstall.sh --purge --yes > /tmp/purge.2.log 2>&1
check "second purge exits cleanly" test $? -eq 0

echo
echo "=== 9. NON-INVASIVE GUARANTEE ==="
find /www -type f -exec sha256sum {} \; | sort > /tmp/www.after
find /www -printf '%m %u %g %p\n' | sort > /tmp/www.perms.after
check "/www contents unchanged"                  diff -q /tmp/www.before /tmp/www.after
check "/www permissions and ownership unchanged" diff -q /tmp/www.perms.before /tmp/www.perms.after
check "no crontab created"                       absent /var/spool/cron/crontabs/root

echo "--- every systemctl call made ---"
sort -u /tmp/systemctl.log
foreign="$(grep -cv 'aadoctor\.service\|daemon-reload' /tmp/systemctl.log || true)"
check "no systemctl call against a foreign unit" test "${foreign}" -eq 0

echo
echo "=== 10. incomplete source aborts before installing ==="
rm -rf /empty && mkdir -p /empty && cp /src/install.sh /empty/
(cd /empty && bash ./install.sh > /tmp/install.bad.log 2>&1)
code=$?
grep -i "must run from a complete" /tmp/install.bad.log || true
check "installer aborts"              test "${code}" -ne 0
check "nothing was installed"         absent /opt/aadoctor

echo
echo "=== 11. a checkout inside the install directory is refused ==="
# `git clone ... /opt/aadoctor` is a natural thing to do, and it used to be
# destructive: the swap deleted the clone and the install came out with no
# systemd unit and no configuration. It must refuse, and leave the checkout
# exactly where it is.
rm -rf /opt/aadoctor
mkdir -p /opt/aadoctor
tar -c --exclude='__pycache__' -C /src . | tar -x -C /opt/aadoctor
echo "marker" > /opt/aadoctor/.git-stand-in

(cd /opt/aadoctor && bash ./install.sh > /tmp/install.self.log 2>&1)
code=$?
grep -i "would delete it" /tmp/install.self.log || true

check "installer refuses"                 test "${code}" -ne 0
check "the checkout is still there"       present /opt/aadoctor/install.sh
check "nothing in it was removed"         present /opt/aadoctor/.git-stand-in
check "the unit source survived"          present /opt/aadoctor/systemd/aadoctor.service
check "the example config survived"       present /opt/aadoctor/config.example.toml
check "no staging left behind"            absent /opt/.aadoctor.stage
check "no previous directory left behind" absent /opt/.aadoctor.previous
rm -rf /opt/aadoctor

echo
echo "========================================"
echo "PASS: ${PASS}   FAIL: ${FAIL}"
echo "========================================"
[ "${FAIL}" -eq 0 ]
