#!/usr/bin/env bash
set -euo pipefail

APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hyperx-alloy-rise-rgb"
BIN_DIR="$HOME/.local/bin"
DESKTOP_FILE="${XDG_DATA_HOME:-$HOME/.local/share}/applications/hyperx-alloy-rise-rgb.desktop"
SERVICE_FILE="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user/hyperx-alloy-rise-rgb.service"

echo "==> Deteniendo y eliminando servicio systemd de usuario"
systemctl --user disable --now hyperx-alloy-rise-rgb.service 2>/dev/null || true
rm -f "$SERVICE_FILE"
systemctl --user daemon-reload 2>/dev/null || true

echo "==> Eliminando aplicacion y accesos directos"
rm -f "$BIN_DIR/hyperx-rise-rgb" "$BIN_DIR/hyperx-rise-gui" "$DESKTOP_FILE"
rm -rf "$APP_DIR"
update-desktop-database "${XDG_DATA_HOME:-$HOME/.local/share}/applications" 2>/dev/null || true

if [ -f /etc/udev/rules.d/60-hyperx-alloy-rise.rules ]; then
  echo "==> Eliminando regla udev (requiere sudo)"
  sudo rm -f /etc/udev/rules.d/60-hyperx-alloy-rise.rules
  sudo udevadm control --reload-rules
fi

echo "Desinstalacion completa. Tu configuracion en ~/.config/hyperx-alloy-rise-rgb se conservo."
