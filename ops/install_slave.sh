#!/usr/bin/env bash
set -Eeuo pipefail

DRY_RUN=0
ROOT=/
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR=
COMMITTED=0
WAS_ENABLED=unknown
WAS_ACTIVE=unknown

while (($#)); do
  case "$1" in
    --dry-run) DRY_RUN=1 ;;
    --root) ROOT="${2:?missing root}"; shift ;;
    *) echo "unknown argument: $1" >&2; exit 2 ;;
  esac
  shift
done

dest() { printf '%s%s' "${ROOT%/}" "$1"; }
say() { printf '%s\n' "$*"; }
run() {
  if ((DRY_RUN)); then
    printf '+ %s' "$1"
    shift
    for arg in "$@"; do printf ' [%s]' "$arg"; done
    printf '\n'
  else
    "$@"
  fi
}

rollback() {
  rc=$?
  ((COMMITTED)) && return "$rc"
  say "ROLLBACK: restore prior slave release and service state"
  if ((!DRY_RUN)) && [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
    rm -rf "$(dest /opt/low-altitude-iot/current)"
    [[ -e "$(dest /opt/low-altitude-iot/previous)" ]] &&
      mv "$(dest /opt/low-altitude-iot/previous)" "$(dest /opt/low-altitude-iot/current)"
    for rel in etc/systemd/system/low-altitude-slave.service usr/local/lib/low-altitude-iot/health_slave.sh; do
      [[ -e "$BACKUP_DIR/$rel" ]] || continue
      install -d -m 0755 "$(dirname "$(dest /$rel)")"
      cp -a "$BACKUP_DIR/$rel" "$(dest /$rel)"
    done
    systemctl daemon-reload || true
    [[ "$WAS_ENABLED" == enabled ]] && systemctl enable low-altitude-slave.service || systemctl disable low-altitude-slave.service || true
    [[ "$WAS_ACTIVE" == active ]] && systemctl restart low-altitude-slave.service || systemctl stop low-altitude-slave.service || true
  fi
  return "$rc"
}
trap rollback ERR INT TERM

say "PREFLIGHT: root, root-owned environment, fixed by-id and Copter heartbeat"
if ((!DRY_RUN)); then
  [[ "$(id -u)" == 0 ]] || { echo "installer requires root" >&2; exit 1; }
  env_file="$(dest /etc/low-altitude-iot/slave.env)"
  [[ -f "$env_file" && "$(stat -c %u "$env_file")" == 0 && "$(stat -c %a "$env_file")" == 600 ]] || {
    echo "requires root-owned mode-0600 $env_file" >&2; exit 1;
  }
  grep -Eq '^SLAVE_FC_DEVICE=/dev/serial/by-id/.+$' "$env_file" || {
    echo "SLAVE_FC_DEVICE=/dev/serial/by-id/... is required" >&2; exit 1;
  }
  set -a; source "$env_file"; set +a
  runuser -u sunrise -- env \
    SLAVE_FC_DEVICE="$SLAVE_FC_DEVICE" \
    SLAVE_HEARTBEAT_TIMEOUT="${SLAVE_HEARTBEAT_TIMEOUT:-5}" \
    bash "$SOURCE_DIR/ops/health_slave.sh" --preflight
  systemctl is-enabled --quiet low-altitude-slave.service && WAS_ENABLED=enabled || WAS_ENABLED=disabled
  systemctl is-active --quiet low-altitude-slave.service && WAS_ACTIVE=active || WAS_ACTIVE=inactive
fi

BACKUP_DIR="$(dest /var/backups/low-altitude-iot)/slave-$(date +%Y%m%d%H%M%S)"
say "BACKUP: current release and slave service -> $BACKUP_DIR"
say "ROLLBACK: armed until post-install health succeeds"
if ((!DRY_RUN)); then
  install -d -m 0700 "$BACKUP_DIR"
  for path in "$(dest /etc/systemd/system/low-altitude-slave.service)" "$(dest /usr/local/lib/low-altitude-iot/health_slave.sh)"; do
    [[ -e "$path" ]] && cp -a --parents "$path" "$BACKUP_DIR"
  done
fi

say "ATOMIC INSTALL: stage release and replace current symlink target"
stage="$(dest /opt/low-altitude-iot/.slave-stage.$$.XXXXXX)"
if ((DRY_RUN)); then
  run mktemp -d "$stage"
else
  install -d -m 0755 "$(dest /opt/low-altitude-iot)"
  stage="$(mktemp -d "$stage")"
  cp -a "$SOURCE_DIR/." "$stage/"
  rm -rf "$(dest /opt/low-altitude-iot/previous)"
  [[ -e "$(dest /opt/low-altitude-iot/current)" ]] && mv "$(dest /opt/low-altitude-iot/current)" "$(dest /opt/low-altitude-iot/previous)"
  mv -f "$stage" "$(dest /opt/low-altitude-iot/current)"
fi

run install -d -m 0755 "$(dest /usr/local/lib/low-altitude-iot)" "$(dest /etc/systemd/system)"
run install -m 0755 "$SOURCE_DIR/ops/health_slave.sh" "$(dest /usr/local/lib/low-altitude-iot/health_slave.sh)"
run install -m 0644 "$SOURCE_DIR/ops/systemd/low-altitude-slave.service" "$(dest /etc/systemd/system/low-altitude-slave.service)"
run systemctl daemon-reload
run systemctl enable low-altitude-slave.service
run systemctl restart low-altitude-slave.service

say "HEALTH: service, UDP listener and fixed serial identity"
if ((DRY_RUN)); then run "$SOURCE_DIR/ops/health_slave.sh" --dry-run; else "$(dest /usr/local/lib/low-altitude-iot/health_slave.sh)"; fi
COMMITTED=1
trap - ERR INT TERM
say "Installation committed. Only the slave service was restarted."
