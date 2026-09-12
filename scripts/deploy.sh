#!/usr/bin/env bash
# From the workstation: push code to the Pi and restart the service.
#   PI=pi@stereo-tv.local scripts/deploy.sh
set -euo pipefail
PI=${PI:?set PI=user@host}
SSH=${SSH:-ssh}
APPDIR=${APPDIR:-'~/stereo-tv'}
cd "$(dirname "$0")/.."
rsync -av --delete -e "$SSH" --exclude .git --exclude __pycache__ --exclude .venv --exclude '*.pyc' ./ "$PI:$APPDIR/"
# no-sudo restart: SIGKILL makes systemd (Restart=always) relaunch with the new code
$SSH "$PI" 'kill -9 $(systemctl show -p MainPID --value stereo-tv); sleep 6; journalctl -u stereo-tv -n 20 --no-pager -o cat'
