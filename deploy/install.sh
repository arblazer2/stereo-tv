#!/usr/bin/env bash
# Run ON THE PI as the user that will run the display (any user with sudo), from the repo directory.
# Installs apt deps, a venv, group membership, and the systemd units (user/paths filled in from $USER/$PWD).
set -euo pipefail
cd "$(dirname "$0")/.."
APPDIR=$(pwd)
RUNUSER=${RUNUSER:-$USER}

sudo apt-get update
sudo apt-get install -y python3-pygame python3-numpy python3-requests python3-venv fonts-dejavu-core \
    libegl1 libegl-mesa0 libgles2 libgl1-mesa-dri \
    alsa-utils ffmpeg

[ -d .venv ] || python3 -m venv --system-site-packages .venv
.venv/bin/python -c "import pygame, numpy, requests; print('pygame', pygame.version.ver)"
.venv/bin/pip install -q --upgrade "shazamio>=0.8" "audioop-lts; python_version>='3.13'" pyserial esptool \
    && .venv/bin/python -c "import shazamio; print('shazamio ok')"

# groups needed for kmsdrm, evdev keyboard, ALSA capture and the USB serial remote
sudo usermod -aG video,render,input,tty,audio,dialout "$RUNUSER"

mkdir -p ~/.config/stereo-tv ~/.local/share/stereo-tv/covers

for u in stereo-tv.service stereo-tv-sync.service; do
    sed -e "s|__APPDIR__|$APPDIR|g" -e "s|__USER__|$RUNUSER|g" "deploy/$u" | sudo tee "/etc/systemd/system/$u" >/dev/null
done
sudo cp deploy/stereo-tv-sync.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable stereo-tv
sudo systemctl enable --now stereo-tv-sync.timer

echo
echo "Next:"
echo "  1. .venv/bin/python -m stereotv.setup        # Discogs token, location, audio device, weather"
echo "  2. sudo systemctl start stereo-tv && journalctl -u stereo-tv -f"
echo "  3. phone page: http://$(hostname -I | cut -d' ' -f1):8080/"
