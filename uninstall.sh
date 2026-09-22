#!/usr/bin/env bash
#
# aaDoctor uninstaller.
#
# Removes aaDoctor's own paths and nothing else. It never touches /www, never
# changes permissions or ownership of aaPanel files, and never stops a service
# other than aadoctor.service (README.md sections 53 to 55, SPEC-001).
#
# Usage:
#   ./uninstall.sh              remove the program, keep configuration and state
#   ./uninstall.sh --purge      also remove configuration, state and logs
#   ./uninstall.sh --yes        do not ask for confirmation
#   ./uninstall.sh --help
#
set -euo pipefail

# --- constants -----------------------------------------------------------
# Every removable path is an exact literal. Nothing here is built from input.

readonly INSTALL_DIR="/opt/aadoctor"
readonly CONFIG_DIR="/etc/aadoctor"
readonly STATE_DIR="/var/lib/aadoctor"
readonly LOG_DIR="/var/log/aadoctor"
readonly CLI_PATH="/usr/local/bin/aadoctor"
readonly UNIT_PATH="/etc/systemd/system/aadoctor.service"
readonly SERVICE_NAME="aadoctor.service"

PURGE=0
ASSUME_YES=0

info() { printf '%s\n' "$*"; }
ok()   { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
fail() { printf '[FAIL] %s\n' "$*" >&2; exit 1; }

usage() {
    sed -n '3,14p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

# Removal guard: exact allowlist of aaDoctor's own paths. An unexpected or
# empty value aborts instead of being removed.
remove_owned_path() {
    local target="${1:-}"
    case "${target}" in
        "${INSTALL_DIR}"|"${CONFIG_DIR}"|"${STATE_DIR}"|"${LOG_DIR}"|"${CLI_PATH}"|"${UNIT_PATH}") ;;
        *) fail "refusing to remove unexpected path: ${target:-<empty>}" ;;
    esac

    if [ ! -e "${target}" ] && [ ! -L "${target}" ]; then
        return 0
    fi

    rm -rf -- "${target}"
    ok "removed ${target}"
}

parse_arguments() {
    while [ "$#" -gt 0 ]; do
        case "$1" in
            --purge) PURGE=1 ;;
            -y|--yes) ASSUME_YES=1 ;;
            -h|--help) usage; exit 0 ;;
            *) fail "unknown option: $1" ;;
        esac
        shift
    done
}

check_root() {
    if [ "$(id -u)" -ne 0 ]; then
        fail "uninstalling requires root. Re-run with sudo."
    fi
}

confirm() {
    if [ "${ASSUME_YES}" -eq 1 ]; then
        return 0
    fi

    info "This will remove:"
    info "  ${UNIT_PATH}"
    info "  ${CLI_PATH}"
    info "  ${INSTALL_DIR}"
    if [ "${PURGE}" -eq 1 ]; then
        info "  ${CONFIG_DIR}"
        info "  ${STATE_DIR}"
        info "  ${LOG_DIR}"
    else
        info ""
        info "Configuration and state are preserved:"
        info "  ${CONFIG_DIR}"
        info "  ${STATE_DIR}"
    fi
    info ""

    printf 'Continue? [y/N] '
    local answer=""
    read -r answer || true
    case "${answer}" in
        y|Y|yes|YES) return 0 ;;
        *) info "Aborted. Nothing was removed."; exit 0 ;;
    esac
}

stop_service() {
    if ! command -v systemctl >/dev/null 2>&1; then
        warn "systemctl not found; skipping service shutdown"
        return 0
    fi

    # aaDoctor's own unit only.
    systemctl stop "${SERVICE_NAME}" >/dev/null 2>&1 || true
    systemctl disable "${SERVICE_NAME}" >/dev/null 2>&1 || true
    ok "${SERVICE_NAME} stopped and disabled"
}

reload_systemd() {
    if command -v systemctl >/dev/null 2>&1; then
        systemctl daemon-reload || warn "systemctl daemon-reload failed"
    fi
}

report() {
    info ""
    if [ "${PURGE}" -eq 1 ]; then
        info "aaDoctor completely removed."
    else
        info "aaDoctor removed."
        info ""
        info "Configuration and state were preserved:"
        info "  ${CONFIG_DIR}"
        info "  ${STATE_DIR}"
        info ""
        info "Run this script with --purge to remove them as well."
    fi
    info ""
    info "No aaPanel files were modified."
}

main() {
    parse_arguments "$@"
    check_root
    confirm

    stop_service

    remove_owned_path "${UNIT_PATH}"
    reload_systemd

    remove_owned_path "${CLI_PATH}"
    remove_owned_path "${INSTALL_DIR}"

    if [ "${PURGE}" -eq 1 ]; then
        remove_owned_path "${CONFIG_DIR}"
        remove_owned_path "${STATE_DIR}"
        remove_owned_path "${LOG_DIR}"
    fi

    report
}

main "$@"
