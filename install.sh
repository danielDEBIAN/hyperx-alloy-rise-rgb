#!/usr/bin/env bash
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
APP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/hyperx-alloy-rise-rgb"
BIN_DIR="$HOME/.local/bin"
DESKTOP_DIR="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
SYSTEMD_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"

echo "==> Instalando archivos en $APP_DIR"
mkdir -p "$APP_DIR" "$BIN_DIR" "$DESKTOP_DIR" "$SYSTEMD_DIR"
install -m 755 "$REPO_DIR/hyperx_rise_rgb.py" "$APP_DIR/hyperx_rise_rgb.py"
install -m 755 "$REPO_DIR/hyperx_rise_daemon.py" "$APP_DIR/hyperx_rise_daemon.py"
install -m 755 "$REPO_DIR/hyperx_rise_gui.py" "$APP_DIR/hyperx_rise_gui.py"

echo "==> Creando accesos en $BIN_DIR"
ln -sf "$APP_DIR/hyperx_rise_rgb.py" "$BIN_DIR/hyperx-rise-rgb"
ln -sf "$APP_DIR/hyperx_rise_gui.py" "$BIN_DIR/hyperx-rise-gui"

echo "==> Instalando acceso directo de escritorio"
cat > "$DESKTOP_DIR/hyperx-alloy-rise-rgb.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=HyperX Alloy Rise 75 RGB
Comment=Control RGB nativo para teclado HyperX Alloy Rise 75
Exec=python3 $APP_DIR/hyperx_rise_gui.py
Icon=input-keyboard
Terminal=false
Categories=Utility;Settings;HardwareSettings;
EOF
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo "==> Instalando regla udev (requiere sudo)"
sudo install -m 644 "$REPO_DIR/udev/60-hyperx-alloy-rise.rules" /etc/udev/rules.d/60-hyperx-alloy-rise.rules
sudo udevadm control --reload-rules
sudo udevadm trigger -s usb -a idVendor=03f0
sudo udevadm trigger -s hidraw

echo "==> Activando servicio systemd de usuario"
cat > "$SYSTEMD_DIR/hyperx-alloy-rise-rgb.service" <<EOF
[Unit]
Description=HyperX Alloy Rise 75 Native Linux RGB Daemon
After=graphical-session.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $APP_DIR/hyperx_rise_daemon.py
Restart=on-failure
RestartSec=3

[Install]
WantedBy=default.target
EOF

systemctl --user daemon-reload
systemctl --user enable --now hyperx-alloy-rise-rgb.service

echo ""
echo "Listo. Puedes abrir 'HyperX Alloy Rise 75 RGB' desde tu menu de aplicaciones"
echo "o ejecutar 'hyperx-rise-gui' / 'hyperx-rise-rgb --help' en la terminal."
