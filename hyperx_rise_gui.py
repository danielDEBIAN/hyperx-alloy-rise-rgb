#!/usr/bin/env python3
"""Native GTK 4 RGB Control GUI for HyperX Alloy Rise 75 on Linux."""

from __future__ import annotations

import os
import subprocess
import sys

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from hyperx_rise_daemon import DEFAULT_CONFIG, load_config, save_config
from hyperx_rise_rgb import parse_hex_color

SERVICE_NAME = "hyperx-alloy-rise-rgb.service"

MODE_ITEMS = [
    ("solid", "Solido"),
    ("twozone", "Dos zonas (Teclas + WASD/Barras)"),
    ("breath", "Respiracion"),
    ("wave", "Ola"),
    ("mirrorwave", "Ola espejo"),
    ("cycle", "Ciclo de color"),
    ("confetti", "Confeti / Destellos"),
    ("alternating", "Alternado"),
    ("off", "Apagado"),
]

COLOR_TYPE_ITEMS = [
    ("one", "1 color"),
    ("two", "2 colores"),
    ("three", "3 colores"),
    ("spectrum", "Arcoiris (Spectrum)"),
]

DIRECTION_ITEMS = [
    ("right", "Derecha"),
    ("left", "Izquierda"),
    ("up", "Arriba"),
    ("down", "Abajo"),
    ("center-out", "Centro afuera"),
    ("center-in", "Centro adentro"),
]

# (Label, mode, ctype, c1, c2, c3, delay_ms)
QUICK_THEMES = [
    ("EVA-01 (Morado+Verde)", "twozone", "two", "9B30FF", "39FF14", "39FF14", 50),
    ("Ola Arcoiris", "wave", "spectrum", "9B30FF", "39FF14", "00E5FF", 45),
    ("Respiracion EVA-01", "breath", "two", "9B30FF", "39FF14", "00E5FF", 55),
    ("Morado Solido", "solid", "one", "9B30FF", "39FF14", "00E5FF", 50),
    ("Verde Neon", "solid", "one", "39FF14", "9B30FF", "00E5FF", 50),
    ("Blanco Solido", "solid", "one", "FFFFFF", "FFFFFF", "FFFFFF", 50),
]


def rgb_to_rgba(rgb: tuple[int, int, int]) -> Gdk.RGBA:
    rgba = Gdk.RGBA()
    rgba.red = rgb[0] / 255.0
    rgba.green = rgb[1] / 255.0
    rgba.blue = rgb[2] / 255.0
    rgba.alpha = 1.0
    return rgba


def rgba_to_hex(rgba: Gdk.RGBA) -> str:
    r = max(0, min(255, round(rgba.red * 255)))
    g = max(0, min(255, round(rgba.green * 255)))
    b = max(0, min(255, round(rgba.blue * 255)))
    return f"#{r:02X}{g:02X}{b:02X}"


def make_dropdown(items: list[tuple[str, str]]) -> Gtk.DropDown:
    model = Gtk.StringList.new([label for _, label in items])
    return Gtk.DropDown(model=model)


def set_dropdown_key(dd: Gtk.DropDown, items: list[tuple[str, str]], key: str) -> None:
    for idx, (k, _) in enumerate(items):
        if k == key:
            dd.set_selected(idx)
            return


def get_dropdown_key(dd: Gtk.DropDown, items: list[tuple[str, str]]) -> str:
    idx = dd.get_selected()
    if 0 <= idx < len(items):
        return items[idx][0]
    return items[0][0]


def ensure_daemon_running() -> tuple[bool, str]:
    try:
        res = subprocess.run(
            ["systemctl", "--user", "is-active", SERVICE_NAME],
            capture_output=True,
            text=True,
            timeout=2,
        )
        if res.returncode == 0 and res.stdout.strip() == "active":
            subprocess.run(
                ["systemctl", "--user", "kill", "-s", "HUP", SERVICE_NAME],
                capture_output=True,
                timeout=2,
            )
            return True, "Servicio activo"
        subprocess.run(
            ["systemctl", "--user", "start", SERVICE_NAME],
            capture_output=True,
            timeout=3,
        )
        return True, "Servicio iniciado"
    except Exception as e:
        return False, f"Servicio no disponible: {e}"


class AlloyRiseWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="HyperX Alloy Rise 75 — RGB")
        self.set_default_size(520, 590)
        self._loading = True
        self._debounce_id: int | None = None

        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        self.apply_btn = Gtk.Button(label="Aplicar")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_: self.apply_now())
        header.pack_end(self.apply_btn)

        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        self.set_child(scroller)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        root.set_margin_top(16)
        root.set_margin_bottom(16)
        root.set_margin_start(18)
        root.set_margin_end(18)
        scroller.set_child(root)

        # Enable toggle + Brightness
        grid_top = Gtk.Grid(column_spacing=12, row_spacing=10)
        root.append(grid_top)

        self.sw_enabled = Gtk.Switch()
        self.sw_enabled.set_halign(Gtk.Align.START)
        self.sw_enabled.connect("notify::active", lambda *_: self.apply_now())

        grid_top.attach(self._label("Control RGB en Linux:"), 0, 0, 1, 1)
        grid_top.attach(self.sw_enabled, 1, 0, 1, 1)

        self.scale_brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.scale_brightness.set_value(100)
        self.scale_brightness.set_draw_value(True)
        self.scale_brightness.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_brightness.set_hexpand(True)
        self.scale_brightness.connect("value-changed", lambda *_: self.schedule_apply())

        grid_top.attach(self._label("Brillo (%):"), 0, 1, 1, 1)
        grid_top.attach(self.scale_brightness, 1, 1, 1, 1)

        root.append(Gtk.Separator())

        # Effect configuration
        sec1 = Gtk.Label(label="Efecto y colores (aplicacion instantanea)")
        sec1.set_xalign(0)
        sec1.add_css_class("heading")
        root.append(sec1)

        grid_main = Gtk.Grid(column_spacing=12, row_spacing=10)
        root.append(grid_main)

        self.dd_mode = make_dropdown(MODE_ITEMS)
        self.dd_mode.set_hexpand(True)
        self.dd_mode.connect("notify::selected", lambda *_: self.apply_now())
        grid_main.attach(self._label("Efecto:"), 0, 0, 1, 1)
        grid_main.attach(self.dd_mode, 1, 0, 1, 1)

        self.dd_ctype = make_dropdown(COLOR_TYPE_ITEMS)
        self.dd_ctype.set_hexpand(True)
        self.dd_ctype.connect("notify::selected", lambda *_: self.apply_now())
        grid_main.attach(self._label("Paleta:"), 0, 1, 1, 1)
        grid_main.attach(self.dd_ctype, 1, 1, 1, 1)

        self.dd_dir = make_dropdown(DIRECTION_ITEMS)
        self.dd_dir.set_hexpand(True)
        self.dd_dir.connect("notify::selected", lambda *_: self.apply_now())
        grid_main.attach(self._label("Direccion:"), 0, 2, 1, 1)
        grid_main.attach(self.dd_dir, 1, 2, 1, 1)

        self.scale_delay = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 25, 200, 5)
        self.scale_delay.set_value(50)
        self.scale_delay.set_draw_value(True)
        self.scale_delay.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_delay.set_hexpand(True)
        self.scale_delay.connect("value-changed", lambda *_: self.schedule_apply())
        grid_main.attach(self._label("Retardo por cuadro (ms):"), 0, 3, 1, 1)
        grid_main.attach(self.scale_delay, 1, 3, 1, 1)

        # Color pickers
        color_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        root.append(color_row)

        self.color_buttons: list[Gtk.ColorDialogButton] = []
        defaults = [
            ("Color 1 (Base)", (155, 48, 255)),
            ("Color 2 (Acentos)", (57, 255, 20)),
            ("Color 3 (Extra)", (0, 229, 255)),
        ]
        for label, rgb in defaults:
            box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
            box.set_hexpand(True)
            lbl = Gtk.Label(label=label)
            dialog = Gtk.ColorDialog(with_alpha=False)
            btn = Gtk.ColorDialogButton(dialog=dialog)
            btn.set_rgba(rgb_to_rgba(rgb))
            btn.connect("notify::rgba", lambda *_: self.apply_now())
            box.append(lbl)
            box.append(btn)
            color_row.append(box)
            self.color_buttons.append(btn)

        root.append(Gtk.Separator())

        # Quick themes
        swatch_lbl = Gtk.Label(label="Temas rapidos (1 clic):")
        swatch_lbl.set_xalign(0)
        swatch_lbl.add_css_class("heading")
        root.append(swatch_lbl)

        swatch_flow = Gtk.FlowBox()
        swatch_flow.set_selection_mode(Gtk.SelectionMode.NONE)
        swatch_flow.set_max_children_per_line(3)
        swatch_flow.set_row_spacing(6)
        swatch_flow.set_column_spacing(6)
        root.append(swatch_flow)

        for theme in QUICK_THEMES:
            b = Gtk.Button(label=theme[0])
            b.connect("clicked", self.on_quick_theme, theme)
            swatch_flow.append(b)

        # Status bar
        self.status_label = Gtk.Label(label="Listo")
        self.status_label.set_xalign(0)
        self.status_label.set_wrap(True)
        self.status_label.add_css_class("dim-label")
        root.append(self.status_label)

        self.load_from_config()

    @staticmethod
    def _label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        return lbl

    def load_from_config(self) -> None:
        self._loading = True
        try:
            cfg = load_config()
            self.sw_enabled.set_active(bool(cfg.get("enabled", True)))
            self.scale_brightness.set_value(int(cfg.get("brightness", 100)))
            set_dropdown_key(self.dd_mode, MODE_ITEMS, str(cfg.get("mode", "twozone")))
            set_dropdown_key(self.dd_ctype, COLOR_TYPE_ITEMS, str(cfg.get("color_type", "two")))
            set_dropdown_key(self.dd_dir, DIRECTION_ITEMS, str(cfg.get("direction", "right")))
            self.scale_delay.set_value(int(cfg.get("delay_ms", 50)))
            raw_cols = cfg.get("colors") or DEFAULT_CONFIG["colors"]
            for idx, btn in enumerate(self.color_buttons):
                if idx < len(raw_cols):
                    try:
                        btn.set_rgba(rgb_to_rgba(parse_hex_color(str(raw_cols[idx]))))
                    except Exception:
                        pass
            ok, msg = ensure_daemon_running()
            self.status_label.set_text(f"{msg} — Efecto actual: {cfg.get('mode')} ({cfg.get('brightness')}%)")
        finally:
            self._loading = False

    def on_quick_theme(self, _btn: Gtk.Button, theme: tuple) -> None:
        _name, mode, ctype, c1, c2, c3, delay = theme
        self._loading = True
        try:
            self.sw_enabled.set_active(True)
            set_dropdown_key(self.dd_mode, MODE_ITEMS, mode)
            set_dropdown_key(self.dd_ctype, COLOR_TYPE_ITEMS, ctype)
            self.color_buttons[0].set_rgba(rgb_to_rgba(parse_hex_color(c1)))
            self.color_buttons[1].set_rgba(rgb_to_rgba(parse_hex_color(c2)))
            self.color_buttons[2].set_rgba(rgb_to_rgba(parse_hex_color(c3)))
            self.scale_delay.set_value(delay)
        finally:
            self._loading = False
        self.apply_now()

    def schedule_apply(self) -> None:
        if self._loading:
            return
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
        self._debounce_id = GLib.timeout_add(60, self._run_debounced_apply)

    def _run_debounced_apply(self) -> bool:
        self._debounce_id = None
        self.apply_now()
        return False

    def apply_now(self) -> None:
        if self._loading:
            return
        if self._debounce_id is not None:
            GLib.source_remove(self._debounce_id)
            self._debounce_id = None
        cfg = {
            "enabled": bool(self.sw_enabled.get_active()),
            "brightness": int(round(self.scale_brightness.get_value())),
            "mode": get_dropdown_key(self.dd_mode, MODE_ITEMS),
            "color_type": get_dropdown_key(self.dd_ctype, COLOR_TYPE_ITEMS),
            "direction": get_dropdown_key(self.dd_dir, DIRECTION_ITEMS),
            "delay_ms": int(round(self.scale_delay.get_value())),
            "colors": [rgba_to_hex(b.get_rgba()) for b in self.color_buttons],
        }
        try:
            save_config(cfg)
            _ok, svc_msg = ensure_daemon_running()
            if cfg["enabled"]:
                self.status_label.set_text(
                    f"Aplicado ({svc_msg}): {cfg['mode']} | {cfg['colors'][0]} | brillo {cfg['brightness']}%"
                )
            else:
                self.status_label.set_text("Control Linux pausado — usando efecto nativo del teclado.")
        except Exception as e:
            self.status_label.set_text(f"Error guardando configuracion: {e}")


class AlloyRiseApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id="local.hyperx.AlloyRise75RGB")

    def do_activate(self) -> None:
        win = self.props.active_window
        if not win:
            win = AlloyRiseWindow(self)
        win.present()


def main() -> int:
    app = AlloyRiseApp()
    return app.run(sys.argv)


if __name__ == "__main__":
    raise SystemExit(main())
