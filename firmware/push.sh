#!/usr/bin/env bash
# Push the fm_status app (and optionally its config) onto a Cardputer running
# UiFlow2 + the Claude Buddy launcher, over USB-serial. No reflash, no button
# sequence, and nothing else on /flash is touched (main.py, buddy_*.py and NVS
# stay as they are).
#
#   firmware/push.sh [--port DEV] [--app-only | --cfg-only] [--no-reset] [--tool mpremote|buddy]
#
# Files written on the device:
#   firmware/apps/fm_status.py     -> /flash/apps/fm_status.py
#   firmware/fm_status_cfg.json    -> /flash/fm_status_cfg.json   (gitignored; holds Wi-Fi + token)
#
# Pushers (first available wins unless --tool is given):
#   mpremote   pipx install mpremote  (or: python3 -m pip install --user mpremote)
#   buddy      moremas/build-with-claude buddy/scripts/push.py; set BUDDY_REPO to the
#              clone and have pyserial installed. Proven against the Buddy bundle's REPL.
#
# Port: --port, else $FMS_PORT, else the single /dev/serial/by-id/usb-M5Stack_Cardputer-ADV*
# link. Refuses to guess when zero or several Cardputers are attached.
set -euo pipefail

FW_DIR=$(cd "$(dirname "$0")" && pwd -P)
port=${FMS_PORT:-}
tool=""
push_app=1
push_cfg=1
reset=1

usage() { sed -n '2,20p' "$0" | sed 's/^# \{0,1\}//'; }

while [ $# -gt 0 ]; do
  case "$1" in
    --port) port=$2; shift 2 ;;
    --tool) tool=$2; shift 2 ;;
    --app-only) push_cfg=0; shift ;;
    --cfg-only) push_app=0; shift ;;
    --no-reset) reset=0; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "push.sh: unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$port" ]; then
  shopt -s nullglob
  links=(/dev/serial/by-id/usb-M5Stack_Cardputer-ADV*)
  shopt -u nullglob
  if [ "${#links[@]}" -ne 1 ]; then
    echo "push.sh: found ${#links[@]} Cardputer serial links; pass --port explicitly" >&2
    exit 2
  fi
  port=${links[0]}
fi
if [ ! -r "$port" ] || [ ! -w "$port" ]; then
  echo "push.sh: no read/write access to $port" >&2
  echo "  one-time fix: sudo usermod -aG uucp \"$USER\"   (then log out and back in)" >&2
  exit 2
fi

files=()
[ "$push_app" = 1 ] && files+=("apps/fm_status.py")
if [ "$push_cfg" = 1 ]; then
  if [ -f "$FW_DIR/fm_status_cfg.json" ]; then
    python3 -c 'import json,sys; json.load(open(sys.argv[1]))' "$FW_DIR/fm_status_cfg.json" \
      || { echo "push.sh: fm_status_cfg.json is not valid JSON" >&2; exit 2; }
    files+=("fm_status_cfg.json")
  elif [ "$push_app" = 0 ]; then
    echo "push.sh: --cfg-only but $FW_DIR/fm_status_cfg.json does not exist" >&2
    exit 2
  else
    echo "push.sh: no fm_status_cfg.json yet; pushing the app only" >&2
    echo "  (cp firmware/fm_status_cfg.example.json firmware/fm_status_cfg.json and edit it)" >&2
  fi
fi
[ "${#files[@]}" -gt 0 ] || { echo "push.sh: nothing to push" >&2; exit 2; }

if [ -z "$tool" ]; then
  if command -v mpremote >/dev/null 2>&1; then
    tool=mpremote
  elif [ -n "${BUDDY_REPO:-}" ] && [ -f "$BUDDY_REPO/buddy/scripts/push.py" ]; then
    tool=buddy
  else
    echo "push.sh: need mpremote on PATH, or BUDDY_REPO=<build-with-claude clone>" >&2
    exit 2
  fi
fi

echo "push.sh: ${files[*]} -> $port via $tool" >&2
case "$tool" in
  mpremote)
    args=(connect "$port")
    for f in "${files[@]}"; do
      args+=(fs cp "$FW_DIR/$f" ":/flash/$f" +)
    done
    if [ "$reset" = 1 ]; then
      args+=(reset)
    else
      unset 'args[${#args[@]}-1]'
    fi
    mpremote "${args[@]}"
    ;;
  buddy)
    extra=()
    [ "$reset" = 1 ] || extra+=(--no-reset)
    python3 "$BUDDY_REPO/buddy/scripts/push.py" --port "$port" --src "$FW_DIR" \
      --files "${files[@]}" "${extra[@]}"
    ;;
  *) echo "push.sh: unknown --tool $tool" >&2; exit 2 ;;
esac
echo "push.sh: done. Pick fm_status in the launcher (; / . to move, Enter to launch)." >&2
