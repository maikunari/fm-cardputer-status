#!/usr/bin/env bash
# Install the publisher as a systemd --user service.
#
#   host/install-service.sh [--now]
#
# Writes ~/.config/systemd/user/fm-cardputer-status.service pointing at this
# checkout, creates ~/.config/fm-cardputer-status/env from env.example if it
# does not exist yet (an existing env file is never overwritten), and reloads
# systemd. With --now it also enables and starts the service.
set -euo pipefail

HOST_DIR=$(cd "$(dirname "$0")" && pwd -P)
UNIT_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user
ENV_DIR=${XDG_CONFIG_HOME:-$HOME/.config}/fm-cardputer-status
ENV_FILE=$ENV_DIR/env

mkdir -p "$UNIT_DIR" "$ENV_DIR"
sed "s|@HOST_DIR@|$HOST_DIR|g" "$HOST_DIR/systemd/fm-cardputer-status.service" \
  > "$UNIT_DIR/fm-cardputer-status.service"
echo "wrote $UNIT_DIR/fm-cardputer-status.service"

if [ -e "$ENV_FILE" ]; then
  echo "kept existing $ENV_FILE"
else
  umask 077
  sed "s|@FM_HOME@|${FM_HOME:-$HOME/Projects/firstmate}|" "$HOST_DIR/systemd/env.example" > "$ENV_FILE"
  echo "wrote $ENV_FILE (set FMS_TOKEN before exposing it on the LAN)"
fi

systemctl --user daemon-reload
if [ "${1:-}" = "--now" ]; then
  systemctl --user enable --now fm-cardputer-status.service
  systemctl --user --no-pager status fm-cardputer-status.service | head -n 5
else
  echo "next: systemctl --user enable --now fm-cardputer-status.service"
fi
