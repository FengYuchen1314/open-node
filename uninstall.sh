#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
umask 077

readonly DEFAULT_INSTALL_DIR="/opt/open-node"
readonly DEFAULT_CONFIG_DIR="/etc/open-node"
readonly DEFAULT_BACKUP_DIR="/var/backups/open-node"

INSTALL_DIR="${OPEN_NODE_INSTALL_DIR:-$DEFAULT_INSTALL_DIR}"
CONFIG_DIR="${OPEN_NODE_CONFIG_DIR:-$DEFAULT_CONFIG_DIR}"
BACKUP_DIR="${OPEN_NODE_BACKUP_DIR:-$DEFAULT_BACKUP_DIR}"
INSTALLER="$INSTALL_DIR/install.sh"

die() {
  printf '[open-node-uninstall] ERROR: %s\n' "$*" >&2
  exit 1
}

validate_target() {
  local label="$1" value="$2" canonical current
  [[ "$value" == /* && "$value" != *[[:space:]]* ]] \
    || die "$label 必须是无空白的绝对路径"
  canonical="$(realpath -m -- "$value")" || die "无法解析 $label"
  [[ "$canonical" == "$value" ]] || die "$label 必须是规范路径"
  case "$value" in
    /|/etc|/opt|/root|/usr|/var|/var/backups)
      die "$label 范围过大，拒绝继续"
      ;;
  esac
  current="$value"
  while [[ "$current" != "/" ]]; do
    if [[ -e "$current" || -L "$current" ]]; then
      [[ ! -L "$current" ]] || die "$label 不能包含符号链接"
    fi
    current="$(dirname -- "$current")"
  done
}

[[ "${EUID:-$(id -u)}" -eq 0 ]] || die "请使用 sudo 或 root 运行"
command -v realpath >/dev/null 2>&1 || die "缺少 GNU realpath"
[[ -t 0 && -t 1 && -t 2 ]] || die "卸载必须在交互式终端中运行"

validate_target "安装目录" "$INSTALL_DIR"
validate_target "配置目录" "$CONFIG_DIR"
validate_target "备份目录" "$BACKUP_DIR"
[[ -f "$INSTALLER" && ! -L "$INSTALLER" ]] || die "未找到受管安装器：$INSTALLER"
[[ "$(stat -c '%u' -- "$INSTALLER")" == "0" ]] || die "受管安装器不是 root 所有"
installer_mode="$(stat -c '%a' -- "$INSTALLER")" || die "无法检查受管安装器"
(( (8#$installer_mode & 022) == 0 )) || die "受管安装器可被组或其他用户修改"

printf '%s\n' \
  '即将卸载 Open Node 面板。直接回车默认为彻底清除。' \
  "  源码：$INSTALL_DIR" \
  "  配置和安装清单：$CONFIG_DIR" \
  "  备份：$BACKUP_DIR" \
  '  应用/PostgreSQL/Caddy Docker 数据卷' \
  '  面板容器、网关容器、项目网络和更新状态'
printf '是否彻底清除以上数据？[Y/n] '
IFS= read -r answer

case "${answer,,}" in
  ""|y|yes)
    printf '[open-node-uninstall] 已确认彻底清除，正在等待全部资源删除完成。\n'
    OPEN_NODE_PURGE_CONFIRMED=YES exec bash "$INSTALLER" purge
    ;;
  n|no)
    printf '[open-node-uninstall] 将停止并移除运行资源，但保留数据。\n'
    exec bash "$INSTALLER" uninstall
    ;;
  *)
    die "输入无效，未执行任何卸载操作"
    ;;
esac
