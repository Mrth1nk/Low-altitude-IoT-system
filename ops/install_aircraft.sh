#!/usr/bin/env bash
set -Eeuo pipefail

DRY_RUN=0
ROOT=/
SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BACKUP_DIR=
COMMITTED=0
legacy_state=
LEGACY_SERVICES=(light-wifi-bridge.service light-ir-precision-land.service)

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
    printf '+'
    printf ' %s' "$@"
    printf '\n'
  else
    "$@"
  fi
}

restore_legacy_services() {
  [[ -n "$legacy_state" && -f "$legacy_state" ]] || return 0
  while read -r service enabled active; do
    [[ "$enabled" == enabled ]] && systemctl enable "$service" || systemctl disable "$service"
    [[ "$active" == active ]] && systemctl start "$service" || systemctl stop "$service"
  done < "$legacy_state"
}

rollback() {
  local rc=$?
  ((COMMITTED)) && return "$rc"
  say "ROLLBACK: stop replacement services, restore files and legacy service state"
  if ((!DRY_RUN)); then
    systemctl disable --now low-altitude-vision.service low-altitude-aircraft.service || true
    rm -rf "$(dest /opt/low-altitude-iot/current)"
    rm -f \
      "$(dest /usr/local/lib/low-altitude-iot/check_serial_roles.sh)" \
      "$(dest /usr/local/lib/low-altitude-iot/health_aircraft.sh)" \
      "$(dest /etc/systemd/system/low-altitude-aircraft.service)" \
      "$(dest /etc/systemd/system/low-altitude-vision.service)" \
      "$(dest /etc/logrotate.d/low-altitude-aircraft)"
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR/root" ]]; then
      # Restore backed-up top-level trees without copying the backup root
      # directory metadata onto `/`.
      for top_level in "$BACKUP_DIR/root"/*; do
        [[ -e "$top_level" ]] && cp -a "$top_level" "$(dest /)"
      done
      systemctl daemon-reload || true
    fi
    restore_legacy_services || true
  fi
  return "$rc"
}
trap rollback ERR INT TERM

say "PREFLIGHT: root, tools, source, shell syntax, serial roles and protected PSK"
say "PREFLIGHT: AIRCRAFT_LINK_PSK=<redacted> from root-only aircraft.env"
if ((!DRY_RUN)); then
  [[ "$(id -u)" == 0 ]] || { echo "installer requires root" >&2; exit 1; }
  for tool in install mktemp mv cp systemctl bash stat grep; do command -v "$tool" >/dev/null; done
  bash -n "$SOURCE_DIR/ops/install_aircraft.sh" \
    "$SOURCE_DIR/ops/check_serial_roles.sh" \
    "$SOURCE_DIR/ops/health_aircraft.sh"
  env_file="$(dest /etc/low-altitude-iot/aircraft.env)"
  [[ -f "$env_file" && "$(stat -c %u "$env_file")" == 0 &&
    "$(stat -c %a "$env_file")" == 600 ]] || {
    echo "aircraft.env must be root-owned mode 0600" >&2
    exit 1
  }
  grep -Eq '^AIRCRAFT_LINK_PSK=.{16,}$' "$env_file" || {
    echo "AIRCRAFT_LINK_PSK missing or too short" >&2
    exit 1
  }
fi

BACKUP_DIR="$(dest /var/backups/low-altitude-iot)/aircraft-$(date +%Y%m%d%H%M%S)-$$"
legacy_state="$BACKUP_DIR/legacy_state"
say "BACKUP: application, units, scripts, logrotate and legacy_state -> $BACKUP_DIR"
if ((!DRY_RUN)); then
  install -d -m 0700 "$BACKUP_DIR/root"
  for path in \
    /opt/low-altitude-iot/current \
    /usr/local/lib/low-altitude-iot/check_serial_roles.sh \
    /usr/local/lib/low-altitude-iot/health_aircraft.sh \
    /etc/systemd/system/low-altitude-aircraft.service \
    /etc/systemd/system/low-altitude-vision.service \
    /etc/logrotate.d/low-altitude-aircraft; do
    full="$(dest "$path")"
    [[ -e "$full" ]] && cp -a --parents "$full" "$BACKUP_DIR/root"
  done
  : > "$legacy_state"
  for service in "${LEGACY_SERVICES[@]}"; do
    enabled=disabled; active=inactive
    systemctl is-enabled --quiet "$service" && enabled=enabled
    systemctl is-active --quiet "$service" && active=active
    printf '%s %s %s\n' "$service" "$enabled" "$active" >> "$legacy_state"
  done
fi

for service in "${LEGACY_SERVICES[@]}"; do
  say "REPLACE: stop and disable legacy $service"
  run systemctl disable --now "$service"
done

say "ATOMIC INSTALL: stage repository and replace current with mv -f"
stage="$(dest /opt/low-altitude-iot/.aircraft-stage.$$.XXXXXX)"
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

for script in check_serial_roles.sh health_aircraft.sh; do
  target="$(dest /usr/local/lib/low-altitude-iot)/$script"
  run install -d -m 0755 "$(dirname "$target")"
  run install -m 0755 "$SOURCE_DIR/ops/$script" "$target.tmp"
  run mv -f "$target.tmp" "$target"
done
for unit in low-altitude-aircraft.service low-altitude-vision.service; do
  target="$(dest /etc/systemd/system)/$unit"
  run install -d -m 0755 "$(dirname "$target")"
  run install -m 0644 "$SOURCE_DIR/ops/systemd/$unit" "$target.tmp"
  run mv -f "$target.tmp" "$target"
done

logrotate="$(dest /etc/logrotate.d/low-altitude-aircraft)"
run install -d -m 0755 "$(dirname "$logrotate")"
if ((DRY_RUN)); then
  say "logrotate: /var/lib/low-altitude-iot/transactions.jsonl size 1M rotate 5"
else
  cat > "$logrotate.tmp" <<'EOF'
/var/lib/low-altitude-iot/transactions.jsonl {
    size 1M
    rotate 5
    compress
    missingok
    notifempty
    copytruncate
}
EOF
  chmod 0644 "$logrotate.tmp"
  mv -f "$logrotate.tmp" "$logrotate"
fi

run systemctl daemon-reload
run systemctl enable --now low-altitude-aircraft.service
run systemctl enable --now low-altitude-vision.service
say "HEALTH: validate atomic state, heartbeat and exclusive process roles"
if ((DRY_RUN)); then
  run "$(dest /usr/local/lib/low-altitude-iot/health_aircraft.sh)" --dry-run
else
  set -a
  # The file is root-owned mode 0600 and was validated during preflight.
  source "$env_file"
  set +a
  HEALTH_ATTEMPTS="${HEALTH_ATTEMPTS:-30}"
  health_ok=0
  for ((attempt = 1; attempt <= HEALTH_ATTEMPTS; attempt++)); do
    if "$(dest /usr/local/lib/low-altitude-iot/health_aircraft.sh)"; then
      health_ok=1
      break
    fi
    sleep 1
  done
  ((health_ok)) || {
    echo "aircraft health did not become ready" >&2
    false
  }
fi

COMMITTED=1
trap - ERR INT TERM
say "Installation committed; ROLLBACK no longer required."
