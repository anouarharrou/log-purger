#!/usr/bin/env bash
# =============================================================================
#  purge_crontab.sh  —  Production-grade cron wrapper for purge_v2.py
#  Version : 2.1.0
# =============================================================================
#
#  Cron example (daily at 02:00):
#    0 2 * * * root /opt/purger/purge_crontab.sh
#
#  Environment overrides (can also be set in .purge_secrets):
#    FILESYSTEM        Mount point to monitor         (default: /)
#    CRITICAL_USAGE    Trigger threshold in %         (default: 75)
#    LOG_KEEP_DAYS     Daily log files to retain      (default: 14)
#    PURGE_TIMEOUT     Max seconds for purge run      (default: 3600)
#    SMTP_HOST / SMTP_PORT / SMTP_USER / SMTP_PASS
#    MAIL_FROM / MAIL_TO   (MAIL_TO supports comma-separated list)
# =============================================================================

set -euo pipefail

# ── Paths ─────────────────────────────────────────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$(realpath "$0")")" && pwd)"
PURGE_DIR="${SCRIPT_DIR}/purger"
PURGE_SCRIPT="${PURGE_DIR}/purge_v2.py"
CONFIG_FILE="${PURGE_DIR}/purge_config.json"
CRON_LOG_DIR="${PURGE_DIR}/cron_logs"
LOG_FILE="${CRON_LOG_DIR}/cron_log_$(date +'%Y-%m-%d').log"
LOCK_FILE="/tmp/purger_cron.lock"

# ── Operational settings ──────────────────────────────────────────────────────
FILESYSTEM="${FILESYSTEM:-/}"
CRITICAL_USAGE="${CRITICAL_USAGE:-75}"
LOG_KEEP_DAYS="${LOG_KEEP_DAYS:-14}"
PURGE_TIMEOUT="${PURGE_TIMEOUT:-3600}"

# ── SMTP credentials — load from env or .purge_secrets (never hard-code here) ─
SECRETS_FILE="${SCRIPT_DIR}/.purge_secrets"
if [[ -f "${SECRETS_FILE}" ]]; then
    # shellcheck source=/dev/null
    source "${SECRETS_FILE}"
fi
SMTP_HOST="${SMTP_HOST:-smtp.gmail.com}"
SMTP_PORT="${SMTP_PORT:-587}"
SMTP_USER="${SMTP_USER:-}"
SMTP_PASS="${SMTP_PASS:-}"
MAIL_FROM="${MAIL_FROM:-${SMTP_USER}}"
MAIL_TO="${MAIL_TO:-}"       # comma-separated for multiple recipients

# ── Runtime state ─────────────────────────────────────────────────────────────
START_TIME="$(date)"
# FIX: removed pointless HOSTNAME=$(echo "$HOSTNAME") — $HOSTNAME is a shell built-in
SERVER_IP="$(hostname -I 2>/dev/null | awk '{print $1}')"
_TEMP_FILES=()

# =============================================================================
#  Cleanup trap — runs on ANY exit (normal, error, or signal)
# =============================================================================
cleanup() {
    local code=$?
    [[ -f "${LOCK_FILE}" ]] && rm -f "${LOCK_FILE}"
    for f in "${_TEMP_FILES[@]+"${_TEMP_FILES[@]}"}"; do
        [[ -f "$f" ]] && rm -f "$f"
    done
    exit "${code}"
}
trap cleanup EXIT

# =============================================================================
#  Logging — writes directly to LOG_FILE, not via outer redirect
#  FIX: avoids the double-logging bug where send_email() wrote >> $LOG_FILE
#       while already inside the outer { } >> $LOG_FILE redirect block.
# =============================================================================
_log() {
    local level="$1"; shift
    local line
    printf -v line '%s [%-5s] %s' "$(date +'%Y-%m-%d %H:%M:%S')" "${level}" "$*"
    echo "${line}" >> "${LOG_FILE}"
    echo "${line}"
}
log_info()  { _log "INFO"  "$@"; }
log_warn()  { _log "WARN"  "$@" >&2; }
log_error() { _log "ERROR" "$@" >&2; }
log_sep()   { _log "INFO"  "$(printf '%.0s─' {1..70})"; }

# =============================================================================
#  Dependency check
# =============================================================================
check_dependencies() {
    local missing=()
    command -v python3  &>/dev/null || missing+=("python3")
    command -v curl     &>/dev/null || missing+=("curl")
    command -v jq       &>/dev/null || missing+=("jq")
    command -v timeout  &>/dev/null || missing+=("timeout")

    if [[ ${#missing[@]} -gt 0 ]]; then
        log_error "Missing required tools: ${missing[*]}"
        log_error "Install: apt-get install -y ${missing[*]}"
        exit 1
    fi

    if [[ ! -f "${PURGE_SCRIPT}" ]]; then
        log_error "Purge script not found: ${PURGE_SCRIPT}"
        _send_email_raw \
            "🚨 [PURGER] Script missing on ${HOSTNAME}" \
            "<p>File not found: <code>${PURGE_SCRIPT}</code></p><p>Disk is at <strong>?%</strong> — manual action may be required.</p>"
        exit 1
    fi

    if [[ ! -f "${CONFIG_FILE}" ]]; then
        log_error "Config file not found: ${CONFIG_FILE}"
        exit 1
    fi
}

# =============================================================================
#  Lock file — prevents overlapping cron executions
# =============================================================================
acquire_lock() {
    if [[ -f "${LOCK_FILE}" ]]; then
        local pid=""
        pid="$(cat "${LOCK_FILE}" 2>/dev/null || true)"
        if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
            log_warn "Another purger instance is running (PID ${pid}). Exiting."
            exit 0
        fi
        log_warn "Stale lock file (PID ${pid:-?}). Removing and continuing."
        rm -f "${LOCK_FILE}"
    fi
    echo $$ > "${LOCK_FILE}"
    log_info "Lock acquired (PID $$)"
}

# =============================================================================
#  Log rotation — keeps last LOG_KEEP_DAYS daily log files
# =============================================================================
rotate_logs() {
    mkdir -p "${CRON_LOG_DIR}"
    find "${CRON_LOG_DIR}" -maxdepth 1 -name 'cron_log_*.log' \
        -mtime "+${LOG_KEEP_DAYS}" -delete 2>/dev/null || true
    local kept
    kept="$(find "${CRON_LOG_DIR}" -maxdepth 1 -name 'cron_log_*.log' | wc -l)"
    log_info "Log rotation: ${kept} file(s) retained (keep=${LOG_KEEP_DAYS} days)"
}

# =============================================================================
#  Disk helpers
#  FIX: df -P (POSIX) replaces df -h — no SI suffixes, safe for arithmetic
# =============================================================================
get_disk_usage_pct() {
    df -P "${FILESYSTEM}" 2>/dev/null \
        | awk 'NR==2 {gsub(/%/,"",$5); print $5}'
}

get_free_mb() {
    df -P -BM "${FILESYSTEM}" 2>/dev/null \
        | awk 'NR==2 {gsub(/M/,"",$4); print $4}'
}

# =============================================================================
#  Email  (curl + STARTTLS, quiet mode, multi-recipient)
# =============================================================================
_send_email_raw() {
    local subject="$1"
    local html_body="$2"

    if [[ -z "${SMTP_USER}" || -z "${SMTP_PASS}" || -z "${MAIL_TO}" ]]; then
        log_warn "SMTP not configured — skipping email notification."
        return 0
    fi

    # FIX: separate local from assignment so mktemp exit-code is not masked
    local temp_file
    temp_file="$(mktemp)"
    _TEMP_FILES+=("${temp_file}")

    # RFC-2822 message (headers + blank line + body)
    {
        printf 'Subject: %s\r\n'        "${subject}"
        printf 'From: %s\r\n'           "${MAIL_FROM}"
        printf 'To: %s\r\n'             "${MAIL_TO}"
        printf 'MIME-Version: 1.0\r\n'
        printf 'Content-Type: text/html; charset=UTF-8\r\n'
        printf 'X-Mailer: purge_crontab/2.1.0\r\n'
        printf '\r\n'
        printf '<html><body style="font-family:sans-serif">'
        printf '<h2 style="color:#2E8B57">%s</h2>' "${subject}"
        printf '%s'                      "${html_body}"
        printf '<hr/><p style="font-size:0.8em;color:#888">Sent by <strong>Purge Bot 2.1</strong> · <code>%s</code> · %s</p>' \
            "${HOSTNAME}" "${SERVER_IP}"
        printf '</body></html>\r\n'
    } > "${temp_file}"

    # Build --mail-rcpt flag for every recipient
    local rcpt_flags=()
    IFS=',' read -ra _rcpts <<< "${MAIL_TO}"
    for rcpt in "${_rcpts[@]}"; do
        rcpt="$(printf '%s' "${rcpt}" | xargs)"
        [[ -n "${rcpt}" ]] && rcpt_flags+=("--mail-rcpt" "${rcpt}")
    done

    # FIX: -s -S (silent, show errors only) replaces -v (extremely noisy in prod)
    # FIX: --starttls smtp explicitly negotiates STARTTLS on port 587
    local curl_exit=0
    curl -s -S \
        --url "smtp://${SMTP_HOST}:${SMTP_PORT}" \
        --starttls smtp \
        --ssl-reqd \
        --mail-from "${MAIL_FROM}" \
        "${rcpt_flags[@]}" \
        --upload-file "${temp_file}" \
        --user "${SMTP_USER}:${SMTP_PASS}" \
        2>> "${LOG_FILE}" || curl_exit=$?

    if [[ "${curl_exit}" -eq 0 ]]; then
        log_info "Email sent → ${MAIL_TO} | ${subject}"
    else
        log_warn "Email failed (curl exit ${curl_exit}) — run continues."
    fi

    rm -f "${temp_file}"
    _TEMP_FILES=("${_TEMP_FILES[@]/${temp_file}/}")
}

send_alert_failure() {
    local title="$1" detail="$2" disk_pct="${3:-?}"
    _send_email_raw "${title}" "
<p>${detail}</p>
<table style='border-collapse:collapse;width:100%'>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Hostname</b></td><td>${HOSTNAME}</td></tr>
  <tr><td style='padding:6px 12px'><b>Server IP</b></td><td>${SERVER_IP}</td></tr>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Filesystem</b></td><td>${FILESYSTEM}</td></tr>
  <tr><td style='padding:6px 12px'><b>Disk usage</b></td><td>${disk_pct}%</td></tr>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Log file</b></td><td><code>${LOG_FILE}</code></td></tr>
  <tr><td style='padding:6px 12px'><b>Time</b></td><td>$(date)</td></tr>
</table>"
}

send_alert_success() {
    local disk_before="$1" disk_after="$2" free_after="$3"
    local svc_html="$4" end_time="$5" purge_exit="$6"

    local icon="✅" color="#2E8B57" label="Completed Successfully"
    if [[ "${purge_exit}" -eq 2 ]]; then
        icon="⚠️"; color="#FFA500"; label="Completed with Partial Failures (check DLQ)"
    fi

    _send_email_raw "${icon} [PURGER] ${label} — ${HOSTNAME}" "
<h3 style='color:${color}'>${icon} ${label}</h3>
<table style='border-collapse:collapse;width:100%'>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Start time</b></td><td>${START_TIME}</td></tr>
  <tr><td style='padding:6px 12px'><b>End time</b></td><td>${end_time}</td></tr>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Hostname</b></td><td>${HOSTNAME}</td></tr>
  <tr><td style='padding:6px 12px'><b>Server IP</b></td><td>${SERVER_IP}</td></tr>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Filesystem</b></td><td>${FILESYSTEM}</td></tr>
  <tr><td style='padding:6px 12px'><b>Disk before</b></td><td>${disk_before}%</td></tr>
  <tr style='background:#f5f5f5'><td style='padding:6px 12px'><b>Disk after</b></td><td>${disk_after}% (${free_after} MB free)</td></tr>
  <tr><td style='padding:6px 12px'><b>Reclaimed</b></td><td>~$((disk_before - disk_after))%</td></tr>
</table>
<h4 style='margin-top:16px'>Services Processed</h4>
<table style='border-collapse:collapse;width:100%'>
  <tr style='background:#333;color:#fff'>
    <th style='padding:6px 12px;text-align:left'>Service</th>
    <th style='padding:6px 12px;text-align:left'>Bucket</th>
    <th style='padding:6px 12px;text-align:left'>Project</th>
  </tr>
  ${svc_html}
</table>"
}

# =============================================================================
#  Config helpers
# =============================================================================
jq_safe() { jq -r "$1" "$2" 2>/dev/null || echo ""; }

build_services_html() {
    local bucket project count
    bucket="$(jq_safe '.config.bucket'    "${CONFIG_FILE}")"
    project="$(jq_safe '.config.project'  "${CONFIG_FILE}")"
    count="$(jq_safe  '.services | length' "${CONFIG_FILE}")"

    # FIX: iterate ALL services, not just services[0]
    local html="" i bg
    for ((i=0; i<count; i++)); do
        local svc; svc="$(jq_safe ".services[${i}].service" "${CONFIG_FILE}")"
        local enabled; enabled="$(jq_safe ".services[${i}].enabled // true" "${CONFIG_FILE}")"
        [[ -z "${svc}" ]]              && continue
        [[ "${enabled}" == "false" ]]  && continue
        bg=$( (( i % 2 == 0 )) && echo "#f5f5f5" || echo "#ffffff" )
        html+="<tr style='background:${bg}'>"
        html+="<td style='padding:6px 12px'>${svc}</td>"
        html+="<td style='padding:6px 12px'>${bucket}</td>"
        html+="<td style='padding:6px 12px'>${project}</td>"
        html+="</tr>"
    done
    echo "${html}"
}

# =============================================================================
#  MAIN
# =============================================================================
main() {
    mkdir -p "${CRON_LOG_DIR}"
    rotate_logs

    log_sep
    log_info "CRON JOB STARTED — ${START_TIME}"
    log_info "Host: ${HOSTNAME} (${SERVER_IP})  Script: ${SCRIPT_DIR}"
    log_sep

    check_dependencies
    acquire_lock

    # ── Disk check ────────────────────────────────────────────────────────
    local disk_before free_before
    disk_before="$(get_disk_usage_pct)"
    free_before="$(get_free_mb)"

    if [[ -z "${disk_before}" ]] || ! [[ "${disk_before}" =~ ^[0-9]+$ ]]; then
        log_error "Cannot read disk usage for '${FILESYSTEM}' — check FILESYSTEM env var."
        send_alert_failure \
            "🚨 [PURGER] Disk check failed on ${HOSTNAME}" \
            "Could not read disk usage for filesystem <code>${FILESYSTEM}</code>." "?"
        exit 1
    fi

    log_info "Disk: ${FILESYSTEM} → ${disk_before}% used / ${free_before} MB free (threshold: ${CRITICAL_USAGE}%)"

    if (( disk_before < CRITICAL_USAGE )); then
        log_info "Usage ${disk_before}% < ${CRITICAL_USAGE}% — no purge needed."
        log_sep
        log_info "CRON JOB ENDED — $(date)"
        exit 0
    fi

    log_warn "Usage ${disk_before}% ≥ ${CRITICAL_USAGE}% — triggering purge."

    # ── Run purge_v2.py ────────────────────────────────────────────────────
    log_sep
    log_info "PURGE STARTED — $(date)"
    log_sep

    local purge_exit=0
    # FIX: quoted variable + execution timeout + --replay-dlq for DLQ self-healing
    timeout "${PURGE_TIMEOUT}" python3 "${PURGE_SCRIPT}" \
        --config "${CONFIG_FILE}" \
        --replay-dlq \
        >> "${LOG_FILE}" 2>&1 \
        || purge_exit=$?

    log_sep
    log_info "PURGE ENDED — $(date) (exit: ${purge_exit})"
    log_sep

    # ── Timeout ────────────────────────────────────────────────────────────
    if [[ "${purge_exit}" -eq 124 ]]; then
        log_error "Purge timed out after ${PURGE_TIMEOUT}s."
        send_alert_failure \
            "🚨 [PURGER] Timed out on ${HOSTNAME}" \
            "purge_v2.py exceeded <strong>${PURGE_TIMEOUT}s</strong> and was killed." \
            "${disk_before}"
        exit 1
    fi

    # ── Fatal startup error ────────────────────────────────────────────────
    if [[ "${purge_exit}" -eq 1 ]]; then
        log_error "Purge fatal error (exit 1) — bad config or missing credentials."
        send_alert_failure \
            "❌ [PURGER] Fatal error on ${HOSTNAME}" \
            "purge_v2.py exited with code <strong>1</strong>. No files were processed. Check config and AWS credentials." \
            "${disk_before}"
        exit 1
    fi

    # ── Post-purge disk ────────────────────────────────────────────────────
    local disk_after free_after
    disk_after="$(get_disk_usage_pct)"
    free_after="$(get_free_mb)"
    log_info "Disk after: ${disk_after}% used / ${free_after} MB free  (reclaimed: ~$((disk_before - disk_after))%)"

    if (( disk_after >= 95 )); then
        log_error "CRITICAL: disk still at ${disk_after}% after purge — manual intervention needed."
        send_alert_failure \
            "🚨 [PURGER] CRITICAL disk still full on ${HOSTNAME}" \
            "Disk remains at <strong>${disk_after}%</strong> after purge. Free: <strong>${free_after} MB</strong>. <br/><strong>Immediate manual action required.</strong>" \
            "${disk_after}"
    fi

    # ── Success / partial-success email ───────────────────────────────────
    local svc_html
    svc_html="$(build_services_html)"
    send_alert_success \
        "${disk_before}" "${disk_after}" "${free_after}" \
        "${svc_html}" "$(date)" "${purge_exit}"

    log_sep
    log_info "CRON JOB ENDED — $(date)"
    log_sep
}

main "$@"