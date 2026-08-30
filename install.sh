#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly DEFAULT_REPOSITORY="https://github.com/FengYuchen1314/open-node.git"
readonly DEFAULT_INSTALL_DIR="/opt/open-node"
readonly DEFAULT_CONFIG_DIR="/etc/open-node"
readonly DEFAULT_BACKUP_DIR="/var/backups/open-node"
readonly MANIFEST_VERSION="1"
readonly RUNTIME_DATABASE_URL="sqlite:////var/lib/open-node/open-node.db"
readonly RUNTIME_CONTAINER_PORT="8080"
readonly RUNTIME_UID_GID="10001:10001"
readonly HEALTH_STABLE_OBSERVATIONS="3"

ACTION="${1:-install}"
REPOSITORY="${OPEN_NODE_REPOSITORY:-$DEFAULT_REPOSITORY}"
REF="${OPEN_NODE_REF:-main}"
INSTALL_DIR="${OPEN_NODE_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
CONFIG_DIR="${OPEN_NODE_CONFIG_DIR:-$DEFAULT_CONFIG_DIR}"
BACKUP_DIR="${OPEN_NODE_BACKUP_DIR:-$DEFAULT_BACKUP_DIR}"
PROJECT_NAME="${OPEN_NODE_PROJECT_NAME:-open-node}"
IMAGE_REPOSITORY="${OPEN_NODE_IMAGE_REPOSITORY:-open-node}"
ENV_FILE="$CONFIG_DIR/open-node.env"
MANIFEST_FILE="$CONFIG_DIR/installer.manifest"
RECOVERY_FILE="$CONFIG_DIR/installer.recovery"
DATA_VOLUME="${PROJECT_NAME}_data"
AUTO_INSTALL="${OPEN_NODE_AUTO_INSTALL_DEPENDENCIES:-1}"
BUILD_PULL="${OPEN_NODE_BUILD_PULL:-1}"
CREATE_ADMIN="${OPEN_NODE_CREATE_ADMIN:-auto}"
ADMIN_USERNAME="${OPEN_NODE_ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD_FILE="${OPEN_NODE_ADMIN_PASSWORD_FILE:-}"
COMPOSE=()
LOCK_FD=""
GLOBAL_LOCK_FD=""
GLOBAL_LOCK_FILE=""
DOCKER_DAEMON_ID=""
CANDIDATE_SOURCE=""
CANDIDATE_REVISION=""
CANDIDATE_IMAGE_ID=""
CANDIDATE_UNCHANGED=0
BACKUP_PATH=""
TXN_PHASE="idle"
TXN_KIND=""
TXN_CANDIDATE_ENV=""
TXN_IMAGE_TAG=""
TXN_BACKUP="pending"
TXN_TEMP_BACKUP=""
TXN_BACKUP_CONTAINER=""
TXN_ROLLBACK_IMAGE=""
TXN_VERIFY_VOLUME=""
TXN_CANDIDATE_ACTIVATED=0

log() {
  printf '[open-node] %s\n' "$*"
}

warn() {
  printf '[open-node] WARNING: %s\n' "$*" >&2
}

die() {
  printf '[open-node] ERROR: %s\n' "$*" >&2
  exit 1
}

require_root() {
  [[ "${EUID:-$(id -u)}" -eq 0 ]] || die "run this installer as root"
}

validate_plain_value() {
  local label="$1" value="$2"
  [[ -n "$value" ]] || die "$label must not be empty"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "$label contains a newline"
  [[ "$value" != -* ]] || die "$label must not start with '-'"
}

validate_absolute_path() {
  local label="$1" value="$2" canonical current owner mode
  validate_plain_value "$label" "$value"
  [[ "$value" == /* ]] || die "$label must be an absolute path"
  [[ "$value" != *[[:space:]]* ]] || die "$label must not contain whitespace"
  canonical="$(realpath -m -- "$value")" || die "could not normalize $label"
  [[ "$canonical" == "$value" ]] \
    || die "$label must already be canonical (no trailing slash, '.', '..', or duplicate slash)"
  case "$canonical" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var|/var/backups)
      die "$label is too broad"
      ;;
  esac
  current="$canonical"
  while [[ "$current" != "/" ]]; do
    if [[ -e "$current" || -L "$current" ]]; then
      [[ ! -L "$current" ]] || die "$label must not contain symlink path components"
      if [[ -d "$current" ]]; then
        owner="$(stat -c '%u' -- "$current")" || die "could not inspect $label"
        mode="$(stat -c '%a' -- "$current")" || die "could not inspect $label"
        [[ "$owner" == "0" ]] || die "$label has a non-root-owned component: $current"
        (( (8#$mode & 022) == 0 )) \
          || die "$label has a group/world-writable component: $current"
      fi
    fi
    current="$(dirname -- "$current")"
  done
}

paths_overlap() {
  local first="$1" second="$2"
  [[ "$first" == "$second" || "$first" == "$second/"* || "$second" == "$first/"* ]]
}

validate_path_separation() {
  paths_overlap "$INSTALL_DIR" "$CONFIG_DIR" \
    && die "install and configuration directories must not overlap"
  paths_overlap "$INSTALL_DIR" "$BACKUP_DIR" \
    && die "install and backup directories must not overlap"
  paths_overlap "$CONFIG_DIR" "$BACKUP_DIR" \
    && die "configuration and backup directories must not overlap"
  return 0
}

validate_safe_file() {
  local label="$1" path="$2" private="${3:-0}" owner mode
  [[ -f "$path" && ! -L "$path" ]] || die "$label must be a regular non-symlink file: $path"
  owner="$(stat -c '%u' -- "$path")" || die "could not inspect $label"
  mode="$(stat -c '%a' -- "$path")" || die "could not inspect $label"
  [[ "$owner" == "0" ]] || die "$label must be root-owned: $path"
  (( (8#$mode & 022) == 0 )) || die "$label must not be group/world writable: $path"
  if [[ "$private" == "1" ]]; then
    (( (8#$mode & 077) == 0 )) || die "$label must not grant group/other access: $path"
  fi
}

validate_safe_directory() {
  local label="$1" directory="$2" private="${3:-0}" owner mode
  [[ -d "$directory" && ! -L "$directory" ]] || die "$label must be a real directory: $directory"
  owner="$(stat -c '%u' -- "$directory")" || die "could not inspect $label"
  mode="$(stat -c '%a' -- "$directory")" || die "could not inspect $label"
  [[ "$owner" == "0" ]] || die "$label must be root-owned: $directory"
  (( (8#$mode & 022) == 0 )) || die "$label must not be group/world writable: $directory"
  if [[ "$private" == "1" ]]; then
    (( (8#$mode & 077) == 0 )) || die "$label must not grant group/other access: $directory"
  fi
}

ensure_private_directory() {
  local label="$1" directory="$2"
  if [[ -e "$directory" || -L "$directory" ]]; then
    validate_safe_directory "$label" "$directory" 1
    return
  fi
  mkdir -p -- "$directory" || die "could not create $label"
  chmod 0700 -- "$directory" || die "could not secure $label"
  validate_safe_directory "$label" "$directory" 1
}

read_key() {
  local file="$1" key="$2"
  awk -v key="$key" '
    index($0, key "=") == 1 { value = substr($0, length(key) + 2); found = 1 }
    END { if (found) print value; else exit 1 }
  ' "$file"
}

read_env_value() {
  read_key "$ENV_FILE" "$1"
}

read_manifest_value() {
  read_key "$MANIFEST_FILE" "$1"
}

sync_file_and_parent() {
  local path="$1"
  sync -f -- "$path" || die "could not durably sync $path"
  sync -f -- "$(dirname -- "$path")" || die "could not durably sync $(dirname -- "$path")"
}

docker_daemon_identity() {
  docker info --format '{{.ID}}' 2>/dev/null
}

daemon_identity_is_current() {
  local current
  [[ -n "$DOCKER_DAEMON_ID" ]] || return 1
  current="$(docker_daemon_identity)" || return 1
  [[ "$current" == "$DOCKER_DAEMON_ID" ]]
}

volume_fingerprint() {
  docker volume inspect "$DATA_VOLUME" 2>/dev/null \
    | jq -cS '.[0] | {
        Name,
        Driver,
        Scope,
        CreatedAt,
        Options: (.Options // {}),
        Labels: (.Labels // {})
      }' \
    | sha256sum \
    | awk '{print $1}'
}

set_file_value() {
  local file="$1" key="$2" value="$3" temporary directory
  [[ "$key" =~ ^[A-Z0-9_]+$ ]] || die "invalid environment key"
  [[ "$value" != *$'\n'* && "$value" != *$'\r'* ]] || die "invalid value for $key"
  directory="$(dirname -- "$file")"
  temporary="$(mktemp "$directory/.open-node-value.XXXXXX")" \
    || die "could not create a private temporary file"
  if ! awk -v key="$key" -v value="$value" '
    BEGIN { found = 0 }
    index($0, key "=") == 1 { if (!found) print key "=" value; found = 1; next }
    { print }
    END { if (!found) print key "=" value }
  ' "$file" > "$temporary"; then
    rm -f -- "$temporary"
    die "could not update $key"
  fi
  chmod 0600 -- "$temporary" || {
    rm -f -- "$temporary"
    die "could not secure the temporary environment file"
  }
  mv -f -- "$temporary" "$file" || {
    rm -f -- "$temporary"
    die "could not commit the environment update"
  }
  sync_file_and_parent "$file"
}

adopt_manifest_value() {
  local variable_name="$1" environment_name="$2" manifest_key="$3" saved current
  saved="$(read_key "$MANIFEST_FILE" "$manifest_key")" \
    || die "installer manifest is missing $manifest_key"
  current="${!variable_name}"
  if [[ -v "$environment_name" ]]; then
    [[ "$current" == "$saved" ]] \
      || die "$environment_name conflicts with the installed manifest"
  else
    printf -v "$variable_name" '%s' "$saved"
  fi
}

load_manifest_defaults() {
  command -v realpath >/dev/null 2>&1 || die "GNU realpath is required"
  validate_absolute_path "OPEN_NODE_CONFIG_DIR" "$CONFIG_DIR"
  [[ -e "$MANIFEST_FILE" || -L "$MANIFEST_FILE" ]] || return 0
  validate_safe_file "installer manifest" "$MANIFEST_FILE" 1
  adopt_manifest_value REPOSITORY OPEN_NODE_REPOSITORY REPOSITORY
  adopt_manifest_value REF OPEN_NODE_REF REF
  adopt_manifest_value INSTALL_DIR OPEN_NODE_INSTALL_DIR INSTALL_DIR
  adopt_manifest_value BACKUP_DIR OPEN_NODE_BACKUP_DIR BACKUP_DIR
  adopt_manifest_value PROJECT_NAME OPEN_NODE_PROJECT_NAME PROJECT_NAME
  adopt_manifest_value IMAGE_REPOSITORY OPEN_NODE_IMAGE_REPOSITORY IMAGE_REPOSITORY
  DATA_VOLUME="${PROJECT_NAME}_data"
}

validate_inputs() {
  case "$ACTION" in
    install|update|status|uninstall|create-admin) ;;
    *) die "usage: install.sh [install|update|status|uninstall|create-admin]" ;;
  esac
  command -v realpath >/dev/null 2>&1 || die "GNU realpath is required"
  validate_plain_value "OPEN_NODE_REPOSITORY" "$REPOSITORY"
  validate_plain_value "OPEN_NODE_REF" "$REF"
  validate_plain_value "OPEN_NODE_PROJECT_NAME" "$PROJECT_NAME"
  validate_plain_value "OPEN_NODE_IMAGE_REPOSITORY" "$IMAGE_REPOSITORY"
  validate_absolute_path "OPEN_NODE_INSTALL_DIR" "$INSTALL_DIR"
  validate_absolute_path "OPEN_NODE_CONFIG_DIR" "$CONFIG_DIR"
  validate_absolute_path "OPEN_NODE_BACKUP_DIR" "$BACKUP_DIR"
  validate_path_separation
  [[ "$REPOSITORY" != *[[:space:]]* ]] || die "OPEN_NODE_REPOSITORY must not contain whitespace"
  [[ "$REF" =~ ^[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$ && "$REF" != *".."* ]] \
    || die "OPEN_NODE_REF must be a simple branch or tag name"
  [[ "$PROJECT_NAME" =~ ^[a-z0-9][a-z0-9_-]{0,62}$ ]] \
    || die "OPEN_NODE_PROJECT_NAME must match [a-z0-9][a-z0-9_-]{0,62}"
  [[ "$IMAGE_REPOSITORY" =~ ^[a-z0-9][a-z0-9._/-]{0,200}$ ]] \
    || die "OPEN_NODE_IMAGE_REPOSITORY must be a lowercase Docker repository without a tag"
  [[ "$ADMIN_USERNAME" =~ ^[^[:cntrl:]]{1,64}$ ]] || die "invalid administrator username"
  [[ "$AUTO_INSTALL" == "0" || "$AUTO_INSTALL" == "1" ]] \
    || die "OPEN_NODE_AUTO_INSTALL_DEPENDENCIES must be 0 or 1"
  [[ "$BUILD_PULL" == "0" || "$BUILD_PULL" == "1" ]] \
    || die "OPEN_NODE_BUILD_PULL must be 0 or 1"
  [[ "$CREATE_ADMIN" == "0" || "$CREATE_ADMIN" == "1" || "$CREATE_ADMIN" == "auto" ]] \
    || die "OPEN_NODE_CREATE_ADMIN must be 0, 1, or auto"
}

install_dependencies() {
  [[ "$AUTO_INSTALL" == "1" ]] \
    || die "required dependencies are missing and automatic installation is disabled"
  command -v apt-get >/dev/null 2>&1 \
    || die "automatic dependency installation supports Debian/Ubuntu apt hosts only"
  log "installing required Debian/Ubuntu packages"
  apt-get update
  DEBIAN_FRONTEND=noninteractive apt-get install -y \
    ca-certificates coreutils curl findutils gawk git grep jq sed tar util-linux
  if ! command -v docker >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker.io
  fi
  if ! docker compose version >/dev/null 2>&1; then
    DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-v2 \
      || DEBIAN_FRONTEND=noninteractive apt-get install -y docker-compose-plugin \
      || die "Docker Compose v2 could not be installed"
  fi
}

ensure_dependencies() {
  local missing=0 compose_version compose_major command_name
  for command_name in awk curl date dirname docker find flock git grep install jq mktemp \
    mv realpath sed sha256sum stat sync tar; do
    command -v "$command_name" >/dev/null 2>&1 || missing=1
  done
  if [[ "$missing" -eq 1 ]] || ! docker compose version >/dev/null 2>&1; then
    [[ "$ACTION" == "install" || "$ACTION" == "update" ]] \
      || die "Git, curl, Docker, flock, and Docker Compose v2 are required"
    install_dependencies
  fi
  if ! docker info >/dev/null 2>&1; then
    if [[ "$ACTION" == "install" || "$ACTION" == "update" ]]; then
      command -v systemctl >/dev/null 2>&1 || die "Docker daemon is not available"
      systemctl enable --now docker >/dev/null || die "could not start Docker"
    else
      die "Docker daemon is not available (status/uninstall do not start it)"
    fi
  fi
  docker info >/dev/null 2>&1 || die "Docker daemon is not available"
  docker compose version >/dev/null 2>&1 || die "Docker Compose v2 is required"
  compose_version="$(docker compose version --short 2>/dev/null | sed 's/^v//')"
  compose_major="${compose_version%%.*}"
  [[ "$compose_major" =~ ^[0-9]+$ && "$compose_major" -ge 2 ]] \
    || die "Docker Compose v2 is required"
  COMPOSE=(docker compose)
}

compose_with() {
  local source_dir="$1" environment_file="$2"
  shift 2
  env \
    -u OPEN_NODE_IMAGE_REPOSITORY \
    -u OPEN_NODE_IMAGE_TAG \
    -u OPEN_NODE_REVISION \
    -u OPEN_NODE_BIND_ADDRESS \
    -u OPEN_NODE_HTTP_PORT \
    -u OPEN_NODE_SESSION_COOKIE_SECURE \
    -u OPEN_NODE_SHORT_LINKS_ENABLED \
    -u OPEN_NODE_TRUSTED_PROXIES \
    -u OPEN_NODE_AGENT_IDENTITY_FILE \
    -u OPEN_NODE_SUBSCRIBER_TOTP_KEY \
    "${COMPOSE[@]}" --env-file "$environment_file" --project-name "$PROJECT_NAME" \
      --project-directory "$source_dir/deploy" \
      --file "$source_dir/deploy/compose.yaml" "$@"
}

require_environment_file() {
  validate_safe_file "environment file" "$ENV_FILE" 1
}

require_manifest() {
  validate_safe_file "installer manifest" "$MANIFEST_FILE" 1
  [[ "$(read_manifest_value MANIFEST_VERSION || true)" == "$MANIFEST_VERSION" ]] \
    || die "unsupported or damaged installer manifest"
  [[ "$(read_manifest_value REPOSITORY || true)" == "$REPOSITORY" ]] \
    || die "OPEN_NODE_REPOSITORY conflicts with the installed manifest"
  [[ "$(read_manifest_value REF || true)" == "$REF" ]] \
    || die "OPEN_NODE_REF conflicts with the installed manifest"
  [[ "$(read_manifest_value INSTALL_DIR || true)" == "$INSTALL_DIR" ]] \
    || die "OPEN_NODE_INSTALL_DIR conflicts with the installed manifest"
  [[ "$(read_manifest_value CONFIG_DIR || true)" == "$CONFIG_DIR" ]] \
    || die "OPEN_NODE_CONFIG_DIR conflicts with the installed manifest"
  [[ "$(read_manifest_value BACKUP_DIR || true)" == "$BACKUP_DIR" ]] \
    || die "OPEN_NODE_BACKUP_DIR conflicts with the installed manifest"
  [[ "$(read_manifest_value PROJECT_NAME || true)" == "$PROJECT_NAME" ]] \
    || die "OPEN_NODE_PROJECT_NAME conflicts with the installed manifest"
  [[ "$(read_manifest_value IMAGE_REPOSITORY || true)" == "$IMAGE_REPOSITORY" ]] \
    || die "OPEN_NODE_IMAGE_REPOSITORY conflicts with the installed manifest"
  [[ "$(read_manifest_value DATA_VOLUME || true)" == "$DATA_VOLUME" ]] \
    || die "managed data volume identity conflicts with the installed manifest"
  [[ -n "$DOCKER_DAEMON_ID" \
    && "$(read_manifest_value DOCKER_DAEMON_ID || true)" == "$DOCKER_DAEMON_ID" ]] \
    || die "the installed manifest belongs to a different Docker daemon"
}

verify_active_identity() {
  local allow_absent="${1:-0}"
  local revision image_tag image_id image_reference referenced_image_id image_revision
  local environment_repository environment_tag environment_revision container_id
  revision="$(read_manifest_value DEPLOYED_REVISION)"
  image_tag="$(read_manifest_value DEPLOYED_IMAGE_TAG)"
  image_id="$(read_manifest_value DEPLOYED_IMAGE_ID)"
  environment_repository="$(read_env_value OPEN_NODE_IMAGE_REPOSITORY || true)"
  environment_tag="$(read_env_value OPEN_NODE_IMAGE_TAG || true)"
  environment_revision="$(read_env_value OPEN_NODE_REVISION || true)"
  [[ "$environment_repository" == "$IMAGE_REPOSITORY" ]] \
    || die "active environment image repository does not match the installer manifest"
  [[ "$environment_tag" == "$image_tag" ]] \
    || die "active environment image tag does not match the installer manifest"
  [[ "$environment_revision" == "$revision" ]] \
    || die "active environment revision does not match the installer manifest"
  image_reference="$IMAGE_REPOSITORY:$image_tag"
  referenced_image_id="$(docker image inspect --format '{{.Id}}' "$image_reference" 2>/dev/null || true)"
  [[ -n "$referenced_image_id" && "$referenced_image_id" == "$image_id" ]] \
    || die "deployed image tag no longer resolves to the recorded image ID"
  image_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$image_id" 2>/dev/null || true)"
  [[ "$image_revision" == "$revision" ]] \
    || die "deployed image source revision does not match the installer manifest"
  verify_volume
  validate_candidate_compose "$INSTALL_DIR" "$ENV_FILE" "$image_reference" \
    || die "active Compose identity does not match the installer manifest"
  container_id="$(compose_with "$INSTALL_DIR" "$ENV_FILE" ps -a -q open-node)" \
    || die "could not inspect the deployed container identity"
  if [[ -z "$container_id" && "$allow_absent" == "1" ]]; then
    project_runtime_is_absent \
      || die "deployed container is absent but project runtime resources remain"
    return 0
  fi
  runtime_container_is_safe "$INSTALL_DIR" "$ENV_FILE" "$image_id" 0 \
    || die "deployed container runtime identity is outside installer policy"
}

write_manifest() {
  local revision="$1" image_tag="$2" image_id="$3" temporary current_volume_fingerprint
  daemon_identity_is_current || die "Docker daemon identity changed during deployment"
  volume_is_safe || die "managed data volume is outside installer policy"
  current_volume_fingerprint="$(volume_fingerprint)" \
    || die "could not fingerprint the managed data volume"
  [[ "$current_volume_fingerprint" =~ ^[0-9a-f]{64}$ ]] \
    || die "managed data volume fingerprint is invalid"
  temporary="$(mktemp "$CONFIG_DIR/.installer.manifest.XXXXXX")" \
    || die "could not create installer manifest"
  if ! {
    printf 'MANIFEST_VERSION=%s\n' "$MANIFEST_VERSION"
    printf 'REPOSITORY=%s\n' "$REPOSITORY"
    printf 'REF=%s\n' "$REF"
    printf 'INSTALL_DIR=%s\n' "$INSTALL_DIR"
    printf 'CONFIG_DIR=%s\n' "$CONFIG_DIR"
    printf 'BACKUP_DIR=%s\n' "$BACKUP_DIR"
    printf 'PROJECT_NAME=%s\n' "$PROJECT_NAME"
    printf 'IMAGE_REPOSITORY=%s\n' "$IMAGE_REPOSITORY"
    printf 'DOCKER_DAEMON_ID=%s\n' "$DOCKER_DAEMON_ID"
    printf 'DATA_VOLUME=%s\n' "$DATA_VOLUME"
    printf 'DATA_VOLUME_FINGERPRINT=%s\n' "$current_volume_fingerprint"
    printf 'DEPLOYED_REVISION=%s\n' "$revision"
    printf 'DEPLOYED_IMAGE_TAG=%s\n' "$image_tag"
    printf 'DEPLOYED_IMAGE_ID=%s\n' "$image_id"
  } > "$temporary"; then
    rm -f -- "$temporary"
    die "could not write installer manifest"
  fi
  chmod 0600 -- "$temporary" || {
    rm -f -- "$temporary"
    die "could not secure installer manifest"
  }
  TXN_PHASE="manifest-committing"
  mv -f -- "$temporary" "$MANIFEST_FILE" || {
    rm -f -- "$temporary"
    die "could not commit installer manifest"
  }
  TXN_PHASE="commit-complete"
  TXN_CANDIDATE_ACTIVATED=0
  sync_file_and_parent "$MANIFEST_FILE"
}

write_recovery_marker() {
  local phase="$1" revision="$2" image_tag="$3" backup="$4" temporary
  local active_revision="none" active_image_id="none"
  if [[ -f "$MANIFEST_FILE" && ! -L "$MANIFEST_FILE" ]]; then
    active_revision="$(read_key "$MANIFEST_FILE" DEPLOYED_REVISION || printf 'unknown')"
    active_image_id="$(read_key "$MANIFEST_FILE" DEPLOYED_IMAGE_ID || printf 'unknown')"
  fi
  temporary="$(mktemp "$CONFIG_DIR/.installer.recovery.XXXXXX")" \
    || die "could not create recovery marker"
  if ! {
    printf 'PHASE=%s\n' "$phase"
    printf 'ACTIVE_REVISION=%s\n' "$active_revision"
    printf 'ACTIVE_IMAGE_ID=%s\n' "$active_image_id"
    printf 'CANDIDATE_REVISION=%s\n' "$revision"
    printf 'CANDIDATE_IMAGE_TAG=%s\n' "$image_tag"
    printf 'BACKUP=%s\n' "$backup"
    printf 'RECORDED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$temporary"; then
    rm -f -- "$temporary"
    die "could not write recovery marker"
  fi
  chmod 0600 -- "$temporary" || {
    rm -f -- "$temporary"
    die "could not secure recovery marker"
  }
  mv -f -- "$temporary" "$RECOVERY_FILE" || {
    rm -f -- "$temporary"
    die "could not commit recovery marker"
  }
  sync_file_and_parent "$RECOVERY_FILE"
}

clear_recovery_marker() {
  if [[ -e "$RECOVERY_FILE" || -L "$RECOVERY_FILE" ]]; then
    validate_safe_file "recovery marker" "$RECOVERY_FILE" 1
    rm -f -- "$RECOVERY_FILE" || die "could not clear recovery marker"
    sync -f -- "$CONFIG_DIR" || die "could not durably clear recovery marker"
  fi
}

clear_candidate_recovery_marker() {
  local recorded_revision recorded_image_tag
  [[ -e "$RECOVERY_FILE" || -L "$RECOVERY_FILE" ]] || return 0
  validate_safe_file "recovery marker" "$RECOVERY_FILE" 1
  [[ -n "$CANDIDATE_REVISION" && -n "$TXN_IMAGE_TAG" ]] || return 0
  recorded_revision="$(read_key "$RECOVERY_FILE" CANDIDATE_REVISION || true)"
  recorded_image_tag="$(read_key "$RECOVERY_FILE" CANDIDATE_IMAGE_TAG || true)"
  if [[ "$recorded_revision" == "$CANDIDATE_REVISION" \
    && "$recorded_image_tag" == "$TXN_IMAGE_TAG" ]]; then
    clear_recovery_marker
  fi
}

acquire_lock() {
  exec {LOCK_FD}<"$CONFIG_DIR" || die "could not open the configuration directory lock"
  flock -n "$LOCK_FD" || die "another Open Node installer process is already running"
}

acquire_global_lock() {
  local lock_directory="/run/lock/open-node-installer" daemon_hash
  DOCKER_DAEMON_ID="$(docker_daemon_identity)" \
    || die "could not read Docker daemon identity"
  [[ -n "$DOCKER_DAEMON_ID" && "$DOCKER_DAEMON_ID" != *$'\n'* \
    && "$DOCKER_DAEMON_ID" != *$'\r'* ]] \
    || die "Docker daemon returned an invalid identity"
  daemon_hash="$(printf '%s' "$DOCKER_DAEMON_ID" | sha256sum | awk '{print $1}')"
  [[ "$daemon_hash" =~ ^[0-9a-f]{64}$ ]] || die "could not hash Docker daemon identity"
  ensure_private_directory "global installer lock directory" "$lock_directory"
  GLOBAL_LOCK_FILE="$lock_directory/$PROJECT_NAME-$daemon_hash.lock"
  exec {GLOBAL_LOCK_FD}>"$GLOBAL_LOCK_FILE" || die "could not open the global installer lock"
  chmod 0600 -- "$GLOBAL_LOCK_FILE" || die "could not secure the global installer lock"
  flock -n "$GLOBAL_LOCK_FD" \
    || die "another installer controls project $PROJECT_NAME on this Docker daemon"
}

require_no_recovery() {
  if [[ -e "$RECOVERY_FILE" || -L "$RECOVERY_FILE" ]]; then
    validate_safe_file "recovery marker" "$RECOVERY_FILE" 1
    printf '[open-node] A previous deployment requires recovery:\n' >&2
    sed 's/^/[open-node]   /' "$RECOVERY_FILE" >&2
    die "restore and verify the recorded backup before removing $RECOVERY_FILE"
  fi
}

verify_checkout() {
  local expected_revision="$1" actual_revision
  validate_safe_directory "install directory" "$INSTALL_DIR" 0
  [[ -d "$INSTALL_DIR/.git" && ! -L "$INSTALL_DIR/.git" ]] \
    || die "installed source is not a regular Git checkout"
  [[ "$(git -C "$INSTALL_DIR" rev-parse --show-toplevel)" == "$INSTALL_DIR" ]] \
    || die "install directory is not the repository root"
  [[ -z "$(git -C "$INSTALL_DIR" status --porcelain --untracked-files=all)" ]] \
    || die "installed checkout has local changes"
  [[ "$(git -C "$INSTALL_DIR" remote get-url origin)" == "$REPOSITORY" ]] \
    || die "installed checkout origin does not match the manifest"
  actual_revision="$(git -C "$INSTALL_DIR" rev-parse HEAD)"
  [[ "$actual_revision" == "$expected_revision" ]] \
    || die "installed checkout revision does not match the manifest"
  [[ -f "$INSTALL_DIR/deploy/compose.yaml" && -f "$INSTALL_DIR/deploy/.env.example" ]] \
    || die "installed checkout is missing deployment assets"
}

verify_volume() {
  local expected_fingerprint actual_fingerprint
  volume_is_safe || die "managed data volume is missing or outside installer policy: $DATA_VOLUME"
  expected_fingerprint="$(read_manifest_value DATA_VOLUME_FINGERPRINT || true)"
  actual_fingerprint="$(volume_fingerprint || true)"
  [[ "$expected_fingerprint" =~ ^[0-9a-f]{64}$ \
    && "$actual_fingerprint" == "$expected_fingerprint" ]] \
    || die "managed data volume does not match the installer manifest"
}

volume_is_safe() {
  local details
  details="$(docker volume inspect "$DATA_VOLUME" 2>/dev/null)" || return 1
  printf '%s\n' "$details" | jq -e --arg name "$DATA_VOLUME" --arg project "$PROJECT_NAME" '
    length == 1
    and .[0].Name == $name
    and .[0].Driver == "local"
    and .[0].Scope == "local"
    and (((.[0].Options // {}) | length) == 0)
    and .[0].Labels["com.docker.compose.project"] == $project
    and .[0].Labels["com.docker.compose.volume"] == "data"
  ' >/dev/null
}

network_is_safe() {
  local details expected_network="${PROJECT_NAME}_default"
  details="$(docker network inspect "$expected_network" 2>/dev/null)" || return 1
  printf '%s\n' "$details" | jq -e \
    --arg name "$expected_network" --arg project "$PROJECT_NAME" '
    length == 1
    and .[0].Name == $name
    and .[0].Driver == "bridge"
    and .[0].Scope == "local"
    and .[0].Internal == false
    and .[0].Attachable == false
    and .[0].Ingress == false
    and (((.[0].Options // {}) | length) == 0)
    and .[0].Labels["com.docker.compose.project"] == $project
    and .[0].Labels["com.docker.compose.network"] == "default"
  ' >/dev/null
}

runtime_container_is_safe() {
  local source_dir="$1" environment_file="$2" expected_image_id="$3" require_running="${4:-0}"
  local container_id project_containers details expected_image expected_network expected_network_id
  local port bind_address secure_cookie short_links trusted_proxies agent_identity subscriber_totp
  expected_network="${PROJECT_NAME}_default"
  expected_image="$(read_key "$environment_file" OPEN_NODE_IMAGE_REPOSITORY || true):$(read_key "$environment_file" OPEN_NODE_IMAGE_TAG || true)"
  port="$(read_key "$environment_file" OPEN_NODE_HTTP_PORT || true)"
  bind_address="$(read_key "$environment_file" OPEN_NODE_BIND_ADDRESS || true)"
  secure_cookie="$(read_key "$environment_file" OPEN_NODE_SESSION_COOKIE_SECURE || true)"
  short_links="$(read_key "$environment_file" OPEN_NODE_SHORT_LINKS_ENABLED || true)"
  trusted_proxies="$(read_key "$environment_file" OPEN_NODE_TRUSTED_PROXIES || true)"
  agent_identity="$(read_key "$environment_file" OPEN_NODE_AGENT_IDENTITY_FILE || true)"
  subscriber_totp="$(read_key "$environment_file" OPEN_NODE_SUBSCRIBER_TOTP_KEY || true)"
  daemon_identity_is_current || return 1
  expected_network_id="$(docker network inspect --format '{{.Id}}' "$expected_network" 2>/dev/null || true)"
  [[ "$expected_network_id" =~ ^[0-9a-f]{12,64}$ ]] || return 1
  container_id="$(compose_with "$source_dir" "$environment_file" ps -a -q open-node 2>/dev/null || true)"
  [[ "$container_id" =~ ^[0-9a-f]{12,64}$ ]] || return 1
  project_containers="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT_NAME" -q 2>/dev/null || true)"
  [[ "$project_containers" == "$container_id" ]] || return 1
  volume_is_safe && network_is_safe || return 1
  details="$(docker inspect "$container_id" 2>/dev/null)" || return 1
  printf '%s\n' "$details" | jq -e \
    --arg project "$PROJECT_NAME" \
    --arg source "$(realpath -m -- "$source_dir")" \
    --arg compose_file "$(realpath -m -- "$source_dir/deploy/compose.yaml")" \
    --arg expected_image "$expected_image" \
    --arg expected_image_id "$expected_image_id" \
    --arg expected_volume "$DATA_VOLUME" \
    --arg expected_network "$expected_network" \
    --arg expected_network_id "$expected_network_id" \
    --arg port "$port" \
    --arg bind "$bind_address" \
    --arg secure_cookie "$secure_cookie" \
    --arg short_links "$short_links" \
    --arg trusted_proxies "$trusted_proxies" \
    --arg agent_identity "$agent_identity" \
    --arg subscriber_totp "$subscriber_totp" \
    --arg database_url "$RUNTIME_DATABASE_URL" \
    --arg runtime_user "$RUNTIME_UID_GID" \
    --argjson require_running "$require_running" '
    length == 1
    and (.[0] as $container
      | $container.Image == $expected_image_id
      and $container.Config.Image == $expected_image
      and $container.Config.User == $runtime_user
      and $container.Config.WorkingDir == "/opt/open-node"
      and $container.Config.Entrypoint == ["open-node-entrypoint"]
      and $container.Config.Cmd == [
        "uvicorn", "open_node.main:app", "--host", "0.0.0.0", "--port", "8080",
        "--proxy-headers", "--no-access-log"
      ]
      and (($container.Config.ExposedPorts | keys) == ["8080/tcp"])
      and (($container.Config.Volumes | keys) == ["/var/lib/open-node"])
      and $container.Config.Labels["com.docker.compose.project"] == $project
      and $container.Config.Labels["com.docker.compose.service"] == "open-node"
      and $container.Config.Labels["com.docker.compose.oneoff"] == "False"
      and $container.Config.Labels["com.docker.compose.project.working_dir"] == ($source + "/deploy")
      and $container.Config.Labels["com.docker.compose.project.config_files"] == $compose_file
      and ([ $container.Config.Env[]? | select(startswith("OPEN_NODE_DATABASE_URL=")) ] == ["OPEN_NODE_DATABASE_URL=" + $database_url])
      and ([ $container.Config.Env[]? | select(startswith("OPEN_NODE_SESSION_COOKIE_SECURE=")) ] == ["OPEN_NODE_SESSION_COOKIE_SECURE=" + $secure_cookie])
      and ([ $container.Config.Env[]? | select(startswith("OPEN_NODE_SHORT_LINKS_ENABLED=")) ] == ["OPEN_NODE_SHORT_LINKS_ENABLED=" + $short_links])
      and ([ $container.Config.Env[]? | select(startswith("OPEN_NODE_AGENT_IDENTITY_FILE=")) ] == ["OPEN_NODE_AGENT_IDENTITY_FILE=" + $agent_identity])
      and ([ $container.Config.Env[]? | select(startswith("OPEN_NODE_SUBSCRIBER_TOTP_KEY=")) ] == ["OPEN_NODE_SUBSCRIBER_TOTP_KEY=" + $subscriber_totp])
      and ([ $container.Config.Env[]? | select(startswith("FORWARDED_ALLOW_IPS=")) ] == ["FORWARDED_ALLOW_IPS=" + $trusted_proxies])
      and $container.Config.Healthcheck.Test == [
        "CMD", "python", "-c",
        "import urllib.request; urllib.request.urlopen('\''http://127.0.0.1:8080/healthz'\'', timeout=3).read()"
      ]
      and $container.Config.Healthcheck.Interval == 20000000000
      and $container.Config.Healthcheck.Timeout == 5000000000
      and $container.Config.Healthcheck.StartPeriod == 20000000000
      and $container.Config.Healthcheck.Retries == 3
      and $container.HostConfig.Privileged == false
      and $container.HostConfig.ReadonlyRootfs == true
      and (($container.HostConfig.CapAdd // []) | length == 0)
      and $container.HostConfig.CapDrop == ["ALL"]
      and $container.HostConfig.SecurityOpt == ["no-new-privileges:true"]
      and (($container.HostConfig.Devices // []) | length == 0)
      and (($container.HostConfig.DeviceRequests // []) | length == 0)
      and (($container.HostConfig.Binds // []) | length == 0)
      and (($container.HostConfig.Links // []) | length == 0)
      and (($container.HostConfig.ExtraHosts // []) | length == 0)
      and (($container.HostConfig.Dns // []) | length == 0)
      and (($container.HostConfig.DnsOptions // []) | length == 0)
      and (($container.HostConfig.DnsSearch // []) | length == 0)
      and $container.HostConfig.PublishAllPorts == false
      and $container.HostConfig.AutoRemove == false
      and $container.HostConfig.Init == true
      and $container.HostConfig.NetworkMode == $expected_network
      and ($container.HostConfig.PidMode // "") == ""
      and ($container.HostConfig.UTSMode // "") == ""
      and ($container.HostConfig.UsernsMode // "") == ""
      and (($container.HostConfig.IpcMode // "") == "" or $container.HostConfig.IpcMode == "private")
      and $container.HostConfig.RestartPolicy.Name == "unless-stopped"
      and $container.HostConfig.RestartPolicy.MaximumRetryCount == 0
      and $container.HostConfig.LogConfig.Type == "local"
      and $container.HostConfig.LogConfig.Config == {"max-file": "5", "max-size": "10m"}
      and (($container.HostConfig.PortBindings | keys) == ["8080/tcp"])
      and ($container.HostConfig.PortBindings["8080/tcp"] | length) == 1
      and $container.HostConfig.PortBindings["8080/tcp"][0].HostIp == $bind
      and $container.HostConfig.PortBindings["8080/tcp"][0].HostPort == $port
      and (($container.HostConfig.Tmpfs | keys) == ["/tmp"])
      and (
        $container.HostConfig.Tmpfs["/tmp"] == "rw,nosuid,noexec,size=64m,mode=1777"
        or $container.HostConfig.Tmpfs["/tmp"] == "rw,nosuid,noexec,size=67108864,mode=1777"
      )
      and ($container.HostConfig.Mounts | length) == 1
      and $container.HostConfig.Mounts[0].Type == "volume"
      and $container.HostConfig.Mounts[0].Source == $expected_volume
      and $container.HostConfig.Mounts[0].Target == "/var/lib/open-node"
      and ($container.HostConfig.Mounts[0].ReadOnly // false) == false
      and (($container.Mounts | length) == 1)
      and $container.Mounts[0].Type == "volume"
      and $container.Mounts[0].Name == $expected_volume
      and $container.Mounts[0].Destination == "/var/lib/open-node"
      and $container.Mounts[0].RW == true
      and (($container.NetworkSettings.Networks | keys) == [$expected_network])
      and $container.NetworkSettings.Networks[$expected_network].NetworkID == $expected_network_id
      and (($container.NetworkSettings.Ports | keys) == ["8080/tcp"])
      and ($container.NetworkSettings.Ports["8080/tcp"] | length) == 1
      and $container.NetworkSettings.Ports["8080/tcp"][0].HostIp == $bind
      and $container.NetworkSettings.Ports["8080/tcp"][0].HostPort == $port
      and (($require_running == 0) or (
        $container.State.Running == true
        and $container.State.Health.Status == "healthy"
      ))
    )
  ' >/dev/null
}

require_fresh_project() {
  local containers
  containers="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT_NAME" -q)" \
    || die "could not inspect existing Compose containers"
  [[ -z "$containers" ]] || die "Compose project already has containers: $PROJECT_NAME"
  ! docker volume inspect "$DATA_VOLUME" >/dev/null 2>&1 \
    || die "data volume already exists without an installer manifest: $DATA_VOLUME"
  ! docker network inspect "${PROJECT_NAME}_default" >/dev/null 2>&1 \
    || die "Compose network already exists without an installer manifest: ${PROJECT_NAME}_default"
}

create_candidate_environment() {
  local source_dir="$1" destination="$2" base_file="${3:-}" created=0
  local port bind_address secure_cookie existing_bind=""
  if [[ -n "$base_file" ]]; then
    validate_safe_file "active environment file" "$base_file" 1
    install -m 0600 -- "$base_file" "$destination"
    existing_bind="$(read_key "$base_file" OPEN_NODE_BIND_ADDRESS || printf '127.0.0.1')"
  else
    install -m 0600 -- "$source_dir/deploy/.env.example" "$destination"
    created=1
  fi
  port="${OPEN_NODE_HTTP_PORT:-$(read_key "$destination" OPEN_NODE_HTTP_PORT || printf '8080')}"
  bind_address="${OPEN_NODE_BIND_ADDRESS:-$(read_key "$destination" OPEN_NODE_BIND_ADDRESS || printf '127.0.0.1')}"
  if [[ -n "${OPEN_NODE_SESSION_COOKIE_SECURE:-}" ]]; then
    secure_cookie="$OPEN_NODE_SESSION_COOKIE_SECURE"
  elif [[ "$created" -eq 1 ]]; then
    secure_cookie=false
  else
    secure_cookie="$(read_key "$destination" OPEN_NODE_SESSION_COOKIE_SECURE || printf 'false')"
  fi
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] \
    || die "OPEN_NODE_HTTP_PORT must be between 1 and 65535"
  case "$bind_address" in
    127.0.0.1) ;;
    0.0.0.0)
      [[ "$existing_bind" == "0.0.0.0" || "${OPEN_NODE_ALLOW_PUBLIC_HTTP:-0}" == "1" ]] \
        || die "binding public HTTP requires OPEN_NODE_ALLOW_PUBLIC_HTTP=1"
      ;;
    *) die "OPEN_NODE_BIND_ADDRESS must be 127.0.0.1 or 0.0.0.0" ;;
  esac
  [[ "$secure_cookie" == "true" || "$secure_cookie" == "false" ]] \
    || die "OPEN_NODE_SESSION_COOKIE_SECURE must be true or false"
  set_file_value "$destination" OPEN_NODE_IMAGE_REPOSITORY "$IMAGE_REPOSITORY"
  set_file_value "$destination" OPEN_NODE_BIND_ADDRESS "$bind_address"
  set_file_value "$destination" OPEN_NODE_HTTP_PORT "$port"
  set_file_value "$destination" OPEN_NODE_SESSION_COOKIE_SECURE "$secure_cookie"
  set_file_value "$destination" OPEN_NODE_SHORT_LINKS_ENABLED \
    "$(read_key "$destination" OPEN_NODE_SHORT_LINKS_ENABLED || printf 'false')"
  chmod 0600 -- "$destination"
}

set_candidate_identity() {
  local environment_file="$1" revision="$2" image_tag="$3"
  set_file_value "$environment_file" OPEN_NODE_REVISION "$revision"
  set_file_value "$environment_file" OPEN_NODE_IMAGE_TAG "$image_tag"
}

validate_candidate_compose() {
  local source_dir="$1" environment_file="$2" expected_image="$3"
  local images config_json context revision port bind_address secure_cookie short_links
  local trusted_proxies agent_identity subscriber_totp
  context="$(realpath -m -- "$source_dir")"
  [[ -d "$context" && ! -L "$context" \
    && -f "$context/Dockerfile" && ! -L "$context/Dockerfile" \
    && -f "$context/deploy/compose.yaml" && ! -L "$context/deploy/compose.yaml" ]] || {
    warn "candidate build context, Dockerfile, or Compose file is not a regular path"
    return 1
  }
  revision="$(read_key "$environment_file" OPEN_NODE_REVISION || true)"
  port="$(read_key "$environment_file" OPEN_NODE_HTTP_PORT || true)"
  bind_address="$(read_key "$environment_file" OPEN_NODE_BIND_ADDRESS || true)"
  secure_cookie="$(read_key "$environment_file" OPEN_NODE_SESSION_COOKIE_SECURE || true)"
  short_links="$(read_key "$environment_file" OPEN_NODE_SHORT_LINKS_ENABLED || true)"
  trusted_proxies="$(read_key "$environment_file" OPEN_NODE_TRUSTED_PROXIES || true)"
  agent_identity="$(read_key "$environment_file" OPEN_NODE_AGENT_IDENTITY_FILE || true)"
  subscriber_totp="$(read_key "$environment_file" OPEN_NODE_SUBSCRIBER_TOTP_KEY || true)"
  [[ "$revision" =~ ^[0-9a-f]{40,64}$ \
    && "$port" =~ ^[0-9]+$ \
    && ( "$bind_address" == "127.0.0.1" || "$bind_address" == "0.0.0.0" ) ]] || {
    warn "candidate identity or listener settings are invalid"
    return 1
  }
  if ! images="$(compose_with "$source_dir" "$environment_file" config --images)"; then
    warn "candidate Compose configuration is invalid"
    return 1
  fi
  if [[ "$images" != "$expected_image" ]]; then
    warn "candidate Compose image does not match $expected_image"
    return 1
  fi
  if ! config_json="$(compose_with "$source_dir" "$environment_file" config --format json)"; then
    warn "candidate Compose data mount could not be inspected"
    return 1
  fi
  if ! printf '%s\n' "$config_json" | jq -e \
    --arg project "$PROJECT_NAME" \
    --arg expected_volume "$DATA_VOLUME" \
    --arg expected_network "${PROJECT_NAME}_default" \
    --arg expected_image "$expected_image" \
    --arg context "$context" \
    --arg revision "$revision" \
    --arg port "$port" \
    --arg bind "$bind_address" \
    --arg secure_cookie "$secure_cookie" \
    --arg short_links "$short_links" \
    --arg trusted_proxies "$trusted_proxies" \
    --arg agent_identity "$agent_identity" \
    --arg subscriber_totp "$subscriber_totp" '
    .services["open-node"] as $service
    | ((keys) == ["name", "networks", "services", "volumes"])
    and (.name == $project)
    and ((.services | keys) == ["open-node"])
    and ((.volumes | keys) == ["data"])
    and (((.configs // {}) | length) == 0)
    and (((.secrets // {}) | length) == 0)
    and ((.networks | keys) == ["default"])
    and (.networks.default.name == $expected_network)
    and ((.networks.default.driver // "bridge") == "bridge")
    and ((.networks.default.external // false) == false)
    and (((.networks.default.driver_opts // {}) | length) == 0)
    and (((.networks.default.ipam // {}) | length) == 0)
    and (((.networks.default.labels // {}) | length) == 0)
    and (((.networks.default | keys) - [
      "driver", "driver_opts", "enable_ipv4", "enable_ipv6", "external", "ipam",
      "labels", "name"
    ]) | length == 0)
    and ((($service | keys) - [
      "build", "cap_drop", "command", "entrypoint", "environment", "image", "init", "logging",
      "networks", "ports", "pull_policy", "read_only", "restart",
      "security_opt", "stop_grace_period", "tmpfs", "volumes"
    ]) | length == 0)
    and ($service.command == null)
    and ($service.entrypoint == null)
    and ($service.image == $expected_image)
    and ($service.pull_policy == "never")
    and ($service.build.context == $context)
    and ($service.build.dockerfile == "Dockerfile")
    and ((($service.build | keys) - ["args", "context", "dockerfile"]) | length == 0)
    and (($service.build.args | keys) == ["VCS_REF"])
    and ($service.build.args.VCS_REF == $revision)
    and ($service.init == true)
    and ($service.restart == "unless-stopped")
    and ($service.read_only == true)
    and ($service.cap_drop == ["ALL"])
    and ($service.security_opt == ["no-new-privileges:true"])
    and ($service.environment == {
      "FORWARDED_ALLOW_IPS": $trusted_proxies,
      "OPEN_NODE_AGENT_IDENTITY_FILE": $agent_identity,
      "OPEN_NODE_SESSION_COOKIE_SECURE": $secure_cookie,
      "OPEN_NODE_SHORT_LINKS_ENABLED": $short_links,
      "OPEN_NODE_SUBSCRIBER_TOTP_KEY": $subscriber_totp
    })
    and ($service.logging.driver == "local")
    and ($service.logging.options == {"max-file": "5", "max-size": "10m"})
    and (
      $service.tmpfs == ["/tmp:rw,nosuid,noexec,size=64m,mode=1777"]
      or $service.tmpfs == ["/tmp:rw,nosuid,noexec,size=67108864,mode=1777"]
    )
    and ($service.stop_grace_period == "30s")
    and (($service.networks | keys) == ["default"])
    and (
      [$service.ports[]?] as $ports
      | ($ports | length) == 1
      and ($ports[0].target | tostring) == "8080"
      and ($ports[0].published | tostring) == $port
      and $ports[0].host_ip == $bind
      and ($ports[0].protocol // "tcp") == "tcp"
      and ($ports[0].mode // "ingress") == "ingress"
      and ((($ports[0] | keys) - ["host_ip", "mode", "protocol", "published", "target"]) | length == 0)
    )
    and (.volumes.data.name == $expected_volume)
    and ((.volumes.data.external // false) == false)
    and ((.volumes.data.driver // "local") == "local")
    and (((.volumes.data.driver_opts // {}) | length) == 0)
    and (((.volumes.data.labels // {}) | length) == 0)
    and (((.volumes.data | keys) - ["driver", "driver_opts", "external", "labels", "name"]) | length == 0)
    and (
      [$service.volumes[]?] as $mounts
      | ($mounts | length) == 1
      and $mounts[0].target == "/var/lib/open-node"
      and $mounts[0].type == "volume"
      and $mounts[0].source == "data"
      and ((((($mounts[0] | keys) - ["source", "target", "type", "volume"]) | length) == 0))
      and ((($mounts[0].volume // {}) | length) == 0)
    )
  ' >/dev/null; then
    warn "candidate rendered Compose configuration is outside the installer allowlist"
    return 1
  fi
}

candidate_image_is_safe_to_start() {
  local image_id="$1" revision="$2" details
  details="$(docker image inspect "$image_id" 2>/dev/null)" || return 1
  printf '%s\n' "$details" | jq -e \
    --arg image_id "$image_id" \
    --arg revision "$revision" \
    --arg database_url "$RUNTIME_DATABASE_URL" \
    --arg runtime_user "$RUNTIME_UID_GID" '
    length == 1
    and (.[0] as $image
      | $image.Id == $image_id
      and $image.Config.User == $runtime_user
      and $image.Config.WorkingDir == "/opt/open-node"
      and $image.Config.Entrypoint == ["open-node-entrypoint"]
      and $image.Config.Cmd == [
        "uvicorn", "open_node.main:app", "--host", "0.0.0.0", "--port", "8080",
        "--proxy-headers", "--no-access-log"
      ]
      and (($image.Config.ExposedPorts | keys) == ["8080/tcp"])
      and (($image.Config.Volumes | keys) == ["/var/lib/open-node"])
      and (($image.Config.OnBuild // []) | length == 0)
      and $image.Config.Labels["org.opencontainers.image.revision"] == $revision
      and ([ $image.Config.Env[]? | select(startswith("OPEN_NODE_DATABASE_URL=")) ] == ["OPEN_NODE_DATABASE_URL=" + $database_url])
      and $image.Config.Healthcheck.Test == [
        "CMD", "python", "-c",
        "import urllib.request; urllib.request.urlopen('\''http://127.0.0.1:8080/healthz'\'', timeout=3).read()"
      ]
      and $image.Config.Healthcheck.Interval == 20000000000
      and $image.Config.Healthcheck.Timeout == 5000000000
      and $image.Config.Healthcheck.StartPeriod == 20000000000
      and $image.Config.Healthcheck.Retries == 3
    )
  ' >/dev/null
}

build_candidate_image() {
  local source_dir="$1" environment_file="$2" image_reference="$3"
  local revision built_revision arguments=(build)
  revision="$(read_key "$environment_file" OPEN_NODE_REVISION)" || return 1
  daemon_identity_is_current || return 1
  if docker image inspect "$image_reference" >/dev/null 2>&1; then
    warn "refusing to overwrite an existing candidate image tag: $image_reference"
    return 1
  fi
  [[ "$BUILD_PULL" == "0" ]] || arguments+=(--pull)
  arguments+=(
    --file "$source_dir/Dockerfile"
    --network default
    --build-arg "VCS_REF=$revision"
    --tag "$image_reference"
    "$source_dir"
  )
  log "building candidate image $image_reference"
  if ! docker "${arguments[@]}"; then
    return 1
  fi
  if ! docker image inspect "$image_reference" >/dev/null 2>&1; then
    warn "candidate build did not create $image_reference"
    return 1
  fi
  CANDIDATE_IMAGE_ID="$(docker image inspect --format '{{.Id}}' "$image_reference")" || return 1
  built_revision="$(docker image inspect --format '{{ index .Config.Labels "org.opencontainers.image.revision" }}' "$CANDIDATE_IMAGE_ID" 2>/dev/null || true)"
  if [[ "$built_revision" != "$revision" ]]; then
    warn "candidate image source revision label does not match the selected checkout"
    return 1
  fi
  if ! candidate_image_is_safe_to_start "$CANDIDATE_IMAGE_ID" "$revision"; then
    warn "candidate image runtime metadata is outside the installer allowlist"
    return 1
  fi
}

wait_for_health() {
  local source_dir="$1" environment_file="$2" expected_image_id="$3"
  local port bind_address attempt stable=0
  port="$(read_key "$environment_file" OPEN_NODE_HTTP_PORT)"
  bind_address="$(read_key "$environment_file" OPEN_NODE_BIND_ADDRESS)"
  [[ "$port" =~ ^[0-9]+$ && "$port" -ge 1 && "$port" -le 65535 ]] || return 1
  [[ "$bind_address" == "127.0.0.1" || "$bind_address" == "0.0.0.0" ]] || return 1
  for attempt in $(seq 1 90); do
    if runtime_container_is_safe "$source_dir" "$environment_file" "$expected_image_id" 1 \
      && curl --noproxy '*' --fail --silent --show-error --max-time 3 \
        "http://127.0.0.1:$port/healthz" >/dev/null 2>&1; then
      ((stable += 1))
      if [[ "$stable" -ge "$HEALTH_STABLE_OBSERVATIONS" ]]; then
        return 0
      fi
    else
      stable=0
    fi
    sleep 1
  done
  compose_with "$source_dir" "$environment_file" ps >&2 || true
  compose_with "$source_dir" "$environment_file" logs --tail 100 open-node >&2 || true
  return 1
}

safe_delete_candidate() {
  local candidate="$1" parent
  [[ -n "$candidate" && -d "$candidate" && ! -L "$candidate" ]] || return 0
  parent="$(dirname -- "$INSTALL_DIR")"
  [[ "$candidate" == "$parent/.open-node-candidate."* ]] \
    || die "refusing to clean unexpected path: $candidate"
  find "$candidate" -depth -delete
}

quarantine_candidate() {
  local ids id identity remaining network_details network_names down_failed=0
  [[ "$TXN_CANDIDATE_ACTIVATED" == "1" ]] || return 0
  daemon_identity_is_current || return 1
  if [[ -n "$CANDIDATE_SOURCE" && -n "$TXN_CANDIDATE_ENV" \
    && -f "$CANDIDATE_SOURCE/deploy/compose.yaml" \
    && -f "$TXN_CANDIDATE_ENV" ]]; then
    if ! compose_with "$CANDIDATE_SOURCE" "$TXN_CANDIDATE_ENV" down --remove-orphans \
      >/dev/null 2>&1; then
      down_failed=1
      warn "Compose could not remove the candidate; applying exact-label quarantine"
    fi
  fi
  ids="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT_NAME" -q)" \
    || return 1
  for id in $ids; do
    identity="$(docker inspect --format '{{ index .Config.Labels "com.docker.compose.project" }}:{{ index .Config.Labels "com.docker.compose.service" }}' "$id" 2>/dev/null || true)"
    [[ "$identity" == "$PROJECT_NAME:open-node" ]] || return 1
    if ! docker rm -f -- "$id" >/dev/null 2>&1; then
      warn "could not remove quarantined candidate container $id"
      return 1
    fi
  done
  remaining="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT_NAME" -q)" \
    || return 1
  [[ -z "$remaining" ]] || return 1
  if docker network inspect "${PROJECT_NAME}_default" >/dev/null 2>&1; then
    network_details="$(docker network inspect "${PROJECT_NAME}_default" 2>/dev/null)" \
      || return 1
    printf '%s\n' "$network_details" | jq -e --arg project "$PROJECT_NAME" '
      length == 1
      and .[0].Name == ($project + "_default")
      and .[0].Labels["com.docker.compose.project"] == $project
      and .[0].Labels["com.docker.compose.network"] == "default"
    ' >/dev/null || return 1
    if ! docker network rm "${PROJECT_NAME}_default" >/dev/null 2>&1; then
      warn "could not remove quarantined candidate network ${PROJECT_NAME}_default"
      return 1
    fi
  fi
  network_names="$(docker network ls --format '{{.Name}}')" || return 1
  if printf '%s\n' "$network_names" | grep -Fxq "${PROJECT_NAME}_default"; then
    return 1
  fi
  if [[ "$down_failed" == "1" ]]; then
    log "candidate containment was recovered by exact-label quarantine" >&2
  fi
  TXN_CANDIDATE_ACTIVATED=0
}

project_runtime_is_absent() {
  local containers network_names
  daemon_identity_is_current || return 1
  containers="$(docker ps -a --filter "label=com.docker.compose.project=$PROJECT_NAME" -q)" \
    || return 1
  [[ -z "$containers" ]] || return 1
  network_names="$(docker network ls --format '{{.Name}}')" || return 1
  ! printf '%s\n' "$network_names" | grep -Fxq "${PROJECT_NAME}_default"
}

mark_containment_failure() {
  local phase="$1"
  write_recovery_marker \
    "containment-failed-$phase" "$CANDIDATE_REVISION" "$TXN_IMAGE_TAG" "$TXN_BACKUP"
  TXN_PHASE="containment-failed"
  warn "candidate containment could not be verified; source and environment were preserved"
}

cleanup_candidate_artifacts() {
  local failed=0
  if [[ -n "$TXN_CANDIDATE_ENV" && "$TXN_CANDIDATE_ENV" != "$ENV_FILE" ]]; then
    rm -f -- "$TXN_CANDIDATE_ENV" || failed=1
  fi
  case "$TXN_KIND" in
    fresh)
      safe_delete_candidate "$CANDIDATE_SOURCE" || failed=1
      ;;
    update)
      remove_update_worktree "$CANDIDATE_SOURCE" || failed=1
      ;;
  esac
  TXN_CANDIDATE_ENV=""
  CANDIDATE_SOURCE=""
  [[ "$failed" == "0" ]]
}

manifest_records_candidate() {
  [[ -f "$MANIFEST_FILE" && ! -L "$MANIFEST_FILE" ]] || return 1
  [[ "$(read_key "$MANIFEST_FILE" DEPLOYED_REVISION || true)" == "$CANDIDATE_REVISION" \
    && "$(read_key "$MANIFEST_FILE" DEPLOYED_IMAGE_TAG || true)" == "$TXN_IMAGE_TAG" \
    && "$(read_key "$MANIFEST_FILE" DEPLOYED_IMAGE_ID || true)" == "$CANDIDATE_IMAGE_ID" ]]
}

backup_verify_volume_is_safe() {
  local details
  [[ -n "$TXN_VERIFY_VOLUME" \
    && "$TXN_VERIFY_VOLUME" == "$PROJECT_NAME-backup-verify-"* ]] || return 1
  details="$(docker volume inspect "$TXN_VERIFY_VOLUME" 2>/dev/null)" || return 1
  printf '%s\n' "$details" | jq -e \
    --arg name "$TXN_VERIFY_VOLUME" --arg project "$PROJECT_NAME" '
    length == 1
    and .[0].Name == $name
    and .[0].Driver == "local"
    and .[0].Scope == "local"
    and (((.[0].Options // {}) | length) == 0)
    and .[0].Labels["com.open-node.installer.project"] == $project
    and .[0].Labels["com.open-node.installer.purpose"] == "backup-restore-verification"
  ' >/dev/null
}

backup_container_is_safe() {
  local details
  [[ -n "$TXN_BACKUP_CONTAINER" \
    && "$TXN_BACKUP_CONTAINER" == "$PROJECT_NAME-backup-"* ]] || return 1
  details="$(docker inspect "$TXN_BACKUP_CONTAINER" 2>/dev/null)" || return 1
  printf '%s\n' "$details" | jq -e \
    --arg name "/$TXN_BACKUP_CONTAINER" --arg project "$PROJECT_NAME" '
    length == 1
    and .[0].Name == $name
    and .[0].Config.Labels["com.open-node.installer.project"] == $project
    and .[0].Config.Labels["com.open-node.installer.purpose"] == "backup-helper"
  ' >/dev/null
}

cleanup_partial_backup() {
  if [[ -n "$TXN_BACKUP_CONTAINER" && "$TXN_BACKUP_CONTAINER" == "$PROJECT_NAME-backup-"* ]]; then
    if docker inspect "$TXN_BACKUP_CONTAINER" >/dev/null 2>&1; then
      if ! backup_container_is_safe; then
        warn "refusing to remove an unverified temporary container: $TXN_BACKUP_CONTAINER"
      elif ! docker rm -f -- "$TXN_BACKUP_CONTAINER" >/dev/null 2>&1; then
        warn "temporary backup container could not be removed: $TXN_BACKUP_CONTAINER"
      fi
    fi
  fi
  TXN_BACKUP_CONTAINER=""
  if [[ -n "$TXN_VERIFY_VOLUME" && "$TXN_VERIFY_VOLUME" == "$PROJECT_NAME-backup-verify-"* ]]; then
    if docker volume inspect "$TXN_VERIFY_VOLUME" >/dev/null 2>&1; then
      if ! backup_verify_volume_is_safe; then
        warn "refusing to remove an unverified temporary volume: $TXN_VERIFY_VOLUME"
      elif ! docker volume rm "$TXN_VERIFY_VOLUME" >/dev/null 2>&1; then
        warn "temporary backup verification volume could not be removed: $TXN_VERIFY_VOLUME"
      fi
    fi
  fi
  TXN_VERIFY_VOLUME=""
  if [[ -n "$TXN_ROLLBACK_IMAGE" && "$TXN_ROLLBACK_IMAGE" == "$IMAGE_REPOSITORY:rollback-"* ]]; then
    if docker image inspect "$TXN_ROLLBACK_IMAGE" >/dev/null 2>&1 \
      && ! docker image rm -- "$TXN_ROLLBACK_IMAGE" >/dev/null 2>&1; then
      warn "temporary rollback tag could not be removed: $TXN_ROLLBACK_IMAGE"
    fi
  fi
  TXN_ROLLBACK_IMAGE=""
  if [[ -n "$TXN_TEMP_BACKUP" && -d "$TXN_TEMP_BACKUP" && ! -L "$TXN_TEMP_BACKUP" \
    && "$TXN_TEMP_BACKUP" == "$BACKUP_DIR/.open-node-backup."* ]]; then
    find "$TXN_TEMP_BACKUP" -depth -delete \
      || warn "temporary backup directory could not be completely removed: $TXN_TEMP_BACKUP"
  fi
  TXN_TEMP_BACKUP=""
}

cleanup_transaction_on_exit() {
  local status="$?"
  trap - EXIT INT TERM HUP
  case "$TXN_PHASE" in
    idle|handled|commit-complete)
      ;;
    containment-failed)
      cleanup_partial_backup
      ;;
    manifest-committing)
      if manifest_records_candidate; then
        TXN_CANDIDATE_ACTIVATED=0
        warn "deployment manifest committed before interruption; candidate was left active"
      else
        if ! quarantine_candidate; then
          mark_containment_failure "interrupted-$TXN_PHASE"
          cleanup_partial_backup
          exit "$status"
        fi
        write_recovery_marker \
          "interrupted-$TXN_PHASE" "$CANDIDATE_REVISION" "$TXN_IMAGE_TAG" "$TXN_BACKUP"
        cleanup_partial_backup
        cleanup_candidate_artifacts \
          || warn "contained candidate artifacts could not be completely removed"
      fi
      ;;
    prepared|candidate-built)
      clear_candidate_recovery_marker
      cleanup_partial_backup
      cleanup_candidate_artifacts \
        || warn "inactive candidate artifacts could not be completely removed"
      ;;
    *)
      if ! quarantine_candidate; then
        mark_containment_failure "interrupted-$TXN_PHASE"
        cleanup_partial_backup
        exit "$status"
      fi
      if [[ -n "$CANDIDATE_REVISION" && -n "$TXN_IMAGE_TAG" ]]; then
        write_recovery_marker \
          "interrupted-$TXN_PHASE" "$CANDIDATE_REVISION" "$TXN_IMAGE_TAG" "$TXN_BACKUP"
      fi
      cleanup_partial_backup
      cleanup_candidate_artifacts \
        || warn "contained candidate artifacts could not be completely removed"
      ;;
  esac
  exit "$status"
}

trap cleanup_transaction_on_exit EXIT
trap 'exit 130' INT TERM HUP

prepare_fresh_candidate() {
  local parent
  parent="$(dirname -- "$INSTALL_DIR")"
  validate_safe_directory "install parent" "$parent" 0
  CANDIDATE_SOURCE="$(mktemp -d "$parent/.open-node-candidate.XXXXXX")" \
    || die "could not create candidate checkout directory"
  TXN_KIND="fresh"
  TXN_PHASE="prepared"
  log "cloning $REPOSITORY ($REF)"
  if ! git clone --no-local --single-branch --branch "$REF" -- "$REPOSITORY" "$CANDIDATE_SOURCE"; then
    safe_delete_candidate "$CANDIDATE_SOURCE"
    die "could not clone the requested Open Node ref"
  fi
  [[ -f "$CANDIDATE_SOURCE/deploy/compose.yaml" && -f "$CANDIDATE_SOURCE/deploy/.env.example" ]] \
    || {
      safe_delete_candidate "$CANDIDATE_SOURCE"
      die "candidate repository is missing deployment assets"
    }
  CANDIDATE_REVISION="$(git -C "$CANDIDATE_SOURCE" rev-parse HEAD)"
}

prepare_update_candidate() {
  local parent old_revision
  old_revision="$(read_manifest_value DEPLOYED_REVISION)"
  verify_checkout "$old_revision"
  log "fetching candidate ref $REF"
  git -C "$INSTALL_DIR" fetch --no-tags origin "$REF"
  CANDIDATE_REVISION="$(git -C "$INSTALL_DIR" rev-parse FETCH_HEAD)"
  git -C "$INSTALL_DIR" merge-base --is-ancestor "$old_revision" "$CANDIDATE_REVISION" \
    || die "candidate ref is not a fast-forward descendant of the deployed revision"
  if [[ "$CANDIDATE_REVISION" == "$old_revision" ]]; then
    CANDIDATE_UNCHANGED=1
    return
  fi
  parent="$(dirname -- "$INSTALL_DIR")"
  CANDIDATE_SOURCE="$(mktemp -d "$parent/.open-node-candidate.XXXXXX")" \
    || die "could not reserve candidate worktree path"
  rmdir -- "$CANDIDATE_SOURCE" || die "could not prepare candidate worktree path"
  git -C "$INSTALL_DIR" worktree add --detach "$CANDIDATE_SOURCE" "$CANDIDATE_REVISION" \
    || die "could not create candidate worktree"
  TXN_KIND="update"
  TXN_PHASE="prepared"
}

remove_update_worktree() {
  local candidate="$1"
  [[ -n "$candidate" ]] || return 0
  if ! git -C "$INSTALL_DIR" worktree remove --force "$candidate"; then
    warn "candidate worktree could not be removed: $candidate"
    return 1
  fi
}

backup_stopped_volume() {
  local source_dir="$1" active_env="$2" old_revision="$3" old_tag="$4" old_image_id="$5" transaction_id="$6"
  local temporary_bundle archive final_bundle timestamp rollback_tag rollback_image_reference created_volume
  local original_database_sha restored_database_sha artifact archive_entries
  local database_probe extraction_failed=0
  ( ensure_private_directory "OPEN_NODE_BACKUP_DIR" "$BACKUP_DIR" ) || return 1
  daemon_identity_is_current || return 1
  volume_is_safe || return 1
  docker image inspect "$old_image_id" >/dev/null 2>&1 || return 1
  temporary_bundle="$(mktemp -d "$BACKUP_DIR/.open-node-backup.XXXXXX")" || return 1
  TXN_TEMP_BACKUP="$temporary_bundle"
  archive="$temporary_bundle/volume.tar.gz"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  final_bundle="$BACKUP_DIR/open-node-$timestamp-${old_revision:0:12}-$transaction_id"
  rollback_tag="rollback-$old_revision-$transaction_id"
  TXN_BACKUP_CONTAINER="$PROJECT_NAME-backup-$transaction_id"
  log "creating stopped-volume backup in $final_bundle" >&2
  database_probe=$'import hashlib\nimport pathlib\nimport sqlite3\np = pathlib.Path("/var/lib/open-node/open-node.db")\nif p.is_symlink() or not p.is_file() or p.stat().st_size <= 0:\n    raise SystemExit("open-node.db is missing, empty, or a symlink")\nconnection = sqlite3.connect("file:/var/lib/open-node/open-node.db?mode=ro", uri=True)\ntry:\n    result = connection.execute("PRAGMA integrity_check").fetchall()\nfinally:\n    connection.close()\nif result != [("ok",)]:\n    raise SystemExit(f"SQLite integrity_check failed: {result!r}")\nwith p.open("rb") as stream:\n    print(hashlib.file_digest(stream, "sha256").hexdigest())'
  if ! original_database_sha="$(docker run --rm --name "$TXN_BACKUP_CONTAINER" \
    --network none --read-only --user "$RUNTIME_UID_GID" --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --label "com.open-node.installer.project=$PROJECT_NAME" \
    --label "com.open-node.installer.purpose=backup-helper" \
    --mount "type=volume,src=$DATA_VOLUME,dst=/var/lib/open-node,readonly" \
    --entrypoint python "$old_image_id" -c "$database_probe")"; then
    cleanup_partial_backup
    return 1
  fi
  TXN_BACKUP_CONTAINER=""
  if [[ ! "$original_database_sha" =~ ^[0-9a-f]{64}$ ]]; then
    cleanup_partial_backup
    return 1
  fi
  TXN_BACKUP_CONTAINER="$PROJECT_NAME-backup-$transaction_id"
  if ! docker run --rm --name "$TXN_BACKUP_CONTAINER" --network none --read-only \
    --user "$RUNTIME_UID_GID" --cap-drop ALL --security-opt no-new-privileges:true \
    --label "com.open-node.installer.project=$PROJECT_NAME" \
    --label "com.open-node.installer.purpose=backup-helper" \
    --mount "type=volume,src=$DATA_VOLUME,dst=/var/lib/open-node,readonly" \
    --entrypoint tar "$old_image_id" -C /var/lib/open-node -czf - . > "$archive"; then
    cleanup_partial_backup
    return 1
  fi
  TXN_BACKUP_CONTAINER=""
  archive_entries="$(tar -tzf "$archive" 2>/dev/null || true)"
  if [[ ! -s "$archive" || -z "$archive_entries" ]] \
    || ! printf '%s\n' "$archive_entries" | grep -Fxq './open-node.db' \
    || printf '%s\n' "$archive_entries" | grep -Eq '(^/|(^|/)\.\.(/|$))' \
    || tar -tvzf "$archive" | grep -Eq '^[^-d]' \
    || ! tar -tvzf "$archive" | awk '
      substr($0, 1, 1) == "-" && $NF == "./open-node.db" { found = 1 }
      END { exit(found ? 0 : 1) }
    '; then
    cleanup_partial_backup
    return 1
  fi
  if ! install -m 0600 -- "$active_env" "$temporary_bundle/open-node.env" \
    || ! install -m 0600 -- "$MANIFEST_FILE" "$temporary_bundle/installer.manifest" \
    || ! install -m 0600 -- "$source_dir/deploy/compose.yaml" "$temporary_bundle/compose.yaml"; then
    cleanup_partial_backup
    return 1
  fi
  rollback_image_reference="$IMAGE_REPOSITORY:$rollback_tag"
  if docker image inspect "$rollback_image_reference" >/dev/null 2>&1; then
    cleanup_partial_backup
    return 1
  fi
  TXN_ROLLBACK_IMAGE="$rollback_image_reference"
  if ! docker image tag "$old_image_id" "$TXN_ROLLBACK_IMAGE"; then
    cleanup_partial_backup
    return 1
  fi
  if ! {
    printf 'REVISION=%s\n' "$old_revision"
    printf 'IMAGE_TAG=%s\n' "$old_tag"
    printf 'IMAGE_ID=%s\n' "$old_image_id"
    printf 'ROLLBACK_IMAGE=%s:%s\n' "$IMAGE_REPOSITORY" "$rollback_tag"
    printf 'PROJECT_NAME=%s\n' "$PROJECT_NAME"
    printf 'DATA_VOLUME=%s\n' "$DATA_VOLUME"
    printf 'DATABASE_SHA256=%s\n' "$original_database_sha"
    printf 'DOCKER_DAEMON_ID=%s\n' "$DOCKER_DAEMON_ID"
    printf 'CREATED_AT=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  } > "$temporary_bundle/deployment.meta"; then
    cleanup_partial_backup
    return 1
  fi
  chmod 0600 -- "$temporary_bundle/deployment.meta" || {
    cleanup_partial_backup
    return 1
  }
  TXN_VERIFY_VOLUME="$PROJECT_NAME-backup-verify-$transaction_id"
  if docker volume inspect "$TXN_VERIFY_VOLUME" >/dev/null 2>&1; then
    cleanup_partial_backup
    return 1
  fi
  if ! created_volume="$(docker volume create --driver local \
    --label "com.open-node.installer.project=$PROJECT_NAME" \
    --label "com.open-node.installer.purpose=backup-restore-verification" \
    "$TXN_VERIFY_VOLUME")" \
    || [[ "$created_volume" != "$TXN_VERIFY_VOLUME" ]] \
    || ! backup_verify_volume_is_safe; then
    cleanup_partial_backup
    return 1
  fi
  chmod 0644 -- "$archive" || {
    cleanup_partial_backup
    return 1
  }
  TXN_BACKUP_CONTAINER="$PROJECT_NAME-backup-$transaction_id"
  docker run --rm --name "$TXN_BACKUP_CONTAINER" --network none --read-only \
    --user "$RUNTIME_UID_GID" --cap-drop ALL --security-opt no-new-privileges:true \
    --label "com.open-node.installer.project=$PROJECT_NAME" \
    --label "com.open-node.installer.purpose=backup-helper" \
    --mount "type=bind,src=$archive,dst=/tmp/volume.tar.gz,readonly" \
    --mount "type=volume,src=$TXN_VERIFY_VOLUME,dst=/var/lib/open-node" \
    --entrypoint tar "$old_image_id" -C /var/lib/open-node -xzf /tmp/volume.tar.gz \
    || extraction_failed=1
  chmod 0600 -- "$archive" || extraction_failed=1
  if [[ "$extraction_failed" == "1" ]]; then
    cleanup_partial_backup
    return 1
  fi
  TXN_BACKUP_CONTAINER=""
  TXN_BACKUP_CONTAINER="$PROJECT_NAME-backup-$transaction_id"
  if ! restored_database_sha="$(docker run --rm --name "$TXN_BACKUP_CONTAINER" \
    --network none --read-only --user "$RUNTIME_UID_GID" --cap-drop ALL \
    --security-opt no-new-privileges:true \
    --label "com.open-node.installer.project=$PROJECT_NAME" \
    --label "com.open-node.installer.purpose=backup-helper" \
    --mount "type=volume,src=$TXN_VERIFY_VOLUME,dst=/var/lib/open-node,readonly" \
    --entrypoint python "$old_image_id" -c "$database_probe")"; then
    cleanup_partial_backup
    return 1
  fi
  TXN_BACKUP_CONTAINER=""
  if [[ "$restored_database_sha" != "$original_database_sha" ]] \
    || ! backup_verify_volume_is_safe \
    || ! docker volume rm "$TXN_VERIFY_VOLUME" >/dev/null 2>&1; then
    cleanup_partial_backup
    return 1
  fi
  TXN_VERIFY_VOLUME=""
  if ! (
    cd -- "$temporary_bundle"
    sha256sum compose.yaml deployment.meta installer.manifest open-node.env volume.tar.gz \
      > SHA256SUMS
  ); then
    cleanup_partial_backup
    return 1
  fi
  chmod 0600 -- "$temporary_bundle/SHA256SUMS" || {
    cleanup_partial_backup
    return 1
  }
  for artifact in SHA256SUMS compose.yaml deployment.meta installer.manifest open-node.env volume.tar.gz; do
    if ! sync -f -- "$temporary_bundle/$artifact"; then
      cleanup_partial_backup
      return 1
    fi
  done
  if ! sync -f -- "$temporary_bundle"; then
    cleanup_partial_backup
    return 1
  fi
  if ! mv -T -- "$temporary_bundle" "$final_bundle"; then
    cleanup_partial_backup
    return 1
  fi
  if ! sync -f -- "$final_bundle" || ! sync -f -- "$BACKUP_DIR"; then
    warn "backup was published but could not be durably synced: $final_bundle"
    TXN_TEMP_BACKUP=""
    if [[ -d "$final_bundle" && ! -L "$final_bundle" \
      && "$final_bundle" == "$BACKUP_DIR/open-node-"* ]]; then
      find "$final_bundle" -depth -delete \
        || warn "non-durable backup bundle requires manual cleanup: $final_bundle"
      sync -f -- "$BACKUP_DIR" \
        || warn "backup directory could not be synced after cleanup"
    fi
    cleanup_partial_backup
    return 1
  fi
  BACKUP_PATH="$final_bundle" TXN_BACKUP="$final_bundle" \
    TXN_TEMP_BACKUP="" TXN_ROLLBACK_IMAGE=""
}

create_administrator() {
  local password="" confirmation="" password_mode password_owner
  if [[ -n "$ADMIN_PASSWORD_FILE" ]]; then
    validate_absolute_path "OPEN_NODE_ADMIN_PASSWORD_FILE" "$ADMIN_PASSWORD_FILE"
    validate_safe_file "administrator password file" "$ADMIN_PASSWORD_FILE" 1
    password_mode="$(stat -c '%a' -- "$ADMIN_PASSWORD_FILE")"
    password_owner="$(stat -c '%u' -- "$ADMIN_PASSWORD_FILE")"
    [[ "$password_owner" == "0" && $((8#$password_mode & 077)) -eq 0 ]] \
      || die "administrator password file must be root-owned without group/other permissions"
    IFS= read -r password < "$ADMIN_PASSWORD_FILE" || true
  elif [[ -r /dev/tty && ( -t 1 || -t 2 ) ]]; then
    printf 'Administrator username [%s]: ' "$ADMIN_USERNAME" > /dev/tty
    IFS= read -r confirmation < /dev/tty || true
    [[ -z "$confirmation" ]] || ADMIN_USERNAME="$confirmation"
    [[ "$ADMIN_USERNAME" =~ ^[^[:cntrl:]]{1,64}$ ]] || die "invalid administrator username"
    printf 'Administrator password (12+ characters): ' > /dev/tty
    IFS= read -r -s password < /dev/tty || true
    printf '\nConfirm administrator password: ' > /dev/tty
    IFS= read -r -s confirmation < /dev/tty || true
    printf '\n' > /dev/tty
    [[ "$password" == "$confirmation" ]] || die "administrator passwords do not match"
  else
    [[ "$CREATE_ADMIN" == "1" ]] \
      && die "OPEN_NODE_CREATE_ADMIN=1 requires a root-only OPEN_NODE_ADMIN_PASSWORD_FILE"
    log "administrator creation skipped; rerun this script with create-admin"
    return 0
  fi
  [[ ${#password} -ge 12 && ${#password} -le 1024 ]] \
    || die "administrator password must contain 12-1024 characters"
  printf '%s\n' "$password" | compose_with "$INSTALL_DIR" "$ENV_FILE" exec -T open-node \
    open-node-admin create --username "$ADMIN_USERNAME" --password-stdin
  unset password confirmation
}

container_state() {
  local container_id
  container_id="$(compose_with "$INSTALL_DIR" "$ENV_FILE" ps -a -q open-node)" \
    || die "could not inspect the current container"
  if [[ -z "$container_id" ]]; then
    printf 'absent\n'
  elif [[ "$(docker inspect --format '{{.State.Running}}' "$container_id")" == "true" ]]; then
    printf 'running\n'
  else
    printf 'stopped\n'
  fi
}

log_success() {
  log "deployment is healthy"
  log "local URL: http://127.0.0.1:$(read_env_value OPEN_NODE_HTTP_PORT)"
  log "configuration: $ENV_FILE"
  if [[ "$(read_env_value OPEN_NODE_BIND_ADDRESS)" == "0.0.0.0" ]]; then
    warn "the panel is exposed over plain HTTP by explicit operator opt-in"
  fi
  [[ "$CREATE_ADMIN" != "0" ]] \
    || log "create an administrator by running this installer with create-admin"
}

install_fresh() {
  local candidate_env transaction_id image_tag image_reference
  require_fresh_project
  [[ ! -e "$INSTALL_DIR" && ! -L "$INSTALL_DIR" ]] \
    || die "install directory already exists without an installer manifest"
  prepare_fresh_candidate
  transaction_id="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
  image_tag="source-$CANDIDATE_REVISION-$transaction_id"
  image_reference="$IMAGE_REPOSITORY:$image_tag"
  candidate_env="$(mktemp "$CONFIG_DIR/.open-node.env.candidate.XXXXXX")"
  TXN_CANDIDATE_ENV="$candidate_env"
  TXN_IMAGE_TAG="$image_tag"
  create_candidate_environment "$CANDIDATE_SOURCE" "$candidate_env"
  set_candidate_identity "$candidate_env" "$CANDIDATE_REVISION" "$image_tag"
  if ! validate_candidate_compose "$CANDIDATE_SOURCE" "$candidate_env" "$image_reference" \
    || ! build_candidate_image "$CANDIDATE_SOURCE" "$candidate_env" "$image_reference"; then
    cleanup_candidate_artifacts || warn "candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "candidate build or Compose validation failed"
  fi
  TXN_PHASE="candidate-built"
  write_recovery_marker "fresh-candidate-starting" "$CANDIDATE_REVISION" "$image_tag" "none"
  TXN_PHASE="candidate-starting"
  TXN_CANDIDATE_ACTIVATED=1
  log "starting Open Node"
  if ! compose_with "$CANDIDATE_SOURCE" "$candidate_env" up -d --no-build --pull never \
    --no-deps open-node \
    || ! wait_for_health "$CANDIDATE_SOURCE" "$candidate_env" "$CANDIDATE_IMAGE_ID"; then
    if ! quarantine_candidate; then
      mark_containment_failure "fresh-candidate-failed"
      die "fresh deployment failed and candidate containment could not be verified"
    fi
    write_recovery_marker "fresh-candidate-failed" "$CANDIDATE_REVISION" "$image_tag" "none"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "fresh deployment failed; no installation was committed"
  fi
  TXN_PHASE="candidate-healthy"
  if ! mv -T -- "$CANDIDATE_SOURCE" "$INSTALL_DIR"; then
    if ! quarantine_candidate; then
      mark_containment_failure "fresh-source-commit-failed"
      die "source commit failed and candidate containment could not be verified"
    fi
    write_recovery_marker "fresh-source-commit-failed" "$CANDIDATE_REVISION" "$image_tag" "none"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "healthy candidate source could not be committed"
  fi
  CANDIDATE_SOURCE="$INSTALL_DIR"
  TXN_KIND="fresh-promoted"
  TXN_PHASE="source-committed"
  sync -f -- "$INSTALL_DIR" || die "could not durably sync the installed source"
  sync -f -- "$(dirname -- "$INSTALL_DIR")" || die "could not durably sync the install parent"
  if ! mv -f -- "$candidate_env" "$ENV_FILE"; then
    if ! quarantine_candidate; then
      mark_containment_failure "fresh-environment-commit-failed"
      die "environment commit failed and candidate containment could not be verified"
    fi
    write_recovery_marker "fresh-environment-commit-failed" "$CANDIDATE_REVISION" "$image_tag" "none"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "healthy candidate environment could not be committed"
  fi
  TXN_CANDIDATE_ENV="$ENV_FILE"
  TXN_PHASE="environment-committed"
  sync_file_and_parent "$ENV_FILE"
  if ! compose_with "$INSTALL_DIR" "$ENV_FILE" up -d --no-build --pull never \
    --no-deps --force-recreate open-node \
    || ! wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$CANDIDATE_IMAGE_ID"; then
    if ! quarantine_candidate; then
      mark_containment_failure "fresh-canonicalization-failed"
      die "canonical deployment failed and candidate containment could not be verified"
    fi
    write_recovery_marker "fresh-canonicalization-failed" "$CANDIDATE_REVISION" "$image_tag" "none"
    TXN_PHASE="handled"
    die "candidate could not be rebound to the committed source and environment"
  fi
  verify_checkout "$CANDIDATE_REVISION"
  write_recovery_marker "fresh-candidate-healthy-committing" "$CANDIDATE_REVISION" "$image_tag" "none"
  write_manifest "$CANDIDATE_REVISION" "$image_tag" "$CANDIDATE_IMAGE_ID"
  verify_checkout "$CANDIDATE_REVISION"
  verify_active_identity
  wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$CANDIDATE_IMAGE_ID" \
    || die "deployment failed its post-commit stability check"
  clear_recovery_marker
  TXN_PHASE="idle"
  [[ "$CREATE_ADMIN" == "0" ]] || create_administrator
  log_success
}

reinstall_existing() {
  local revision image_tag image_id state
  require_manifest
  require_environment_file
  revision="$(read_manifest_value DEPLOYED_REVISION)"
  image_tag="$(read_manifest_value DEPLOYED_IMAGE_TAG)"
  image_id="$(read_manifest_value DEPLOYED_IMAGE_ID)"
  verify_checkout "$revision"
  verify_active_identity 1
  state="$(container_state)"
  if [[ "$state" == "running" ]]; then
    wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$image_id" \
      || die "deployment is unhealthy"
    log_success
    return
  fi
  CANDIDATE_SOURCE="$INSTALL_DIR"
  CANDIDATE_REVISION="$revision"
  CANDIDATE_IMAGE_ID="$image_id"
  TXN_CANDIDATE_ENV="$ENV_FILE"
  TXN_IMAGE_TAG="$image_tag"
  TXN_BACKUP="none"
  TXN_KIND="reinstall"
  TXN_PHASE="candidate-starting"
  write_recovery_marker "reinstall-starting" "$revision" "$image_tag" "none"
  TXN_CANDIDATE_ACTIVATED=1
  if ! compose_with "$INSTALL_DIR" "$ENV_FILE" up -d --no-build --pull never \
    --no-deps open-node \
    || ! wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$image_id"; then
    if ! quarantine_candidate; then
      mark_containment_failure "reinstall-failed"
      die "reinstall failed and candidate containment could not be verified"
    fi
    write_recovery_marker "reinstall-failed" "$revision" "$image_tag" "none"
    TXN_PHASE="handled"
    die "deployment is unhealthy and was quarantined; data was preserved"
  fi
  TXN_CANDIDATE_ACTIVATED=0
  clear_recovery_marker
  TXN_PHASE="idle"
  log_success
}

update_existing() {
  local candidate_env transaction_id image_tag image_reference
  local old_revision old_tag old_image_id old_state
  require_manifest
  require_environment_file
  old_revision="$(read_manifest_value DEPLOYED_REVISION)"
  old_tag="$(read_manifest_value DEPLOYED_IMAGE_TAG)"
  old_image_id="$(read_manifest_value DEPLOYED_IMAGE_ID)"
  verify_checkout "$old_revision"
  verify_active_identity 1
  prepare_update_candidate
  if [[ "$CANDIDATE_UNCHANGED" -eq 1 ]]; then
    log "already at requested revision $CANDIDATE_REVISION; no image was rebuilt"
    if [[ "$(container_state)" == "running" ]]; then
      wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$old_image_id" \
        || die "current deployment is unhealthy"
    fi
    return
  fi
  transaction_id="$(date -u +%Y%m%dT%H%M%SZ)-$$-$RANDOM"
  image_tag="source-$CANDIDATE_REVISION-$transaction_id"
  image_reference="$IMAGE_REPOSITORY:$image_tag"
  candidate_env="$(mktemp "$CONFIG_DIR/.open-node.env.candidate.XXXXXX")"
  TXN_CANDIDATE_ENV="$candidate_env"
  TXN_IMAGE_TAG="$image_tag"
  create_candidate_environment "$CANDIDATE_SOURCE" "$candidate_env" "$ENV_FILE"
  set_candidate_identity "$candidate_env" "$CANDIDATE_REVISION" "$image_tag"
  if ! validate_candidate_compose "$CANDIDATE_SOURCE" "$candidate_env" "$image_reference" \
    || ! build_candidate_image "$CANDIDATE_SOURCE" "$candidate_env" "$image_reference"; then
    cleanup_candidate_artifacts || warn "candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "candidate build or Compose validation failed"
  fi
  TXN_PHASE="candidate-built"
  old_state="$(container_state)"
  write_recovery_marker "candidate-built" "$CANDIDATE_REVISION" "$image_tag" "pending"
  TXN_PHASE="old-stopping"
  if [[ "$old_state" == "running" ]]; then
    compose_with "$INSTALL_DIR" "$ENV_FILE" stop open-node
  fi
  TXN_PHASE="old-stopped"
  write_recovery_marker "old-stopped" "$CANDIDATE_REVISION" "$image_tag" "pending"
  TXN_PHASE="backing-up"
  if ! backup_stopped_volume "$INSTALL_DIR" "$ENV_FILE" "$old_revision" "$old_tag" "$old_image_id" "$transaction_id"; then
    cleanup_partial_backup
    if [[ "$old_state" == "running" ]]; then
      compose_with "$INSTALL_DIR" "$ENV_FILE" start open-node \
        || die "backup failed and the previous container could not be restarted"
      wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$old_image_id" \
        || die "backup failed and the previous deployment did not recover"
    fi
    clear_recovery_marker
    cleanup_candidate_artifacts || warn "candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "backup failed; no candidate was started"
  fi
  TXN_PHASE="backup-complete"
  write_recovery_marker "backup-complete" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
  log "starting candidate Open Node image"
  TXN_PHASE="candidate-starting"
  TXN_CANDIDATE_ACTIVATED=1
  if ! compose_with "$CANDIDATE_SOURCE" "$candidate_env" up -d --no-build --pull never \
    --no-deps open-node \
    || ! wait_for_health "$CANDIDATE_SOURCE" "$candidate_env" "$CANDIDATE_IMAGE_ID"; then
    if ! quarantine_candidate; then
      mark_containment_failure "candidate-failed-recovery-required"
      die "candidate failed and containment could not be verified; source and environment were preserved"
    fi
    write_recovery_marker "candidate-failed-recovery-required" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
    warn "old source, environment, manifest, and image identity remain recorded"
    warn "the volume may be migrated; do not restart the old image against it"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "candidate failed; restore and verify $BACKUP_PATH in an isolated project"
  fi
  TXN_PHASE="candidate-healthy"
  write_recovery_marker "candidate-healthy-committing" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
  if ! git -C "$INSTALL_DIR" merge --ff-only --no-edit "$CANDIDATE_REVISION"; then
    if ! quarantine_candidate; then
      mark_containment_failure "source-commit-failed-recovery-required"
      die "source commit failed and candidate containment could not be verified"
    fi
    write_recovery_marker "source-commit-failed-recovery-required" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "candidate was stopped because source commit failed; restore $BACKUP_PATH"
  fi
  TXN_PHASE="source-committed"
  sync -f -- "$INSTALL_DIR" || die "could not durably sync the updated source"
  sync -f -- "$(dirname -- "$INSTALL_DIR")" || die "could not durably sync the install parent"
  if ! mv -f -- "$candidate_env" "$ENV_FILE"; then
    if ! quarantine_candidate; then
      mark_containment_failure "environment-commit-failed-recovery-required"
      die "environment commit failed and candidate containment could not be verified"
    fi
    write_recovery_marker "environment-commit-failed-recovery-required" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "candidate was stopped because environment commit failed; restore $BACKUP_PATH"
  fi
  TXN_CANDIDATE_ENV="$ENV_FILE"
  TXN_PHASE="environment-committed"
  sync_file_and_parent "$ENV_FILE"
  if ! compose_with "$INSTALL_DIR" "$ENV_FILE" up -d --no-build --pull never \
    --no-deps --force-recreate open-node \
    || ! wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$CANDIDATE_IMAGE_ID"; then
    if ! quarantine_candidate; then
      mark_containment_failure "canonicalization-failed-recovery-required"
      die "canonical deployment failed and candidate containment could not be verified"
    fi
    write_recovery_marker "canonicalization-failed-recovery-required" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
    cleanup_candidate_artifacts || warn "contained candidate artifacts require manual cleanup"
    TXN_PHASE="handled"
    die "candidate could not be rebound to the committed source and environment; restore $BACKUP_PATH"
  fi
  verify_checkout "$CANDIDATE_REVISION"
  write_recovery_marker "candidate-canonical-healthy-committing" "$CANDIDATE_REVISION" "$image_tag" "$BACKUP_PATH"
  write_manifest "$CANDIDATE_REVISION" "$image_tag" "$CANDIDATE_IMAGE_ID"
  verify_checkout "$CANDIDATE_REVISION"
  verify_active_identity
  wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$CANDIDATE_IMAGE_ID" \
    || die "deployment failed its post-commit stability check"
  clear_recovery_marker
  remove_update_worktree "$CANDIDATE_SOURCE" \
    || warn "committed candidate worktree requires manual cleanup"
  CANDIDATE_SOURCE=""
  TXN_PHASE="idle"
  log_success
  log "pre-update backup: $BACKUP_PATH"
}

show_status() {
  local revision image_id state
  if [[ -e "$RECOVERY_FILE" || -L "$RECOVERY_FILE" ]]; then
    validate_safe_file "recovery marker" "$RECOVERY_FILE" 1
    warn "deployment recovery is required"
    sed 's/^/[open-node]   /' "$RECOVERY_FILE" >&2
  fi
  require_manifest
  require_environment_file
  revision="$(read_manifest_value DEPLOYED_REVISION)"
  image_id="$(read_manifest_value DEPLOYED_IMAGE_ID)"
  verify_checkout "$revision"
  verify_active_identity 1
  verify_volume
  state="$(container_state)"
  compose_with "$INSTALL_DIR" "$ENV_FILE" ps
  case "$state" in
    running)
      wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$image_id" || die "health check failed"
      log "health check passed"
      ;;
    stopped)
      log "deployment container is stopped"
      ;;
    absent)
      log "deployment container is absent; managed data is preserved"
      ;;
  esac
}

uninstall_preserving_data() {
  require_manifest
  require_environment_file
  verify_checkout "$(read_manifest_value DEPLOYED_REVISION)"
  verify_active_identity
  compose_with "$INSTALL_DIR" "$ENV_FILE" down --remove-orphans
  project_runtime_is_absent || die "uninstall could not verify complete runtime removal"
  verify_volume
  log "containers and project network were removed"
  log "data volume, source, configuration, installer state, and backups were preserved"
}

create_admin_action() {
  require_no_recovery
  require_manifest
  require_environment_file
  verify_checkout "$(read_manifest_value DEPLOYED_REVISION)"
  verify_active_identity
  verify_volume
  wait_for_health "$INSTALL_DIR" "$ENV_FILE" "$(read_manifest_value DEPLOYED_IMAGE_ID)" \
    || die "deployment must be healthy before creating an administrator"
  CREATE_ADMIN=1
  create_administrator
}

main() {
  require_root
  load_manifest_defaults
  validate_inputs
  if [[ "$ACTION" == "install" && ! -e "$CONFIG_DIR" && ! -L "$CONFIG_DIR" ]]; then
    ensure_private_directory "OPEN_NODE_CONFIG_DIR" "$CONFIG_DIR"
  else
    validate_safe_directory "OPEN_NODE_CONFIG_DIR" "$CONFIG_DIR" 1
  fi
  acquire_lock
  ensure_dependencies
  acquire_global_lock
  case "$ACTION" in
    install)
      require_no_recovery
      if [[ -e "$MANIFEST_FILE" || -L "$MANIFEST_FILE" ]]; then
        reinstall_existing
      else
        [[ ! -e "$ENV_FILE" && ! -L "$ENV_FILE" ]] \
          || die "environment exists without an installer manifest; explicit adoption is required"
        install_fresh
      fi
      ;;
    update)
      require_no_recovery
      update_existing
      ;;
    status) show_status ;;
    uninstall) uninstall_preserving_data ;;
    create-admin) create_admin_action ;;
  esac
}

main "$@"
