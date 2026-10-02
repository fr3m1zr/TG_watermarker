#!/usr/bin/env sh
set -eu

# Apple container does not implement Docker Compose's depends_on/restart
# semantics. This script reconciles only the resources owned by this project.

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$SCRIPT_DIR"

CONTAINER_CLI=${CONTAINER_CLI:-container}
ENV_FILE=${ENV_FILE:-"$SCRIPT_DIR/.env"}
# This remains the source used by the explicit supervisor control commands.
# The enable path installs a TCC-safe runtime copy in the user's home folder.
SUPERVISOR_SCRIPT="$SCRIPT_DIR/scripts/tg-watermarker-supervisor.sh"

NETWORK_NAME=tg-watermarker-net
DEFAULT_DATA_DIR=.container-data/telegram-bot-api
API_CONTAINER=tg-watermarker-telegram-bot-api
BOT_CONTAINER=tg-watermarker-bot
DEFAULT_IMAGE=tg-watermarker:latest
API_PORT=8081

die() {
    printf '%s\n' "Error: $*" >&2
    exit 1
}

usage() {
    cat <<'EOF'
Usage: ./startup.sh <command>

Commands:
  start, up          Reuse a matching local ARM64 image, then start services
  update             Explicitly pull/rebuild the ARM64 image, then reconcile services
  build              Build the bot image with the local cache (does not require .env)
  stop              Stop only this project's bot and local Telegram API
  status            Show this project's resource and container status
  supervisor <cmd>  Manage the project launchd supervisor (status|enable|disable)
  logs [api|bot]    Show logs; add -f/--follow for one service
  help              Show this help

The default command is start. start does not pull when a matching local image
already exists. It builds only when the image is missing or the Dockerfile
build inputs changed. update is the explicit pull/rebuild operation.
The script never uses Docker or Docker Compose. It never deletes the persistent
data directory or images.
EOF
}

require_command() {
    command -v "$1" >/dev/null 2>&1 || die "Required command not found: $1"
}

require_container_cli() {
    require_command "$CONTAINER_CLI"
    container_cli_path=$(command -v "$CONTAINER_CLI" 2>/dev/null || true)
    case "$container_cli_path" in
        /*)
            ;;
        *)
            die "Apple container CLI did not resolve to an absolute path: $CONTAINER_CLI"
            ;;
    esac
    CONTAINER_CLI=$container_cli_path
    "$CONTAINER_CLI" --version >/dev/null 2>&1 \
        || die "Unable to execute the Apple container CLI: $CONTAINER_CLI"
}

supervisor_control() {
    [ -x "$SUPERVISOR_SCRIPT" ] \
        || die "Project supervisor script is missing or not executable: $SUPERVISOR_SCRIPT"

    TG_WATERMARKER_PROJECT_DIR="$SCRIPT_DIR" \
    TG_WATERMARKER_CONTAINER_CLI="$CONTAINER_CLI" \
        "$SUPERVISOR_SCRIPT" "$@"
}

hash_file() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 "$1" | awk '{ print $1; exit }'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum "$1" | awk '{ print $1; exit }'
    else
        die "A SHA-256 utility is required (shasum or sha256sum)"
    fi
}

hash_stdin() {
    if command -v shasum >/dev/null 2>&1; then
        shasum -a 256 | awk '{ print $1; exit }'
    elif command -v sha256sum >/dev/null 2>&1; then
        sha256sum | awk '{ print $1; exit }'
    else
        die "A SHA-256 utility is required (shasum or sha256sum)"
    fi
}

# These are the files consumed by Dockerfile COPY/RUN instructions. Hashing
# only them keeps README, tests, and local metadata changes from triggering a
# needless image rebuild, while a Dockerfile/context input change is visible.
build_input_manifest() {
    (
        cd "$SCRIPT_DIR"
        for relative_path in Dockerfile requirements.txt bot.py; do
            [ -f "$relative_path" ] \
                || die "Image build input is missing: $relative_path"
            printf 'file\t%s\t%s\n' \
                "$relative_path" \
                "$(hash_file "$relative_path")"
        done

        if [ -f .dockerignore ]; then
            printf 'file\t.dockerignore\t%s\n' "$(hash_file .dockerignore)"
        fi

        if [ -d assets ]; then
            find assets -type f -print | LC_ALL=C sort \
                | while IFS= read -r relative_path; do
                    printf 'file\t%s\t%s\n' \
                        "$relative_path" \
                        "$(hash_file "$relative_path")"
                done
        fi
    )
}

build_input_digest() {
    manifest=$(build_input_manifest) \
        || die "Unable to calculate the image build-input digest"
    printf '%s\n' "$manifest" | hash_stdin
}

# Read one simple KEY=value entry without sourcing .env. This prevents an
# arbitrary value in the secrets file from being executed by the host shell.
env_value() {
    key=$1
    if [ ! -f "$ENV_FILE" ]; then
        return 0
    fi

    awk -v key="$key" '
        /^[[:space:]]*#/ { next }
        {
            line = $0
            sub(/\r$/, "", line)
            sub(/^[[:space:]]*/, "", line)
            prefix = key "="
            if (index(line, prefix) == 1) {
                value = substr(line, length(prefix) + 1)
                sub(/[[:space:]]+$/, "", value)
                print value
                exit
            }
        }
    ' "$ENV_FILE"
}

configured_image() {
    image=$(env_value WATERMARKER_IMAGE || true)
    if [ -n "$image" ]; then
        printf '%s\n' "$image"
    else
        printf '%s\n' "$DEFAULT_IMAGE"
    fi
}

env_or_default() {
    key=$1
    fallback=$2
    value=$(env_value "$key" || true)
    if [ -n "$value" ]; then
        printf '%s\n' "$value"
    else
        printf '%s\n' "$fallback"
    fi
}

require_env_values() {
    [ -f "$ENV_FILE" ] \
        || die "Missing $ENV_FILE. Copy .env.example to .env and fill in the required values."

    for key in TELEGRAM_API_ID TELEGRAM_API_HASH TELEGRAM_BOT_TOKEN; do
        value=$(env_value "$key" || true)
        case "$value" in
            ""|replace-with-*)
                die "$key is missing or still uses the example placeholder in $ENV_FILE"
                ;;
        esac
    done
}

ensure_container_system() {
    if ! "$CONTAINER_CLI" system status >/dev/null 2>&1; then
        printf '%s\n' "Starting Apple container services..."
        "$CONTAINER_CLI" system start >/dev/null
    fi
}

ensure_builder() {
    builder_state=$("$CONTAINER_CLI" builder status --format json 2>/dev/null \
        | awk -F'"' '/"state"[[:space:]]*:/ { print $4; exit }' || true)
    case "$builder_state" in
        running|starting)
            return 0
            ;;
    esac

    printf '%s\n' "Starting Apple container builder..."
    "$CONTAINER_CLI" builder start >/dev/null
}

network_exists() {
    "$CONTAINER_CLI" network list --quiet 2>/dev/null \
        | grep -Fxq "$NETWORK_NAME"
}

configured_data_dir() {
    data_dir=$(env_value TELEGRAM_API_DATA_DIR || true)
    if [ -z "$data_dir" ]; then
        data_dir=$DEFAULT_DATA_DIR
    fi

    case "$data_dir" in
        /*)
            printf '%s\n' "$data_dir"
            ;;
        *)
            printf '%s\n' "$SCRIPT_DIR/$data_dir"
            ;;
    esac
}

ensure_project_resources() {
    if ! network_exists; then
        printf '%s\n' "Creating network: $NETWORK_NAME"
        "$CONTAINER_CLI" network create "$NETWORK_NAME" >/dev/null
    fi

    data_dir=$1
    mkdir -p "$data_dir"
    # Apple bind mounts are presented as root:root inside the Linux guest and
    # do not allow the Bot API image entrypoint to chown them. The API binary
    # still drops to uid/gid 101, so keep the project data directory writable
    # by that user without touching any path outside this project.
    chmod 0777 "$data_dir"
}

container_exists() {
    "$CONTAINER_CLI" list --all --quiet 2>/dev/null \
        | grep -Fxq "$1"
}

container_state() {
    "$CONTAINER_CLI" inspect "$1" 2>/dev/null \
        | awk -F'"' '/"state"[[:space:]]*:/ { print $4; exit }'
}

container_ip() {
    "$CONTAINER_CLI" inspect "$1" 2>/dev/null \
        | awk -F'"' '/"ipv4Address"[[:space:]]*:/ {
            split($4, address, "/")
            print address[1]
            exit
        }'
}

image_exists() {
    "$CONTAINER_CLI" image inspect "$1" >/dev/null 2>&1
}

image_label() {
    image=$1
    label=$2
    "$CONTAINER_CLI" image inspect "$image" 2>/dev/null \
        | awk -F'"' -v label="$label" '$2 == label { print $4; exit }'
}

image_matches_build_inputs() {
    image=$1
    expected_digest=$2
    actual_digest=$(image_label "$image" com.tg-watermarker.source-digest || true)
    [ -n "$actual_digest" ] && [ "$actual_digest" = "$expected_digest" ]
}

project_services_running() {
    [ "$(container_state "$API_CONTAINER" || true)" = running ] \
        && [ "$(container_state "$BOT_CONTAINER" || true)" = running ]
}

stop_one() {
    name=$1
    if ! container_exists "$name"; then
        return 0
    fi

    state=$(container_state "$name" || true)
    case "$state" in
        running|starting)
            printf '%s\n' "Stopping container: $name"
            "$CONTAINER_CLI" stop --time 15 "$name" >/dev/null
            ;;
    esac
}

delete_one() {
    name=$1
    if container_exists "$name"; then
        printf '%s\n' "Removing stopped container: $name"
        "$CONTAINER_CLI" delete "$name" >/dev/null
    fi
}

replace_project_containers() {
    # Stop bot first so it cannot keep reading from the API while the API is
    # being replaced. The persistent host data directory remains intact.
    stop_one "$BOT_CONTAINER"
    stop_one "$API_CONTAINER"
    delete_one "$BOT_CONTAINER"
    delete_one "$API_CONTAINER"
}

build_image() {
    image=$1
    source_digest=$2
    pull=$3
    if [ "$pull" = true ]; then
        printf '%s\n' "Building image with --pull: $image"
        "$CONTAINER_CLI" build \
            --pull \
            --no-cache \
            --label "com.tg-watermarker.source-digest=$source_digest" \
            --platform linux/arm64 \
            --file "$SCRIPT_DIR/Dockerfile" \
            --tag "$image" \
            "$SCRIPT_DIR"
    else
        printf '%s\n' "Building image from the local cache: $image"
        "$CONTAINER_CLI" build \
            --label "com.tg-watermarker.source-digest=$source_digest" \
            --platform linux/arm64 \
            --file "$SCRIPT_DIR/Dockerfile" \
            --tag "$image" \
            "$SCRIPT_DIR"
    fi
}

create_api_container() {
    api_id=$1
    api_hash=$2
    api_verbosity=$3
    data_dir=$4
    printf '%s\n' "Creating container: $API_CONTAINER"
    TELEGRAM_API_ID="$api_id" \
    TELEGRAM_API_HASH="$api_hash" \
    "$CONTAINER_CLI" create \
        --name "$API_CONTAINER" \
        --label com.tg-watermarker.service=telegram-bot-api \
        --network "$NETWORK_NAME" \
        --init \
        --env TELEGRAM_API_ID \
        --env TELEGRAM_API_HASH \
        --env "TELEGRAM_LOCAL=1" \
        --env "TELEGRAM_VERBOSITY=$api_verbosity" \
        --volume "$data_dir:/var/lib/telegram-bot-api" \
        --entrypoint /usr/local/bin/telegram-bot-api \
        aiogram/telegram-bot-api:latest \
        --dir=/var/lib/telegram-bot-api \
        --temp-dir=/tmp/telegram-bot-api \
        --http-port="$API_PORT" \
        --local \
        --verbosity="$api_verbosity" \
        --username=telegram-bot-api \
        --groupname=telegram-bot-api \
        >/dev/null
}

wait_for_api() {
    attempt=1
    # The local API binary can take more than 30 seconds to initialize its
    # persistent data on the first run before port 8081 accepts connections.
    while [ "$attempt" -le 120 ]; do
        state=$(container_state "$API_CONTAINER" || true)
        if [ "$state" = running ]; then
            ip=$(container_ip "$API_CONTAINER" || true)
            if [ -n "$ip" ]; then
                # macOS includes curl. If it is unavailable, the running
                # state and assigned IP are still enough to continue; the bot
                # client will retry its first request if the port is not ready.
                if ! command -v curl >/dev/null 2>&1; then
                    printf '%s\n' "$ip"
                    return 0
                elif curl --silent --show-error --connect-timeout 1 --max-time 1 \
                    "http://$ip:$API_PORT/" >/dev/null 2>&1; then
                    printf '%s\n' "$ip"
                    return 0
                fi
            fi
        elif [ "$state" = exited ] || [ "$state" = stopped ] || [ "$state" = failed ]; then
            die "The local Telegram API container stopped during startup. Run './startup.sh logs api'."
        fi

        sleep 1
        attempt=$((attempt + 1))
    done

    die "Timed out waiting for $API_CONTAINER to expose port $API_PORT"
}

create_bot_container() {
    bot_token=$1
    image=$2
    api_ip=$3
    data_dir=$4
    signature_dir=$5
    signature_file=$6
    jpeg_quality=$7
    max_file_size=$8
    max_image_pixels=$9
    bot_memory_limit=${10}
    transfer_timeout=${11}
    log_level=${12}
    bot_api_url="http://$api_ip:$API_PORT/bot"

    printf '%s\n' "Creating container: $BOT_CONTAINER"
    TELEGRAM_BOT_TOKEN="$bot_token" \
    "$CONTAINER_CLI" create \
        --name "$BOT_CONTAINER" \
        --label com.tg-watermarker.service=bot \
        --network "$NETWORK_NAME" \
        --init \
        --memory "$bot_memory_limit" \
        --env TELEGRAM_BOT_TOKEN \
        --env "TELEGRAM_BOT_API_URL=$bot_api_url" \
        --env TELEGRAM_LOCAL_MODE=true \
        --env "SIGNATURE_IMAGE_DIR=$signature_dir" \
        --env "SIGNATURE_IMAGE_FILE=$signature_file" \
        --env "JPEG_QUALITY=$jpeg_quality" \
        --env "MAX_FILE_SIZE_MB=$max_file_size" \
        --env "MAX_IMAGE_PIXELS=$max_image_pixels" \
        --env "FILE_TRANSFER_TIMEOUT_SECONDS=$transfer_timeout" \
        --env "LOG_LEVEL=$log_level" \
        --volume "$data_dir:/var/lib/telegram-bot-api:ro" \
        "$image" \
        >/dev/null
}

start_services() {
    mode=$1
    require_env_values
    ensure_container_system

    image=$(configured_image)
    source_digest=$(build_input_digest)

    if [ "$mode" = start ] \
        && image_exists "$image" \
        && image_matches_build_inputs "$image" "$source_digest" \
        && project_services_running; then
        supervisor_control enable
        printf '%s\n' "TG_watermarker services are already running; no restart needed."
        return 0
    fi

    # Pause the launchd loop before any rebuild/reconciliation. The marker is
    # intentionally retained if a later step fails, preventing an old bot
    # from being resurrected while the project is only partially started.
    supervisor_control disable

    image_was_built=false
    if [ "$mode" = update ]; then
        ensure_builder
        build_image "$image" "$source_digest" true
        image_was_built=true
    elif image_exists "$image" \
        && image_matches_build_inputs "$image" "$source_digest"; then
        printf '%s\n' "Reusing local image: $image (build inputs unchanged)"
    else
        if image_exists "$image"; then
            printf '%s\n' "Local image metadata is stale; rebuilding without --pull: $image"
        else
            printf '%s\n' "Local image is missing; building without --pull: $image"
        fi
        ensure_builder
        build_image "$image" "$source_digest" false
        image_was_built=true
    fi

    api_id=$(env_value TELEGRAM_API_ID || true)
    api_hash=$(env_value TELEGRAM_API_HASH || true)
    bot_token=$(env_value TELEGRAM_BOT_TOKEN || true)
    data_dir=$(configured_data_dir)
    api_verbosity=$(env_or_default TELEGRAM_API_VERBOSITY 1)
    signature_dir=$(env_or_default SIGNATURE_IMAGE_DIR /app/assets)
    signature_file=$(env_or_default SIGNATURE_IMAGE_FILE signature.png)
    jpeg_quality=$(env_or_default JPEG_QUALITY 95)
    max_file_size=$(env_or_default MAX_FILE_SIZE_MB 100)
    max_image_pixels=$(env_or_default MAX_IMAGE_PIXELS 100000000)
    bot_memory_limit=$(env_or_default BOT_MEMORY_LIMIT 2G)
    transfer_timeout=$(env_or_default FILE_TRANSFER_TIMEOUT_SECONDS 300)
    log_level=$(env_or_default LOG_LEVEL INFO)

    # Build happens before stopping project containers, so a failed build does
    # not create avoidable downtime. Only explicit `update` pulls the base.
    ensure_project_resources "$data_dir"
    replace_project_containers

    create_api_container "$api_id" "$api_hash" "$api_verbosity" "$data_dir"
    printf '%s\n' "Starting container: $API_CONTAINER"
    "$CONTAINER_CLI" start "$API_CONTAINER" >/dev/null
    api_ip=$(wait_for_api)

    # Custom Apple networks do not currently provide Compose-style bare-name
    # discovery. Inject the API's actual network IP into the bot instead.
    create_bot_container \
        "$bot_token" \
        "$image" \
        "$api_ip" \
        "$data_dir" \
        "$signature_dir" \
        "$signature_file" \
        "$jpeg_quality" \
        "$max_file_size" \
        "$max_image_pixels" \
        "$bot_memory_limit" \
        "$transfer_timeout" \
        "$log_level"
    printf '%s\n' "Starting container: $BOT_CONTAINER"
    "$CONTAINER_CLI" start "$BOT_CONTAINER" >/dev/null

    supervisor_control enable
    printf '%s\n' "Started TG_watermarker services. Use './startup.sh status' or './startup.sh logs bot -f'."
}

stop_services() {
    # Write the marker before stopping either container so the launchd loop
    # cannot turn an intentional stop into an automatic restart.
    supervisor_control disable
    require_container_cli
    stop_one "$BOT_CONTAINER"
    stop_one "$API_CONTAINER"
    printf '%s\n' "TG_watermarker containers stopped; network, data directory, and images were kept."
}

show_status() {
    require_container_cli
    data_dir=$(configured_data_dir)
    printf '%s\n' "TG_watermarker resources"
    if network_exists; then
        printf '%s\n' "  network: $NETWORK_NAME (present)"
    else
        printf '%s\n' "  network: $NETWORK_NAME (absent)"
    fi
    if [ -d "$data_dir" ]; then
        printf '  data dir: %s (present)\n' "$data_dir"
    else
        printf '  data dir: %s (absent)\n' "$data_dir"
    fi

    for name in "$API_CONTAINER" "$BOT_CONTAINER"; do
        if container_exists "$name"; then
            state=$(container_state "$name" || true)
            ip=$(container_ip "$name" || true)
            printf '  container: %-32s state=%-9s ip=%s\n' "$name" "${state:-unknown}" "${ip:--}"
        else
            printf '  container: %-32s state=not-created\n' "$name"
        fi
    done

    printf '%s\n' "  supervisor:"
    supervisor_control status | sed 's/^/    /'
}

show_logs() {
    target=all
    follow=false
    for argument in "$@"; do
        case "$argument" in
            api|bot|all)
                target=$argument
                ;;
            -f|--follow)
                follow=true
                ;;
            *)
                die "Unknown logs option: $argument"
                ;;
        esac
    done

    if [ "$follow" = true ] && [ "$target" = all ]; then
        die "Use './startup.sh logs api -f' or './startup.sh logs bot -f' when following logs"
    fi

    case "$target" in
        api)
            container_exists "$API_CONTAINER" \
                || die "$API_CONTAINER has not been created"
            if [ "$follow" = true ]; then
                "$CONTAINER_CLI" logs --follow "$API_CONTAINER"
            else
                "$CONTAINER_CLI" logs "$API_CONTAINER"
            fi
            ;;
        bot)
            container_exists "$BOT_CONTAINER" \
                || die "$BOT_CONTAINER has not been created"
            if [ "$follow" = true ]; then
                "$CONTAINER_CLI" logs --follow "$BOT_CONTAINER"
            else
                "$CONTAINER_CLI" logs "$BOT_CONTAINER"
            fi
            ;;
        all)
            found=false
            if container_exists "$API_CONTAINER"; then
                found=true
                printf '%s\n' "===== $API_CONTAINER ====="
                "$CONTAINER_CLI" logs "$API_CONTAINER"
            fi
            if container_exists "$BOT_CONTAINER"; then
                found=true
                printf '%s\n' "===== $BOT_CONTAINER ====="
                "$CONTAINER_CLI" logs "$BOT_CONTAINER"
            fi
            [ "$found" = true ] \
                || die "TG_watermarker containers have not been created"
            ;;
    esac
}

command=${1:-start}
shift 2>/dev/null || true

case "$command" in
    start|up)
        [ "$#" -eq 0 ] || die "$command does not accept extra arguments"
        require_container_cli
        start_services start
        ;;
    update)
        [ "$#" -eq 0 ] || die "update does not accept extra arguments"
        require_container_cli
        start_services update
        ;;
    build)
        [ "$#" -eq 0 ] || die "build does not accept extra arguments"
        require_container_cli
        ensure_container_system
        ensure_builder
        build_image "$(configured_image)" "$(build_input_digest)" false
        ;;
    stop)
        [ "$#" -eq 0 ] || die "stop does not accept extra arguments"
        stop_services
        ;;
    status)
        [ "$#" -eq 0 ] || die "status does not accept extra arguments"
        show_status
        ;;
    supervisor)
        [ "$#" -ge 1 ] || die "supervisor requires status, enable, or disable"
        supervisor_control "$@"
        ;;
    logs)
        require_container_cli
        show_logs "$@"
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
