#!/usr/bin/env bash
#
# SPEC-007 end to end, against the real daemon.
#
# Builds a synthetic aaPanel tree, drives a load curve through a file the
# collector is pointed at, lets the daemon discover, follow, parse, aggregate
# and freeze an incident on its own, and then runs `aadoctor diagnose` against
# what it wrote. Nothing is stubbed between the log lines and the answer.
#
# Three scenarios, all from SPEC-007:
#   1. one site, one path, one address, and failures  -> named, high confidence
#   2. load with the traffic spread across ten sites  -> no cause identified
#   3. a site that is a third of the traffic and is failing -> named anyway
#
# Runs in a disposable container. It writes only under /tmp and /var/lib.

set -u

WORK="${WORK:-/work}"
ROOT=/tmp/aadoctor-diagnose
PANEL="$ROOT/www/server/panel"
VHOSTS="$PANEL/vhost/nginx"
WWWLOGS="$ROOT/www/wwwlogs"
STATE=/var/lib/aadoctor
LOADAVG=/tmp/fake-loadavg
DAEMON_LOG=/tmp/aadoctor-daemon.log

PASS=0
FAIL=0

check() {
  local label="$1" expected="$2" actual="$3"
  if [ "$expected" = "$actual" ]; then
    printf 'PASS  %s\n' "$label"
    PASS=$((PASS + 1))
  else
    printf 'FAIL  %s\n        expected: %s\n        actual:   %s\n' \
      "$label" "$expected" "$actual"
    FAIL=$((FAIL + 1))
  fi
}

contains() {
  local label="$1" needle="$2" haystack="$3"
  case "$haystack" in
    *"$needle"*) printf 'PASS  %s\n' "$label"; PASS=$((PASS + 1)) ;;
    *) printf 'FAIL  %s\n        missing: %s\n' "$label" "$needle"; FAIL=$((FAIL + 1)) ;;
  esac
}

absent() {
  local label="$1" needle="$2" haystack="$3"
  case "$haystack" in
    *"$needle"*) printf 'FAIL  %s\n        present: %s\n' "$label" "$needle"; FAIL=$((FAIL + 1)) ;;
    *) printf 'PASS  %s\n' "$label"; PASS=$((PASS + 1)) ;;
  esac
}

if [ ! -f /.dockerenv ] && [ "${AADOCTOR_INTEGRATION_ALLOW:-0}" != "1" ]; then
  echo "refusing to run outside a container; set AADOCTOR_INTEGRATION_ALLOW=1" >&2
  exit 1
fi

# --- the synthetic server -------------------------------------------------

reset_tree() {
  # Discovery reads aaPanel's real paths, so the synthetic tree is put there
  # through a symlink. Only a symlink this script made is ever removed: if a
  # real /www is present, the script refuses rather than touching it.
  if [ -e /www ] && [ ! -L /www ]; then
    echo "refusing to run: /www exists and is not a link this script made" >&2
    exit 1
  fi
  rm -f /www

  rm -rf "$ROOT" "$STATE"
  mkdir -p "$VHOSTS" "$WWWLOGS" "$STATE"
  ln -s "$ROOT/www" /www
}

vhost() {
  local name="$1"
  cat > "$VHOSTS/$name.conf" <<EOF
server
{
    listen 80;
    server_name $name;
    root /www/wwwroot/$name;
    access_log /www/wwwlogs/$name.log;
    error_log /www/wwwlogs/$name.error.log;
}
EOF
  : > "$WWWLOGS/$name.log"
  : > "$WWWLOGS/$name.error.log"
}

set_load() {
  # load1 load5 load15 running/total lastpid, as the kernel writes it.
  printf '%s %s %s 2/840 1234\n' "$1" "$2" "$3" > "$LOADAVG"
}

# The collector reads /proc/loadavg by default; the scenario points it at a
# file it controls instead. That is the only substitution in this script -
# discovery, following, parsing, aggregation, the incident and the diagnosis
# are all the real thing.
run_daemon() {
  local interval="$1"
  cat > /etc/aadoctor/config.toml <<EOF
[monitor]
enabled = true
interval_seconds = $interval

[discovery]
interval_seconds = 5

[load]
enabled = true
trigger_per_cpu = 1.0
critical_per_cpu = 2.0
recovery_per_cpu = 0.75
trigger_polls = 2
recovery_polls = 3
EOF

  PYTHONPATH="$WORK/src" PYTHONDONTWRITEBYTECODE=1 python3 -c "
import pathlib, sys
from aadoctor import daemon
from aadoctor.collectors import load
from aadoctor.config import load as load_config

load.LOADAVG_PATH = pathlib.Path('$LOADAVG')
daemon.run(load_config(pathlib.Path('/etc/aadoctor/config.toml')),
           log_file=pathlib.Path('$DAEMON_LOG'), verbose=True)
" &
  DAEMON_PID=$!
}

stop_daemon() {
  if [ -n "${DAEMON_PID:-}" ] && kill -0 "$DAEMON_PID" 2>/dev/null; then
    kill -TERM "$DAEMON_PID" 2>/dev/null
    wait "$DAEMON_PID" 2>/dev/null
  fi
  DAEMON_PID=""
}

aadoctor() {
  PYTHONPATH="$WORK/src" PYTHONDONTWRITEBYTECODE=1 python3 "$WORK/aadoctor" "$@"
}

mkdir -p /etc/aadoctor

# --- scenario 1 -----------------------------------------------------------

echo
echo "=== SCENARIO 1  one site, one path, one address, and failures"
echo

reset_tree
vhost site-a.com.br
vhost site-b.com.br
vhost site-c.com.br
set_load 1.6 1.4 1.2      # 0.40 per core on 4 CPUs

CPUS=$(python3 -c "import os; print(os.cpu_count())")
echo "host CPUs: $CPUS"

run_daemon 5
sleep 8

# A quiet period first, so the window has a previous rate to compare against.
python3 - <<'PY'
import time
quiet = open("/tmp/aadoctor-diagnose/www/wwwlogs/site-b.com.br.log", "a")
line = ('10.0.0.5 - - [22/Sep/2026:18:30:%02d -0300] "GET / HTTP/1.1" 200 512 '
        '"-" "Mozilla/5.0"\n')
for second in range(130):
    quiet.write(line % (second % 60))
    quiet.flush()
    time.sleep(1)
PY

# The burst: 9,500 requests in about twenty seconds.
python3 - <<'PY'
import time

LOGS = "/tmp/aadoctor-diagnose/www/wwwlogs/"
access = '%s - - [22/Sep/2026:18:33:%02d -0300] "GET %s HTTP/1.1" %s 512 "-" "curl/8.4"\n'

a = open(LOGS + "site-a.com.br.log", "a")
b = open(LOGS + "site-b.com.br.log", "a")
c = open(LOGS + "site-c.com.br.log", "a")
errors = open(LOGS + "site-a.com.br.error.log", "a")

def line(handle, ip, path, status, second):
    handle.write(access % (ip, second % 60, path, status))

for index in range(9_500):
    second = index // 500
    if index < 4_620:
        line(a, "185.1.2.3", "/wp-cron.php", 200, second)
    elif index < 5_000:
        line(a, "10.0.0.9", "/wp-cron.php", 200, second)
    elif index < 7_000:
        line(a, "10.0.0.9", "/", 200, second)
    elif index < 7_880:
        line(a, "10.0.0.10", "/produto/123", 200, second)
    elif index < 8_000:
        line(a, "10.0.0.10", "/wp-admin/admin-ajax.php", 502, second)
    elif index < 8_900:
        line(b, "10.0.1.1", "/", 200, second)
    else:
        line(c, "10.0.2.1", "/", 200, second)

    if index % 40 == 0 and index < 1_600:
        errors.write(
            "2026/09/22 18:33:%02d [error] 900#900: *77 upstream timed out "
            "(110: Connection timed out) while reading response header from "
            "upstream, client: 185.1.2.3, server: site-a.com.br, "
            'request: "GET /wp-cron.php HTTP/1.1", upstream: '
            '"fastcgi://unix:/tmp/php-cgi.sock:", host: "site-a.com.br"\n'
            % (second % 60)
        )

    if index % 500 == 499:
        for handle in (a, b, c, errors):
            handle.flush()
        time.sleep(1)

for handle in (a, b, c, errors):
    handle.flush()
PY

# Now press the machine. Four values above the trigger, one of them critical.
for per_core in 1.2 1.8 2.9 2.4; do
  load1=$(python3 -c "print(round($per_core * $CPUS, 2))")
  set_load "$load1" "$(python3 -c "print(round($load1 * 0.7, 2))")" \
           "$(python3 -c "print(round($load1 * 0.45, 2))")"
  sleep 6
done

# And let it recover.
for per_core in 0.6 0.5 0.4 0.4; do
  load1=$(python3 -c "print(round($per_core * $CPUS, 2))")
  set_load "$load1" "$load1" "$load1"
  sleep 6
done

sleep 3
stop_daemon

COUNT=$(ls "$STATE/incidents" 2>/dev/null | wc -l | tr -d ' ')
check "one continuous spike produced one incident" "1" "$COUNT"

ID=$(ls "$STATE/incidents" | head -1 | sed 's/\.json$//')
echo "incident: $ID"

REPORT=$(aadoctor diagnose 2>&1)
echo "$REPORT"

contains "the site is named"            "site-a.com.br"       "$REPORT"
contains "the path is named"            "/wp-cron.php"        "$REPORT"
contains "the address is named"         "185.1.2.3"           "$REPORT"
contains "site domination fired"        "ONE_SITE_DOMINATING" "$REPORT"
contains "path domination fired"        "ONE_URL_DOMINATING"  "$REPORT"
contains "address domination fired"     "ONE_IP_DOMINATING"   "$REPORT"
contains "upstream timeouts fired"      "UPSTREAM_TIMEOUT"    "$REPORT"
contains "the confidence is reported"   "CONFIDENCE"          "$REPORT"
contains "the wording stays a reading"  "Evidence points to"  "$REPORT"
absent   "no cause is claimed as proven" "root cause"         "$REPORT"
absent   "no action is recommended"      "systemctl"          "$REPORT"

SHOWN=$(aadoctor show "$ID" 2>&1)
absent "show stays factual: no findings"    "FINDINGS"   "$SHOWN"
absent "show stays factual: no confidence"  "CONFIDENCE" "$SHOWN"

BEFORE=$(md5sum "$STATE/incidents/$ID.json" | cut -d' ' -f1)
aadoctor diagnose >/dev/null 2>&1
aadoctor diagnose --json >/dev/null 2>&1
AFTER=$(md5sum "$STATE/incidents/$ID.json" | cut -d' ' -f1)
check "diagnosing did not touch the incident file" "$BEFORE" "$AFTER"

FIRST=$(aadoctor diagnose --json 2>&1)
SECOND=$(aadoctor diagnose --json 2>&1)
check "two runs produce the same document" "$(echo "$FIRST" | md5sum)" \
                                           "$(echo "$SECOND" | md5sum)"

VERSION=$(echo "$FIRST" | python3 -c "import json,sys; print(json.load(sys.stdin)['ruleset_version'])")
check "the ruleset version is recorded" "1" "$VERSION"

WIDEST=$(echo "$REPORT" | awk '{ print length }' | sort -n | tail -1)
if [ "$WIDEST" -le 80 ]; then
  printf 'PASS  the report fits eighty columns (%s)\n' "$WIDEST"; PASS=$((PASS + 1))
else
  printf 'FAIL  the report is %s columns wide\n' "$WIDEST"; FAIL=$((FAIL + 1))
fi

# --- scenario 2 -----------------------------------------------------------

echo
echo "=== SCENARIO 2  load with the traffic spread across ten sites"
echo

reset_tree
for index in 0 1 2 3 4 5 6 7 8 9; do
  vhost "site-$index.com.br"
done
set_load 1.6 1.4 1.2

run_daemon 5
sleep 8

python3 - <<'PY'
import time

LOGS = "/tmp/aadoctor-diagnose/www/wwwlogs/"
access = '10.0.%d.%d - - [22/Sep/2026:18:33:%02d -0300] "GET %s HTTP/1.1" 200 512 "-" "Mozilla/5.0"\n'
paths = ["/", "/produto", "/sobre", "/contato", "/blog"]

handles = [open(LOGS + "site-%d.com.br.log" % index, "a") for index in range(10)]
for index in range(9_500):
    site = index % 10
    # The path and address vary independently of the site. Using index % 5 for
    # the path alongside index % 10 for the site would give every site exactly
    # one endpoint, which is not a distributed server at all.
    position = index // 10
    handles[site].write(access % (site, position % 40 + 1, (index // 500) % 60,
                                  paths[position % 5]))
    if index % 500 == 499:
        for handle in handles:
            handle.flush()
        time.sleep(1)
for handle in handles:
    handle.flush()
PY

for per_core in 1.2 1.9 2.6 2.1; do
  load1=$(python3 -c "print(round($per_core * $CPUS, 2))")
  set_load "$load1" "$load1" "$load1"
  sleep 6
done
for per_core in 0.6 0.5 0.4 0.4; do
  load1=$(python3 -c "print(round($per_core * $CPUS, 2))")
  set_load "$load1" "$load1" "$load1"
  sleep 6
done
sleep 3
stop_daemon

REPORT=$(aadoctor diagnose 2>&1)
echo "$REPORT"

contains "no cause is identified"       "No clear log-based cause" "$REPORT"
absent   "no site is named"             "PRIMARY SITE"             "$REPORT"
absent   "no path is named"             "PRIMARY PATH"             "$REPORT"
contains "the load is still recorded"   "Peak load:"               "$REPORT"

WIDEST=$(echo "$REPORT" | awk '{ print length }' | sort -n | tail -1)
if [ "$WIDEST" -le 80 ]; then
  printf 'PASS  the inconclusive report fits eighty columns (%s)\n' "$WIDEST"
  PASS=$((PASS + 1))
else
  printf 'FAIL  the inconclusive report is %s columns wide\n' "$WIDEST"
  FAIL=$((FAIL + 1))
fi

# --- scenario 3 -----------------------------------------------------------

echo
echo "=== SCENARIO 3  a site that is a third of the traffic and is failing"
echo

reset_tree
vhost site-a.com.br
vhost site-b.com.br
vhost site-c.com.br
set_load 1.6 1.4 1.2

run_daemon 5
sleep 8

python3 - <<'PY'
import time

LOGS = "/tmp/aadoctor-diagnose/www/wwwlogs/"
access = '%s - - [22/Sep/2026:18:33:%02d -0300] "GET %s HTTP/1.1" %s 512 "-" "Mozilla/5.0"\n'

a = open(LOGS + "site-a.com.br.log", "a")
b = open(LOGS + "site-b.com.br.log", "a")
c = open(LOGS + "site-c.com.br.log", "a")
errors = open(LOGS + "site-b.com.br.error.log", "a")

paths = ["/", "/produto", "/sobre", "/blog", "/contato"]
timeouts = 0

for index in range(9_000):
    second = (index // 500) % 60
    site = index % 3
    if site == 0:
        a.write(access % ("10.0.0.%d" % (index % 200 + 1), second, paths[index % 5], 200))
    elif site == 1:
        status = 200
        if index % 6 == 1:
            status = 502
        elif index % 12 == 7:
            status = 504
        b.write(access % ("10.0.1.%d" % (index % 200 + 1), second, paths[index % 5], status))
        if timeouts < 300 and index % 10 == 1:
            timeouts += 1
            errors.write(
                "2026/09/22 18:33:%02d [error] 901#901: *88 upstream timed out "
                "(110: Connection timed out) while connecting to upstream, "
                "client: 10.0.1.7, server: site-b.com.br, "
                'request: "GET /checkout HTTP/1.1", upstream: '
                '"fastcgi://unix:/tmp/php-cgi.sock:", host: "site-b.com.br"\n' % second
            )
    else:
        c.write(access % ("10.0.2.%d" % (index % 200 + 1), second, paths[index % 5], 200))

    if index % 500 == 499:
        for handle in (a, b, c, errors):
            handle.flush()
        time.sleep(1)

for handle in (a, b, c, errors):
    handle.flush()
print("upstream timeouts written:", timeouts)
PY

for per_core in 1.3 2.0 2.5 2.2; do
  load1=$(python3 -c "print(round($per_core * $CPUS, 2))")
  set_load "$load1" "$load1" "$load1"
  sleep 6
done
for per_core in 0.6 0.5 0.4 0.4; do
  load1=$(python3 -c "print(round($per_core * $CPUS, 2))")
  set_load "$load1" "$load1" "$load1"
  sleep 6
done
sleep 3
stop_daemon

REPORT=$(aadoctor diagnose 2>&1)
echo "$REPORT"

contains "the failing site is named"      "site-b.com.br"       "$REPORT"
contains "its timeouts are the evidence"  "UPSTREAM_TIMEOUT"    "$REPORT"
contains "its 5xx responses too"          "HTTP_5XX_SPIKE"      "$REPORT"
absent   "it did not need to dominate"    "ONE_SITE_DOMINATING" "$REPORT"

# --- result ---------------------------------------------------------------

echo
echo "passed: $PASS   failed: $FAIL"
[ "$FAIL" -eq 0 ] || exit 1
