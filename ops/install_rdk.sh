#!/usr/bin/env bash
set -Eeuo pipefail

DRY_RUN=0
ENABLE_DEMO=0
ROOT=/
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR=
COMMITTED=0
BASE_WAS_ENABLED=unknown
BASE_WAS_ACTIVE=unknown
AIRCRAFT_WAS_ENABLED=unknown
AIRCRAFT_WAS_ACTIVE=unknown
LEGACY_ROVER_WAS_ENABLED=unknown
LEGACY_ROVER_WAS_ACTIVE=unknown

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --enable-demo-network) ENABLE_DEMO=1 ;;
    --root) ROOT="${2:?missing root}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

dest() { printf '%s%s' "${ROOT%/}" "$1"; }
say() { printf '%s\n' "$*"; }
run() { if ((DRY_RUN)); then printf '+'; printf ' %s' "$@"; printf '\n'; else "$@"; fi; }

restore_service_state() {
  local unit=$1 enabled=$2 active=$3
  case "$enabled" in
    enabled) systemctl enable "$unit" || true ;;
    disabled) systemctl disable "$unit" || true ;;
  esac
  case "$active" in
    active) systemctl restart "$unit" || true ;;
    inactive|failed) systemctl stop "$unit" || true ;;
  esac
}

unit_enabled_state() {
  if systemctl is-enabled --quiet "$1" 2>/dev/null; then echo enabled; else echo disabled; fi
}

unit_active_state() {
  if systemctl is-active --quiet "$1" 2>/dev/null; then echo active; else echo inactive; fi
}

restore_backup_tree() {
  local relative source target
  for relative in \
    opt/low-altitude-iot/current \
    usr/local/lib/low-altitude-iot \
    etc/systemd/system/low-altitude-rdk.service \
    etc/systemd/system/low-altitude-rdk-aircraft-network.service; do
    source="$BACKUP_DIR/$relative"
    target="$(dest /$relative)"
    [[ -e "$source" ]] || continue
    rm -rf "$target"
    install -d -m 0755 "$(dirname "$target")"
    cp -a "$source" "$target"
  done
}

rollback() {
  local rc=$?
  ((COMMITTED)) && return "$rc"
  say "ROLLBACK: restoring previous files and service selection"
  if ((!DRY_RUN)) && [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
    rm -rf "$(dest /opt/low-altitude-iot/current)"
    if [[ -e "$(dest /opt/low-altitude-iot/previous)" ]]; then
      mv "$(dest /opt/low-altitude-iot/previous)" \
        "$(dest /opt/low-altitude-iot/current)"
    fi
    restore_backup_tree
    systemctl daemon-reload || true
    restore_service_state low-altitude-rdk.service \
      "$BASE_WAS_ENABLED" "$BASE_WAS_ACTIVE"
    restore_service_state low-altitude-rdk-aircraft-network.service \
      "$AIRCRAFT_WAS_ENABLED" "$AIRCRAFT_WAS_ACTIVE"
    restore_service_state uav-rover-stack.service \
      "$LEGACY_ROVER_WAS_ENABLED" "$LEGACY_ROVER_WAS_ACTIVE"
  fi
  return "$rc"
}
trap rollback ERR INT TERM

say "PREFLIGHT: root, tools, source and shell syntax"
if ((!DRY_RUN)); then
  [[ "$(id -u)" == 0 ]] || { echo "installer requires root" >&2; exit 1; }
  for tool in install mktemp mv cp systemctl bash; do command -v "$tool" >/dev/null; done
  bash -n "$SOURCE_DIR/ops/configure_rdk_network.sh" \
    "$SOURCE_DIR/ops/health_rdk.sh" "$SOURCE_DIR/ops/install_rdk.sh"
  runtime_env="$(dest /etc/low-altitude-iot/rdk.env)"
  [[ -f "$runtime_env" && "$(stat -c %u "$runtime_env")" == 0 &&
    "$(stat -c %a "$runtime_env")" == 600 ]] || {
    echo "runtime requires root-owned mode-0600 $runtime_env" >&2
    exit 1
  }
  grep -Eq '^AIRCRAFT_LINK_PSK=.+$' "$runtime_env" || {
    echo "runtime environment is missing AIRCRAFT_LINK_PSK" >&2
    exit 1
  }
  if ((ENABLE_DEMO)); then
    secret_file="$(dest /etc/low-altitude-iot/rdk-network.env)"
    [[ -f "$secret_file" && "$(stat -c %u "$secret_file")" == 0 &&
      "$(stat -c %a "$secret_file")" == 600 ]] || {
      echo "demo network requires root-owned mode-0600 $secret_file" >&2
      exit 1
    }
  fi
  BASE_WAS_ENABLED="$(unit_enabled_state low-altitude-rdk.service)"
  BASE_WAS_ACTIVE="$(unit_active_state low-altitude-rdk.service)"
  AIRCRAFT_WAS_ENABLED="$(unit_enabled_state low-altitude-rdk-aircraft-network.service)"
  AIRCRAFT_WAS_ACTIVE="$(unit_active_state low-altitude-rdk-aircraft-network.service)"
  LEGACY_ROVER_WAS_ENABLED="$(unit_enabled_state uav-rover-stack.service)"
  LEGACY_ROVER_WAS_ACTIVE="$(unit_active_state uav-rover-stack.service)"
fi

BACKUP_DIR="$(dest /var/backups/low-altitude-iot)/$(date +%Y%m%d%H%M%S)"
say "BACKUP: existing application, operations scripts and systemd units -> $BACKUP_DIR"
if ((!DRY_RUN)); then
  install -d -m 0700 "$BACKUP_DIR"
  for path in \
    "$(dest /opt/low-altitude-iot/current)" \
    "$(dest /usr/local/lib/low-altitude-iot)" \
    "$(dest /etc/systemd/system/low-altitude-rdk.service)" \
    "$(dest /etc/systemd/system/low-altitude-rdk-aircraft-network.service)"; do
    [[ -e "$path" ]] && cp -a --parents "$path" "$BACKUP_DIR"
  done
fi

say "ATOMIC INSTALL: stage with mktemp, then mv -f into place"
stage="$(dest /opt/low-altitude-iot/.stage.$$.XXXXXX)"
if ((DRY_RUN)); then
  run mktemp -d "$stage"
else
  install -d -m 0755 "$(dest /opt/low-altitude-iot)"
  stage="$(mktemp -d "$stage")"
  cp -a "$SOURCE_DIR/." "$stage/"
  rm -rf "$(dest /opt/low-altitude-iot/previous)"
  [[ -e "$(dest /opt/low-altitude-iot/current)" ]] &&
    mv "$(dest /opt/low-altitude-iot/current)" "$(dest /opt/low-altitude-iot/previous)"
  mv -f "$stage" "$(dest /opt/low-altitude-iot/current)"
fi

for script in configure_rdk_network.sh health_rdk.sh; do
  target="$(dest /usr/local/lib/low-altitude-iot)/$script"
  run install -d -m 0755 "$(dirname "$target")"
  if ((DRY_RUN)); then
    run install -m 0755 "$SOURCE_DIR/ops/$script" "$target.tmp"
    run mv -f "$target.tmp" "$target"
  else
    install -m 0755 "$SOURCE_DIR/ops/$script" "$target.tmp"
    mv -f "$target.tmp" "$target"
  fi
done

for unit in low-altitude-rdk.service low-altitude-rdk-aircraft-network.service; do
  target="$(dest /etc/systemd/system)/$unit"
  run install -d -m 0755 "$(dirname "$target")"
  if ((DRY_RUN)); then
    run install -m 0644 "$SOURCE_DIR/ops/systemd/$unit" "$target.tmp"
    run mv -f "$target.tmp" "$target"
  else
    install -m 0644 "$SOURCE_DIR/ops/systemd/$unit" "$target.tmp"
    mv -f "$target.tmp" "$target"
  fi
done

run systemctl daemon-reload
run systemctl disable --now uav-rover-stack.service
run systemctl enable low-altitude-rdk.service
run systemctl restart low-altitude-rdk.service
if ((ENABLE_DEMO)); then
  run systemctl enable low-altitude-rdk-aircraft-network.service
  if ((DRY_RUN)); then
    run systemctl start low-altitude-rdk-aircraft-network.service
    say "WARN: aircraft network failed; Rover/Tuya remain active (nonfatal)"
  elif ! systemctl start low-altitude-rdk-aircraft-network.service; then
    say "WARN: aircraft network failed; Rover/Tuya remain active"
  fi
else
  run systemctl disable low-altitude-rdk-aircraft-network.service
fi

say "HEALTH: validate Rover/Tuya and route ownership"
if ((DRY_RUN)); then
  run "$(dest /usr/local/lib/low-altitude-iot/health_rdk.sh)" --dry-run
else
  "$(dest /usr/local/lib/low-altitude-iot/health_rdk.sh)"
fi
COMMITTED=1
trap - ERR INT TERM
say "Installation committed. ROLLBACK is no longer required."
