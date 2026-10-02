#!/bin/sh
set -eu

# This is the project-level equivalent of an unless-stopped policy for Apple
# container. launchd keeps this process alive and starts it at user login; the
# loop only starts the two containers owned by this project.

umask 077

die() {
    printf '%s\n' "Error: $*" >&2
    exit 1
}

SCRIPT_DIR=$(CDPATH= cd -- "$(/usr/bin/dirname -- "$0")" && pwd)
DEFAULT_PROJECT_DIR=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)

PROJECT_DIR=${TG_WATERMARKER_PROJECT_DIR:-$DEFAULT_PROJECT_DIR}
PLIST_LABEL=com.tg-watermarker.supervisor
SOURCE_SCRIPT="$PROJECT_DIR/scripts/tg-watermarker-supervisor.sh"
PLIST_TEMPLATE="$PROJECT_DIR/launchd/$PLIST_LABEL.plist.template"

HOME_DIR=${HOME:-}
if [ -n "${TG_WATERMARKER_RUNTIME_DIR:-}" ]; then
    RUNTIME_DIR=$TG_WATERMARKER_RUNTIME_DIR
else
    [ -n "$HOME_DIR" ] || die "HOME is required to locate the supervisor runtime"
    RUNTIME_DIR="$HOME_DIR/Library/Application Support/TG_watermarker"
fi

if [ -n "${TG_WATERMARKER_STATE_DIR:-}" ]; then
    STATE_DIR=$TG_WATERMARKER_STATE_DIR
else
    STATE_DIR="$RUNTIME_DIR/state"
fi

RUNTIME_SCRIPT="$RUNTIME_DIR/tg-watermarker-supervisor.sh"
LEGACY_STATE_DIR="$PROJECT_DIR/.container-data/tg-watermarker-supervisor"
LEGACY_DISABLED_MARKER="$LEGACY_STATE_DIR/.disabled"

if [ -n "${TG_WATERMARKER_PLIST_PATH:-}" ]; then
    PLIST_PATH=$TG_WATERMARKER_PLIST_PATH
else
    [ -n "$HOME_DIR" ] || die "HOME is required to locate the LaunchAgent plist"
    PLIST_PATH="$HOME_DIR/Library/LaunchAgents/$PLIST_LABEL.plist"
fi

LAUNCHCTL=${TG_WATERMARKER_LAUNCHCTL:-/bin/launchctl}
CONFIGURED_CONTAINER_CLI=${TG_WATERMARKER_CONTAINER_CLI:-}
POLL_SECONDS=${TG_WATERMARKER_POLL_SECONDS:-5}

BOT_CONTAINER=tg-watermarker-bot
API_CONTAINER=tg-watermarker-telegram-bot-api
DISABLED_MARKER="$STATE_DIR/.disabled"

require_absolute_path() {
    case "$1" in
        /*)
            ;;
        *)
            die "Expected an absolute path: $1"
            ;;
    esac
}

require_absolute_path "$PROJECT_DIR"
require_absolute_path "$SOURCE_SCRIPT"
require_absolute_path "$RUNTIME_DIR"
require_absolute_path "$RUNTIME_SCRIPT"
require_absolute_path "$STATE_DIR"
require_absolute_path "$PLIST_PATH"
require_absolute_path "$LAUNCHCTL"

[ "$RUNTIME_DIR" != / ] || die "TG_WATERMARKER_RUNTIME_DIR must not be the filesystem root"
[ "$STATE_DIR" != / ] || die "TG_WATERMARKER_STATE_DIR must not be the filesystem root"

case "$POLL_SECONDS" in
    ''|*[!0-9]*)
        die "TG_WATERMARKER_POLL_SECONDS must be a positive integer"
        ;;
esac
[ "$POLL_SECONDS" -gt 0 ] || die "TG_WATERMARKER_POLL_SECONDS must be greater than zero"

ensure_runtime_dir() {
    /bin/mkdir -p "$RUNTIME_DIR"
    /bin/chmod 700 "$RUNTIME_DIR"
}

ensure_state_dir() {
    /bin/mkdir -p "$STATE_DIR"
    /bin/chmod 700 "$STATE_DIR"
}

marker_exists() {
    [ -e "$DISABLED_MARKER" ]
}

legacy_marker_exists() {
    [ "$LEGACY_DISABLED_MARKER" != "$DISABLED_MARKER" ] \
        && [ -e "$LEGACY_DISABLED_MARKER" ]
}

launchctl_available() {
    [ -x "$LAUNCHCTL" ]
}

require_launchctl() {
    launchctl_available \
        || die "launchctl was not found at the required absolute path: $LAUNCHCTL"
}

launchd_domain() {
    printf 'gui/%s\n' "$(/usr/bin/id -u)"
}

launchd_target() {
    printf '%s/%s\n' "$(launchd_domain)" "$PLIST_LABEL"
}

find_container_cli() {
    cli=$CONFIGURED_CONTAINER_CLI
    if [ -z "$cli" ]; then
        cli=$(command -v container 2>/dev/null || true)
    else
        case "$cli" in
            /*)
                ;;
            *)
                cli=$(command -v "$cli" 2>/dev/null || true)
                ;;
        esac
    fi

    case "$cli" in
        /*)
            [ -x "$cli" ] || return 1
            printf '%s\n' "$cli"
            ;;
        *)
            return 1
            ;;
    esac
}

resolve_container_cli() {
    CONTAINER_CLI=$(find_container_cli) \
        || die "Apple container CLI was not found; install it or set TG_WATERMARKER_CONTAINER_CLI"
}

atomic_copy_file() {
    source_path=$1
    target_path=$2
    target_mode=$3
    [ -r "$source_path" ] || die "Source file is missing or unreadable: $source_path"

    target_dir=$(/usr/bin/dirname "$target_path")
    target_name=$(/usr/bin/basename "$target_path")
    /bin/mkdir -p "$target_dir"
    temp_path=$(/usr/bin/mktemp "$target_dir/.$target_name.tmp.XXXXXX")
    trap '/bin/rm -f "$temp_path"' EXIT HUP INT TERM
    /bin/cp "$source_path" "$temp_path"
    /bin/chmod "$target_mode" "$temp_path"
    /bin/mv -f "$temp_path" "$target_path"
    trap - EXIT HUP INT TERM
}

install_runtime_script() {
    ensure_runtime_dir
    atomic_copy_file "$SOURCE_SCRIPT" "$RUNTIME_SCRIPT" 700
}

migrate_legacy_marker() {
    if [ -e "$DISABLED_MARKER" ] || ! legacy_marker_exists; then
        return 0
    fi

    # The previous implementation stored this marker under the checkout. Copy
    # it into the home-directory state location before the first bootstrap so
    # an intentional stop cannot be lost during the TCC-safe migration.
    atomic_copy_file "$LEGACY_DISABLED_MARKER" "$DISABLED_MARKER" 600
}

clear_legacy_marker() {
    if [ "$LEGACY_DISABLED_MARKER" != "$DISABLED_MARKER" ]; then
        /bin/rm -f "$LEGACY_DISABLED_MARKER"
    fi
}

container_exists() {
    "$CONTAINER_CLI" list --all --quiet 2>/dev/null \
        | /usr/bin/grep -Fxq "$1"
}

container_state() {
    "$CONTAINER_CLI" inspect "$1" 2>/dev/null \
        | /usr/bin/awk -F'"' '/"state"[[:space:]]*:/ { print $4; exit }' \
        || true
}

log_message() {
    printf '%s\n' "$*"
}

ensure_container_system() {
    if "$CONTAINER_CLI" system status >/dev/null 2>&1; then
        return 0
    fi

    log_message "Apple container service is not running; attempting to start it."
    if ! "$CONTAINER_CLI" system start >/dev/null 2>&1; then
        log_message "Apple container service could not be started; will retry."
        return 1
    fi
    return 0
}

start_project_container() {
    name=$1
    log_message "Starting project container: $name"
    if ! "$CONTAINER_CLI" start "$name" >/dev/null 2>&1; then
        log_message "Could not start project container: $name; will retry."
    fi
}

reconcile_once() {
    marker_exists && return 0

    ensure_container_system || return 0

    # Do not create containers or start the API on its own. The bot container
    # is the supervisor's ownership anchor, which keeps this scope project-only.
    if ! container_exists "$BOT_CONTAINER"; then
        return 0
    fi

    if ! container_exists "$API_CONTAINER"; then
        log_message "Required project API container is absent; run ./startup.sh start."
        return 0
    fi

    api_state=$(container_state "$API_CONTAINER")
    case "$api_state" in
        running)
            ;;
        starting)
            return 0
            ;;
        *)
            start_project_container "$API_CONTAINER"
            return 0
            ;;
    esac

    bot_state=$(container_state "$BOT_CONTAINER")
    case "$bot_state" in
        running|starting)
            ;;
        *)
            start_project_container "$BOT_CONTAINER"
            ;;
    esac
}

xml_escape() {
    /usr/bin/sed \
        -e 's/&/\&amp;/g' \
        -e 's/</\&lt;/g' \
        -e 's/>/\&gt;/g' \
        -e 's/"/\&quot;/g' \
        -e "s/'/\&apos;/g"
}

sed_replacement_escape() {
    /usr/bin/sed 's/[\\&|]/\\&/g'
}

render_plist() {
    resolve_container_cli
    [ -r "$RUNTIME_SCRIPT" ] \
        || die "Runtime supervisor script is missing: $RUNTIME_SCRIPT"
    [ -r "$PLIST_TEMPLATE" ] \
        || die "LaunchAgent template is missing: $PLIST_TEMPLATE"

    project_value=$(xml_escape <<EOF
$PROJECT_DIR
EOF
)
    state_value=$(xml_escape <<EOF
$STATE_DIR
EOF
)
    cli_value=$(xml_escape <<EOF
$CONTAINER_CLI
EOF
)
    runtime_dir_value=$(xml_escape <<EOF
$RUNTIME_DIR
EOF
)
    runtime_script_value=$(xml_escape <<EOF
$RUNTIME_SCRIPT
EOF
)

    project_value=$(printf '%s\n' "$project_value" | sed_replacement_escape)
    state_value=$(printf '%s\n' "$state_value" | sed_replacement_escape)
    cli_value=$(printf '%s\n' "$cli_value" | sed_replacement_escape)
    runtime_dir_value=$(printf '%s\n' "$runtime_dir_value" | sed_replacement_escape)
    runtime_script_value=$(printf '%s\n' "$runtime_script_value" | sed_replacement_escape)

    /usr/bin/sed \
        -e "s|__PROJECT_DIR__|$project_value|g" \
        -e "s|__STATE_DIR__|$state_value|g" \
        -e "s|__CONTAINER_CLI__|$cli_value|g" \
        -e "s|__RUNTIME_DIR__|$runtime_dir_value|g" \
        -e "s|__RUNTIME_SCRIPT__|$runtime_script_value|g" \
        "$PLIST_TEMPLATE"
}

install_plist() {
    ensure_state_dir
    plist_dir=$(/usr/bin/dirname "$PLIST_PATH")
    /bin/mkdir -p "$plist_dir"

    temp_plist=$(/usr/bin/mktemp "$PLIST_PATH.tmp.XXXXXX")
    trap '/bin/rm -f "$temp_plist"' EXIT HUP INT TERM
    render_plist > "$temp_plist"
    /bin/chmod 600 "$temp_plist"
    /bin/mv -f "$temp_plist" "$PLIST_PATH"
    trap - EXIT HUP INT TERM
}

install_runtime() {
    install_runtime_script
    ensure_state_dir
    migrate_legacy_marker
    install_plist
}

disable_supervisor() {
    ensure_state_dir
    /usr/bin/touch "$DISABLED_MARKER"

    if launchctl_available; then
        target=$(launchd_target)
        # The marker is written before unloading the job. If a loop iteration
        # is already in flight, the next check still observes the stop intent.
        "$LAUNCHCTL" disable "$target" >/dev/null 2>&1 || true
        "$LAUNCHCTL" bootout "$target" >/dev/null 2>&1 || true
    fi

    log_message "TG_watermarker supervisor disabled."
}

enable_supervisor() {
    require_launchctl
    install_runtime

    target=$(launchd_target)
    domain=$(launchd_domain)
    if ! "$LAUNCHCTL" enable "$target" >/dev/null 2>&1; then
        /usr/bin/touch "$DISABLED_MARKER"
        die "Unable to enable LaunchAgent: $target"
    fi
    # Re-bootstrap the project-owned job so repeated enable/start calls always
    # use the freshly rendered absolute paths and current checkout.
    "$LAUNCHCTL" bootout "$target" >/dev/null 2>&1 || true
    if ! "$LAUNCHCTL" bootstrap "$domain" "$PLIST_PATH" >/dev/null 2>&1; then
        /usr/bin/touch "$DISABLED_MARKER"
        die "Unable to bootstrap LaunchAgent: $PLIST_PATH"
    fi
    # Keep the marker until bootstrap succeeds. A failed installation or
    # bootstrap therefore cannot turn an intentional stop into an auto-start.
    /bin/rm -f "$DISABLED_MARKER"
    clear_legacy_marker

    log_message "TG_watermarker supervisor enabled: $PLIST_PATH"
}

status_supervisor() {
    if marker_exists; then
        printf '%s\n' "supervisor: disabled (stop marker present)"
    elif legacy_marker_exists; then
        printf '%s\n' "supervisor: disabled (legacy stop marker pending migration)"
    else
        printf '%s\n' "supervisor: enabled (stop marker absent)"
    fi

    if [ -f "$PLIST_PATH" ]; then
        printf '%s\n' "supervisor plist: installed at $PLIST_PATH"
    else
        printf '%s\n' "supervisor plist: not installed ($PLIST_PATH)"
    fi

    if launchctl_available; then
        target=$(launchd_target)
        if "$LAUNCHCTL" print "$target" >/dev/null 2>&1; then
            printf '%s\n' "launchd job: loaded ($target)"
        else
            printf '%s\n' "launchd job: not loaded ($target)"
        fi
    else
        printf '%s\n' "launchd job: unavailable (launchctl not found at $LAUNCHCTL)"
    fi

    if CONTAINER_CLI=$(find_container_cli 2>/dev/null); then
        bot_state=$(container_state "$BOT_CONTAINER")
        api_state=$(container_state "$API_CONTAINER")
        printf 'supervised containers: bot=%s api=%s\n' \
            "${bot_state:-not-found}" "${api_state:-not-found}"
    else
        printf '%s\n' "supervised containers: unavailable (Apple container CLI not found)"
    fi
}

run_supervisor() {
    while :; do
        if marker_exists; then
            :
        elif CONTAINER_CLI=$(find_container_cli 2>/dev/null); then
            reconcile_once || log_message "Supervisor reconciliation failed; will retry."
        else
            log_message "Apple container CLI is unavailable; will retry."
        fi
        /bin/sleep "$POLL_SECONDS"
    done
}

usage() {
    cat <<'EOF'
Usage: scripts/tg-watermarker-supervisor.sh <command>

Commands:
  run       Run the launchd-managed reconciliation loop
  run-once  Reconcile once (used by regression tests and diagnostics)
  enable    Render/install and bootstrap the project LaunchAgent
  disable   Write the intentional-stop marker and unload the LaunchAgent
  status    Show marker, plist, launchd, and project container state
EOF
}

command=${1:-status}
shift 2>/dev/null || true

case "$command" in
    run)
        [ "$#" -eq 0 ] || die "run does not accept extra arguments"
        run_supervisor
        ;;
    run-once)
        [ "$#" -eq 0 ] || die "run-once does not accept extra arguments"
        resolve_container_cli
        reconcile_once
        ;;
    enable)
        [ "$#" -eq 0 ] || die "enable does not accept extra arguments"
        enable_supervisor
        ;;
    disable)
        [ "$#" -eq 0 ] || die "disable does not accept extra arguments"
        disable_supervisor
        ;;
    status)
        [ "$#" -eq 0 ] || die "status does not accept extra arguments"
        status_supervisor
        ;;
    help|-h|--help)
        [ "$#" -eq 0 ] || die "help does not accept extra arguments"
        usage
        ;;
    *)
        usage >&2
        die "Unknown command: $command"
        ;;
esac
