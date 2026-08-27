#!/usr/bin/env bash
set -euo pipefail

DRY_RUN=0
if [[ "${1:-}" == "--dry-run" ]]; then
  DRY_RUN=1
  shift
fi
ROLE="${1:-all}"
case "$ROLE" in aircraft|vision|all) ;; *) echo "usage: $0 [--dry-run] aircraft|vision|all" >&2; exit 2 ;; esac

if ((DRY_RUN)); then
  cat <<'EOF'
CHECK aircraft FC by-id pattern -> unique /dev/ttyACM*; ambiguous or missing fails
CHECK aircraft link by-id pattern -> unique /dev/ttyUSB*; ambiguous or missing fails
CHECK readlink -f and udevadm info identity for resolved aircraft devices
CHECK vision udevadm info identity for /dev/ttyS9 and /dev/video0
CHECK all resolved role devices are distinct
CHECK lsof and fuser report every device unoccupied; device is busy fails
EOF
  exit 0
fi

for tool in readlink udevadm lsof fuser; do
  command -v "$tool" >/dev/null || {
    echo "required tool missing: $tool" >&2
    exit 1
  }
done

resolve_unique_by_id() {
  local pattern="$1" expected_pattern="$2" label="$3"
  local matches=() candidate resolved
  if [[ -e "$pattern" || -L "$pattern" ]]; then
    matches+=("$pattern")
  else
    while IFS= read -r candidate; do
      [[ -n "$candidate" ]] && matches+=("$candidate")
    done < <(compgen -G "$pattern" || true)
  fi
  if ((${#matches[@]} != 1)); then
    echo "$label by-id identity is ambiguous or missing: ${#matches[@]} matches" >&2
    return 1
  fi
  resolved="$(readlink -f "${matches[0]}")"
  case "$resolved" in
    $expected_pattern) ;;
    *)
      echo "$label resolved to $resolved, expected $expected_pattern" >&2
      return 1
      ;;
  esac
  udevadm info --query=property --name="$resolved" >/dev/null
  printf '%s\n' "$resolved"
}

verify_udev_identity() {
  local device="$1" match="$2" label="$3" properties
  [[ -c "$device" ]] || { echo "$label missing: $device" >&2; return 1; }
  [[ -n "$match" ]] || { echo "$label udev identity match is empty" >&2; return 1; }
  properties="$(udevadm info --query=property --name="$device")"
  grep -Fq -- "$match" <<<"$properties" || {
    echo "$label udev identity mismatch" >&2
    return 1
  }
}

check_busy() {
  local device="$1"
  if lsof -t -- "$device" 2>/dev/null | grep -q . ||
    fuser "$device" >/dev/null 2>&1; then
    echo "device is busy: $device" >&2
    return 1
  fi
}

devices=()
if [[ "$ROLE" == aircraft || "$ROLE" == all ]]; then
  fc="$(resolve_unique_by_id "${AIRCRAFT_FC_BY_ID:?AIRCRAFT_FC_BY_ID required}" "/dev/ttyACM*" "flight controller")"
  link="$(resolve_unique_by_id "${AIRCRAFT_LINK_BY_ID:?AIRCRAFT_LINK_BY_ID required}" "/dev/ttyUSB*" "aircraft link")"
  devices+=("$fc" "$link")
fi
if [[ "$ROLE" == vision || "$ROLE" == all ]]; then
  vision_mav="${VISION_MAV_DEVICE:-/dev/ttyS9}"
  camera="${VISION_CAMERA_DEVICE:-/dev/video0}"
  verify_udev_identity "$vision_mav" "${VISION_MAV_UDEV_MATCH:?VISION_MAV_UDEV_MATCH required}" "vision MAVLink"
  devices+=("$vision_mav")
  if [[ -c "$camera" ]]; then
    verify_udev_identity "$camera" "${VISION_CAMERA_UDEV_MATCH:?VISION_CAMERA_UDEV_MATCH required}" "vision camera"
    devices+=("$camera")
  else
    echo "vision camera missing at startup; runtime will retry: $camera" >&2
  fi
fi

declare -A seen=()
for device in "${devices[@]}"; do
  resolved="$(readlink -f "$device")"
  [[ -z "${seen[$resolved]:-}" ]] || {
    echo "ambiguous device role ownership: $resolved" >&2
    exit 1
  }
  seen["$resolved"]=1
  check_busy "$resolved"
done
