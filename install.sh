#!/usr/bin/env bash
#
# aaDoctor installer.
#
# Installs aaDoctor into its own paths and nothing else. It never writes to
# /www, never changes permissions or ownership of aaPanel files, never creates
# a cron entry or a firewall rule, and never restarts a service other than
# aadoctor.service (README.md section 4, SPEC-001, ADR-001).
#
# Installing does not start monitoring. The intended flow is:
#
#   ./install.sh
#   aadoctor doctor
#   aadoctor enable
#
# Usage:
#   ./install.sh            install or upgrade from this checkout
#   ./install.sh --help
#
set -euo pipefail

# --- constants -----------------------------------------------------------
# Every removable path is a literal constant. Nothing below builds a path to
# remove out of a variable that could be empty or unexpected.

readonly INSTALL_DIR="/opt/aadoctor"
readonly STAGE_DIR="/opt/.aadoctor.stage"
readonly PREVIOUS_DIR="/opt/.aadoctor.previous"
readonly CONFIG_DIR="/etc/aadoctor"
readonly CONFIG_FILE="/etc/aadoctor/config.toml"
readonly STATE_DIR="/var/lib/aadoctor"
readonly LOG_DIR="/var/log/aadoctor"
readonly BIN_DIR="/usr/local/bin"
readonly CLI_PATH="/usr/local/bin/aadoctor"
readonly UNIT_PATH="/etc/systemd/system/aadoctor.service"
readonly SERVICE_NAME="aadoctor.service"

# Read-only detection targets. Never written to.
readonly AAPANEL_DIR="/www/server/panel"
readonly NGINX_VHOST_DIR="/www/server/panel/vhost/nginx"

readonly MIN_PYTHON_MAJOR=3
readonly MIN_PYTHON_MINOR=8

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
readonly SOURCE_DIR

SERVICE_WAS_ACTIVE=0

# --- output --------------------------------------------------------------

info()  { printf '%s\n' "$*"; }
ok()    { printf '[OK] %s\n' "$*"; }
warn()  { printf '[WARN] %s\n' "$*" >&2; }
fail()  { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '3,18p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# Removal guard: only aaDoctor's own installation directories, by exact match.
remove_install_path() {
    local target="${1:-}"
    case "${target}" in
        "${INSTALL_DIR}"|"${STAGE_DIR}"|"${PREVIOUS_DIR}") ;;
        *) fail "refusing to remove unexpected path: ${target:-<empty>}" ;;
    esac
    [ -e "${target}" ] || return 0
    rm -rf -- "${target}"
}

# --- checks --------------------------------------------------------------

check_arguments() {
    if [ "$#" -gt 0 ]; then
        case "$1" in
            -h|--help) usage; exit 0 ;;
            *) fail "unknown option: $1" ;;
        esac
    fi
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fail "installation requires root. Re-run with sudo."
    fi
    ok "running as root"
}

check_linux() {
    if [ "$(uname -s)" != "Linux" ]; then
        fail "aaDoctor targets Linux, found $(uname -s)"
    fi
    ok "Linux detected"
}

check_python() {
    if ! command -v python3 >/dev/null 2>&1; then
        fail "python3 not found; aaDoctor needs Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} or newer"
    fi
    PYTHON_BIN="$(command -v python3)"
    readonly PYTHON_BIN

    if ! "${PYTHON_BIN}" -c "import sys; sys.exit(0 if sys.version_info >= (${MIN_PYTHON_MAJOR}, ${MIN_PYTHON_MINOR}) else 1)"; then
        fail "Python ${MIN_PYTHON_MAJOR}.${MIN_PYTHON_MINOR} or newer required, found $("${PYTHON_BIN}" -V 2>&1)"
    fi
    ok "$("${PYTHON_BIN}" -V 2>&1) at ${PYTHON_BIN}"
}

check_systemd() {
    if ! command -v systemctl >/dev/null 2>&1; then
        fail "systemd not found; the aaDoctor daemon requires it"
    fi
    ok "systemd detected"
}

check_source() {
    local required="src/aadoctor aadoctor VERSION uninstall.sh systemd/aadoctor.service"
    local item

    for item in ${required}; do
        if [ ! -e "${SOURCE_DIR}/${item}" ]; then
            fail "this installer must run from a complete aaDoctor checkout or release archive (missing ${item}).
Downloading a published release is not implemented yet - see SPEC-001."
        fi
    done
    ok "installing from ${SOURCE_DIR}"
}

# Detection only. aaDoctor is installed either way; it simply will not monitor
# anything until aaPanel and Nginx are present (README.md sections 80 and 81).
check_aapanel() {
    if [ -d "${AAPANEL_DIR}" ]; then
        ok "aaPanel detected at ${AAPANEL_DIR}"
    else
        warn "aaPanel not detected at ${AAPANEL_DIR}; aaDoctor will not monitor anything here"
    fi

    if [ -d "${NGINX_VHOST_DIR}" ]; then
        ok "Nginx vhost directory detected"
    else
        warn "Nginx vhost directory not found at ${NGINX_VHOST_DIR}; aaDoctor supports aaPanel + Nginx only"
    fi
}

# --- installation --------------------------------------------------------

create_directories() {
    mkdir -p "${CONFIG_DIR}" "${STATE_DIR}" "${STATE_DIR}/offsets" "${STATE_DIR}/incidents" "${LOG_DIR}" "${BIN_DIR}"
    # Incidents may contain client IPs; keep aaDoctor's own data private.
    chmod 0750 "${CONFIG_DIR}" "${STATE_DIR}" "${LOG_DIR}"
    ok "directories ready"
}

stage_files() {
    remove_install_path "${STAGE_DIR}"
    mkdir -p "${STAGE_DIR}"

    cp -a "${SOURCE_DIR}/src" "${STAGE_DIR}/src"
    cp -a "${SOURCE_DIR}/aadoctor" "${STAGE_DIR}/aadoctor"
    cp -a "${SOURCE_DIR}/VERSION" "${STAGE_DIR}/VERSION"
    cp -a "${SOURCE_DIR}/uninstall.sh" "${STAGE_DIR}/uninstall.sh"

    if [ -f "${SOURCE_DIR}/LICENSE" ]; then
        cp -a "${SOURCE_DIR}/LICENSE" "${STAGE_DIR}/LICENSE"
    fi

    chmod 0755 "${STAGE_DIR}/aadoctor" "${STAGE_DIR}/uninstall.sh"
    chmod 0755 "${STAGE_DIR}"
    ok "files staged"
}

swap_installation() {
    # The previous installation is only removed once the new one is in place.
    if [ -d "${INSTALL_DIR}" ]; then
        remove_install_path "${PREVIOUS_DIR}"
        mv "${INSTALL_DIR}" "${PREVIOUS_DIR}"
    fi

    if ! mv "${STAGE_DIR}" "${INSTALL_DIR}"; then
        if [ -d "${PREVIOUS_DIR}" ]; then
            mv "${PREVIOUS_DIR}" "${INSTALL_DIR}"
        fi
        fail "could not install to ${INSTALL_DIR}; the previous installation was restored"
    fi

    remove_install_path "${PREVIOUS_DIR}"
    ok "installed to ${INSTALL_DIR}"
}

install_configuration() {
    if [ -f "${CONFIG_FILE}" ]; then
        ok "existing configuration preserved at ${CONFIG_FILE}"
        return 0
    fi

    if [ ! -f "${SOURCE_DIR}/config.example.toml" ]; then
        warn "config.example.toml not found; aaDoctor will use built-in defaults"
        return 0
    fi

    cp "${SOURCE_DIR}/config.example.toml" "${CONFIG_FILE}"
    chmod 0640 "${CONFIG_FILE}"
    ok "default configuration written to ${CONFIG_FILE}"
}

install_cli() {
    ln -sfn "${INSTALL_DIR}/aadoctor" "${CLI_PATH}"
    ok "command line entry point at ${CLI_PATH}"
}

install_unit() {
    local source_unit="${SOURCE_DIR}/systemd/aadoctor.service"
    [ -f "${source_unit}" ] || fail "missing ${source_unit}"

    # README section 56 hardcodes /usr/bin/python3; use the interpreter that
    # was actually detected on this host instead.
    sed "s|^ExecStart=.*|ExecStart=${PYTHON_BIN} ${INSTALL_DIR}/aadoctor daemon|" \
        "${source_unit}" > "${UNIT_PATH}.new"
    mv "${UNIT_PATH}.new" "${UNIT_PATH}"
    chmod 0644 "${UNIT_PATH}"
    ok "service unit installed at ${UNIT_PATH}"

    systemctl daemon-reload
    ok "systemctl daemon-reload"
}

remember_service_state() {
    if systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null; then
        SERVICE_WAS_ACTIVE=1
    fi
}

restart_if_it_was_running() {
    # aaDoctor's own service only. Nginx, PHP-FPM and MySQL are never touched.
    if [ "${SERVICE_WAS_ACTIVE}" -eq 1 ]; then
        systemctl restart "${SERVICE_NAME}"
        ok "${SERVICE_NAME} restarted (it was running before the upgrade)"
    fi
}

next_steps() {
    local version
    version="$(cat "${INSTALL_DIR}/VERSION" 2>/dev/null || echo unknown)"

    info ""
    info "aaDoctor ${version} installed."
    info ""
    info "Monitoring is not started automatically. Next steps:"
    info ""
    info "  aadoctor doctor"
    info "  aadoctor enable"
    info ""
    info "No aaPanel files were modified."
}

main() {
    check_arguments "$@"

    info "aaDoctor installer"
    info ""

    check_root
    check_linux
    check_python
    check_systemd
    check_source
    check_aapanel

    remember_service_state
    create_directories
    stage_files
    swap_installation
    install_configuration
    install_cli
    install_unit
    restart_if_it_was_running

    next_steps
}

main "$@"
