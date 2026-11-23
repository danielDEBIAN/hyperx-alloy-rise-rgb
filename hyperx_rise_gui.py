#!/usr/bin/env python3
"""Native GTK 4 RGB Control GUI with interactive per-key & zone keyboard view for HyperX Alloy Rise 75."""

from __future__ import annotations

import math
import os
import subprocess
import sys

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, GLib, Gtk

SCRIPT_DIR = os.path.dirname(os.path.realpath(__file__))
if SCRIPT_DIR not in sys.path:
    sys.path.insert(0, SCRIPT_DIR)

from hyperx_rise_daemon import DEFAULT_CONFIG, load_config, save_config
from hyperx_rise_rgb import (
    KEYBOARD_LAYOUT,
    NUM_LEDS,
    ZONE_ITEMS,
    ZONES,
    _fmt_rgb,
    generate_effect_frames,
    parse_hex_color,
)

SERVICE_NAME = "hyperx-alloy-rise-rgb.service"

MODE_ITEMS = [
    ("solid", "Solido"),
    ("twozone", "Dos zonas (Teclas + WASD/Barras)"),
    ("custom", "Personalizado (Por tecla / Zonas)"),
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

BRUSH_SWATCHES = [
    ("Morado", "#9B30FF"),
    ("Verde", "#39FF14"),
    ("Cian", "#00E5FF"),
    ("Rojo", "#FF1E27"),
    ("Naranja", "#FF6B00"),
    ("Blanco", "#FFFFFF"),
    ("Apagar", "#000000"),
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


def _rounded_rect(cr: cairo.Context, x: float, y: float, w: float, h: float, r: float) -> None:
    r = min(r, w / 2.0, h / 2.0)
    cr.new_sub_path()
    cr.arc(x + w - r, y + r, r, -math.pi / 2.0, 0.0)
    cr.arc(x + w - r, y + h - r, r, 0.0, math.pi / 2.0)
    cr.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
    cr.arc(x + r, y + r, r, math.pi, 3.0 * math.pi / 2.0)
    cr.close_path()


class KeyboardCanvas(Gtk.DrawingArea):
    """Interactive Cairo keyboard map for the HyperX Alloy Rise 75."""

    # Bounding box in keycap units (including sidebars)
    MIN_X = -0.75
    MAX_X = 16.85
    MIN_Y = -0.20
    MAX_Y = 6.35

    def __init__(self, on_key_action, on_eyedropper, on_hover):
        super().__init__()
        self.set_hexpand(True)
        self.set_vexpand(True)
        self.set_content_width(780)
        self.set_content_height(310)
        self.set_draw_func(self._draw)

        self.on_key_action = on_key_action
        self.on_eyedropper = on_eyedropper
        self.on_hover = on_hover

        self.led_colors: dict[int, str] = {i: "#9B30FF" for i in range(1, NUM_LEDS + 1)}
        self.selected_ids: set[str] = set()
        self.hover_id: str | None = None
        self._drag_visited: set[str] = set()
        self._drag_start: tuple[float, float] = (0.0, 0.0)

        # Left click (paint / select)
        click = Gtk.GestureClick.new()
        click.set_button(1)
        click.connect("pressed", self._on_left_press)
        self.add_controller(click)

        # Right click (eyedropper)
        rclick = Gtk.GestureClick.new()
        rclick.set_button(3)
        rclick.connect("pressed", self._on_right_press)
        self.add_controller(rclick)

        # Drag-to-paint / drag-to-select
        drag = Gtk.GestureDrag.new()
        drag.set_button(1)
        drag.connect("drag-begin", self._on_drag_begin)
        drag.connect("drag-update", self._on_drag_update)
        drag.connect("drag-end", self._on_drag_end)
        self.add_controller(drag)

        # Hover tracking
        motion = Gtk.EventControllerMotion.new()
        motion.connect("motion", self._on_motion)
        motion.connect("leave", self._on_leave)
        self.add_controller(motion)

    def _geometry(self, width: int, height: int) -> tuple[float, float, float]:
        span_x = self.MAX_X - self.MIN_X
        span_y = self.MAX_Y - self.MIN_Y
        pad = 14.0
        scale = min((width - 2 * pad) / span_x, (height - 2 * pad) / span_y)
        scale = max(12.0, scale)
        ox = (width - span_x * scale) / 2.0 - self.MIN_X * scale
        oy = (height - span_y * scale) / 2.0 - self.MIN_Y * scale
        return scale, ox, oy

    def _hit_test(self, px: float, py: float) -> dict | None:
        scale, ox, oy = self._geometry(self.get_width(), self.get_height())
        ux = (px - ox) / scale
        uy = (py - oy) / scale
        for item in KEYBOARD_LAYOUT:
            if not item["leds"]:
                continue
            if item["x"] <= ux <= item["x"] + item["w"] and item["y"] <= uy <= item["y"] + item["h"]:
                return item
        return None

    def _on_left_press(self, gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        item = self._hit_test(x, y)
        if not item:
            return
        state = gesture.get_current_event_state()
        ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
        self._drag_visited = {item["id"]}
        self.on_key_action(item, ctrl=ctrl, is_drag=False)

    def _on_right_press(self, _gesture: Gtk.GestureClick, _n_press: int, x: float, y: float) -> None:
        item = self._hit_test(x, y)
        if item and item["leds"]:
            hex_col = self.led_colors.get(item["leds"][0], "#9B30FF")
            self.on_eyedropper(item, hex_col)

    def _on_drag_begin(self, _gesture: Gtk.GestureDrag, start_x: float, start_y: float) -> None:
        self._drag_start = (start_x, start_y)

    def _on_drag_update(self, _gesture: Gtk.GestureDrag, offset_x: float, offset_y: float) -> None:
        x = self._drag_start[0] + offset_x
        y = self._drag_start[1] + offset_y
        item = self._hit_test(x, y)
        if item and item["id"] not in self._drag_visited:
            self._drag_visited.add(item["id"])
            self.on_key_action(item, ctrl=False, is_drag=True)

    def _on_drag_end(self, _gesture: Gtk.GestureDrag, _offset_x: float, _offset_y: float) -> None:
        self._drag_visited.clear()

    def _on_motion(self, _ctrl: Gtk.EventControllerMotion, x: float, y: float) -> None:
        item = self._hit_test(x, y)
        new_id = item["id"] if item else None
        if new_id != self.hover_id:
            self.hover_id = new_id
            self.queue_draw()
        if item and item["leds"]:
            col = self.led_colors.get(item["leds"][0], "#000000")
            self.on_hover(item, col)
        else:
            self.on_hover(None, "")

    def _on_leave(self, _ctrl: Gtk.EventControllerMotion) -> None:
        self.hover_id = None
        self.queue_draw()
        self.on_hover(None, "")

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, width: int, height: int) -> None:
        scale, ox, oy = self._geometry(width, height)

        # Keyboard chassis background
        chassis_x = ox + (self.MIN_X + 0.05) * scale
        chassis_y = oy + (self.MIN_Y + 0.05) * scale
        chassis_w = (self.MAX_X - self.MIN_X - 0.10) * scale
        chassis_h = (self.MAX_Y - self.MIN_Y - 0.10) * scale

        _rounded_rect(cr, chassis_x, chassis_y, chassis_w, chassis_h, 14)
        cr.set_source_rgb(0.09, 0.09, 0.11)
        cr.fill_preserve()
        cr.set_source_rgb(0.22, 0.22, 0.27)
        cr.set_line_width(1.5)
        cr.stroke()

        # Inner plate behind keys
        plate_x = ox - 0.08 * scale
        plate_y = oy - 0.08 * scale
        plate_w = 16.26 * scale
        plate_h = 6.31 * scale
        _rounded_rect(cr, plate_x, plate_y, plate_w, plate_h, 8)
        cr.set_source_rgb(0.05, 0.05, 0.07)
        cr.fill()

        gap = max(2.0, scale * 0.07)
        font_base = max(8.0, min(13.0, scale * 0.27))

        for item in KEYBOARD_LAYOUT:
            kx = ox + item["x"] * scale + gap / 2.0
            ky = oy + item["y"] * scale + gap / 2.0
            kw = item["w"] * scale - gap
            kh = item["h"] * scale - gap

            if item.get("is_knob"):
                cx = kx + kw / 2.0
                cy = ky + kh / 2.0
                rad = min(kw, kh) * 0.42
                cr.arc(cx, cy, rad, 0, 2 * math.pi)
                cr.set_source_rgb(0.18, 0.18, 0.22)
                cr.fill_preserve()
                cr.set_source_rgb(0.45, 0.45, 0.52)
                cr.set_line_width(1.5)
                cr.stroke()
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                cr.set_font_size(max(7.0, font_base * 0.75))
                cr.set_source_rgb(0.75, 0.75, 0.80)
                ext = cr.text_extents("VOL")
                cr.move_to(cx - ext.width / 2.0 - ext.x_bearing, cy - ext.height / 2.0 - ext.y_bearing)
                cr.show_text("VOL")
                continue

            hex_col = self.led_colors.get(item["leds"][0], "#000000")
            try:
                r, g, b = parse_hex_color(hex_col)
            except Exception:
                r, g, b = (0, 0, 0)
            rf, gf, bf = r / 255.0, g / 255.0, b / 255.0
            is_off = (r + g + b) < 15
            is_selected = item["id"] in self.selected_ids
            is_hover = item["id"] == self.hover_id

            if item.get("is_sidebar"):
                # Side bar RGB strip segment
                _rounded_rect(cr, kx, ky, kw, kh, 4)
                if is_off:
                    cr.set_source_rgb(0.14, 0.14, 0.17)
                else:
                    cr.set_source_rgb(rf, gf, bf)
                cr.fill_preserve()
                if is_selected:
                    cr.set_source_rgb(1.0, 1.0, 1.0)
                    cr.set_line_width(2.2)
                elif is_hover:
                    cr.set_source_rgb(0.9, 0.9, 0.95)
                    cr.set_line_width(1.5)
                else:
                    cr.set_source_rgb(0.25, 0.25, 0.30)
                    cr.set_line_width(1.0)
                cr.stroke()
                continue

            # Soft underglow around keycap
            if not is_off:
                _rounded_rect(cr, kx - 1.5, ky - 1.5, kw + 3.0, kh + 3.0, 6)
                cr.set_source_rgba(rf, gf, bf, 0.32)
                cr.fill()

            # Keycap body (dark base subtly tinted with its LED color)
            _rounded_rect(cr, kx, ky, kw, kh, 5)
            cr.set_source_rgb(0.11 + rf * 0.18, 0.11 + gf * 0.18, 0.14 + bf * 0.18)
            cr.fill_preserve()

            # Keycap border
            if is_selected:
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.set_line_width(2.4)
            elif is_hover:
                cr.set_source_rgb(1.0, 1.0, 1.0)
                cr.set_line_width(1.8)
            elif is_off:
                cr.set_source_rgb(0.26, 0.26, 0.32)
                cr.set_line_width(1.2)
            else:
                cr.set_source_rgb(rf, gf, bf)
                cr.set_line_width(1.8)
            cr.stroke()

            # Key label
            label = item["label"]
            if label:
                cr.select_font_face("Sans", cairo.FONT_SLANT_NORMAL, cairo.FONT_WEIGHT_BOLD)
                fsize = font_base if len(label) <= 2 else font_base * 0.78
                cr.set_font_size(fsize)
                if is_off:
                    cr.set_source_rgb(0.55, 0.55, 0.62)
                else:
                    # Bright readable label tinted slightly toward key color
                    cr.set_source_rgb(0.75 + rf * 0.25, 0.75 + gf * 0.25, 0.78 + bf * 0.22)
                ext = cr.text_extents(label)
                tx = kx + (kw - ext.width) / 2.0 - ext.x_bearing
                ty = ky + (kh - ext.height) / 2.0 - ext.y_bearing
                cr.move_to(tx, ty)
                cr.show_text(label)


class AlloyRiseWindow(Gtk.ApplicationWindow):
    def __init__(self, app: Gtk.Application):
        super().__init__(application=app, title="HyperX Alloy Rise 75 — RGB")
        self.set_default_size(880, 620)
        self._loading = True
        self._debounce_id: int | None = None
        self._tool_mode = "paint"  # "paint" or "select"

        header = Gtk.HeaderBar()
        self.set_titlebar(header)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        self.stack.set_transition_duration(180)

        switcher = Gtk.StackSwitcher(stack=self.stack)
        header.set_title_widget(switcher)

        self.apply_btn = Gtk.Button(label="Aplicar")
        self.apply_btn.add_css_class("suggested-action")
        self.apply_btn.connect("clicked", lambda *_: self.apply_now())
        header.pack_end(self.apply_btn)

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        outer.set_margin_top(12)
        outer.set_margin_bottom(12)
        outer.set_margin_start(14)
        outer.set_margin_end(14)
        self.set_child(outer)

        # Global top bar: Enable switch + Brightness
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        outer.append(top_bar)

        lbl_en = Gtk.Label(label="Control RGB en Linux:")
        self.sw_enabled = Gtk.Switch()
        self.sw_enabled.set_valign(Gtk.Align.CENTER)
        self.sw_enabled.connect("notify::active", lambda *_: self.apply_now())

        lbl_br = Gtk.Label(label="Brillo (%):")
        lbl_br.set_margin_start(12)
        self.scale_brightness = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 5)
        self.scale_brightness.set_value(100)
        self.scale_brightness.set_draw_value(True)
        self.scale_brightness.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_brightness.set_hexpand(True)
        self.scale_brightness.connect("value-changed", lambda *_: self.schedule_apply())

        top_bar.append(lbl_en)
        top_bar.append(self.sw_enabled)
        top_bar.append(lbl_br)
        top_bar.append(self.scale_brightness)

        outer.append(Gtk.Separator())
        outer.append(self.stack)

        # Page 1: Interactive Keyboard (Per-Key & Zones)
        kb_page = self._build_keyboard_page()
        self.stack.add_titled(kb_page, "keyboard", "Teclado (Por tecla / Zonas)")

        # Page 2: Effects & Animations
        fx_page = self._build_effects_page()
        self.stack.add_titled(fx_page, "effects", "Efectos y Animaciones")

        # Status bar
        status_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        outer.append(status_row)
        self.status_label = Gtk.Label(label="Listo")
        self.status_label.set_xalign(0)
        self.status_label.set_hexpand(True)
        self.status_label.add_css_class("dim-label")
        status_row.append(self.status_label)

        self.hover_label = Gtk.Label(label="")
        self.hover_label.set_xalign(1)
        self.hover_label.add_css_class("dim-label")
        status_row.append(self.hover_label)

        self.load_from_config()

    def _build_keyboard_page(self) -> Gtk.Box:
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        # Row 1: Brush color, quick swatches, Paint vs Select mode
        tools_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page.append(tools_row)

        tools_row.append(Gtk.Label(label="Color pincel:"))
        brush_dialog = Gtk.ColorDialog(with_alpha=False)
        self.brush_btn = Gtk.ColorDialogButton(dialog=brush_dialog)
        self.brush_btn.set_rgba(rgb_to_rgba((57, 255, 20)))
        self.brush_btn.connect("notify::rgba", self._on_brush_changed)
        tools_row.append(self.brush_btn)

        for name, hex_col in BRUSH_SWATCHES:
            b = Gtk.Button(label=name)
            b.connect("clicked", self._on_swatch_clicked, hex_col)
            tools_row.append(b)

        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        tools_row.append(spacer)

        self.btn_mode_paint = Gtk.ToggleButton(label="Pintar")
        self.btn_mode_paint.set_active(True)
        self.btn_mode_select = Gtk.ToggleButton(label="Seleccionar")
        self.btn_mode_select.set_group(self.btn_mode_paint)
        self.btn_mode_paint.connect("toggled", self._on_tool_toggled)
        tools_row.append(self.btn_mode_paint)
        tools_row.append(self.btn_mode_select)

        # Row 2: Zones & Actions
        zone_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        page.append(zone_row)

        zone_row.append(Gtk.Label(label="Zona:"))
        self.dd_zone = make_dropdown(ZONE_ITEMS)
        self.dd_zone.set_hexpand(True)
        zone_row.append(self.dd_zone)

        btn_paint_zone = Gtk.Button(label="Pintar zona")
        btn_paint_zone.connect("clicked", self._on_paint_zone)
        zone_row.append(btn_paint_zone)

        btn_sel_zone = Gtk.Button(label="Seleccionar zona")
        btn_sel_zone.connect("clicked", self._on_select_zone)
        zone_row.append(btn_sel_zone)

        btn_paint_sel = Gtk.Button(label="Pintar seleccion")
        btn_paint_sel.connect("clicked", self._on_paint_selection)
        zone_row.append(btn_paint_sel)

        btn_clear_sel = Gtk.Button(label="Deseleccionar")
        btn_clear_sel.connect("clicked", self._on_clear_selection)
        zone_row.append(btn_clear_sel)

        btn_copy_fx = Gtk.Button(label="Importar de efecto")
        btn_copy_fx.set_tooltip_text("Copia los colores del efecto actual al mapa por tecla")
        btn_copy_fx.connect("clicked", self._on_copy_from_effect)
        zone_row.append(btn_copy_fx)

        # Visual Keyboard Canvas
        self.canvas = KeyboardCanvas(
            on_key_action=self._on_canvas_key,
            on_eyedropper=self._on_canvas_eyedropper,
            on_hover=self._on_canvas_hover,
        )
        page.append(self.canvas)

        hint = Gtk.Label(
            label="Tip: Haz clic o arrastra para pintar teclas y barras laterales. "
            "Clic derecho copia el color de una tecla. Ctrl+Clic selecciona multiples."
        )
        hint.set_xalign(0)
        hint.add_css_class("dim-label")
        page.append(hint)

        return page

    def _build_effects_page(self) -> Gtk.ScrolledWindow:
        scroller = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER)
        scroller.set_vexpand(True)

        root = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        root.set_margin_top(8)
        root.set_margin_bottom(8)
        root.set_margin_start(4)
        root.set_margin_end(4)
        scroller.set_child(root)

        sec1 = Gtk.Label(label="Efectos y animaciones")
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

        self.scale_delay = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 25, 1000, 5)
        self.scale_delay.set_value(50)
        self.scale_delay.set_draw_value(True)
        self.scale_delay.set_value_pos(Gtk.PositionType.RIGHT)
        self.scale_delay.set_hexpand(True)
        self.scale_delay.connect("value-changed", lambda *_: self.schedule_apply())
        grid_main.attach(self._label("Retardo por cuadro (ms):"), 0, 3, 1, 1)
        grid_main.attach(self.scale_delay, 1, 3, 1, 1)

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

        return scroller

    @staticmethod
    def _label(text: str) -> Gtk.Label:
        lbl = Gtk.Label(label=text)
        lbl.set_xalign(0)
        return lbl

    def _current_brush_hex(self) -> str:
        return rgba_to_hex(self.brush_btn.get_rgba())

    def _on_tool_toggled(self, _btn: Gtk.ToggleButton) -> None:
        self._tool_mode = "paint" if self.btn_mode_paint.get_active() else "select"

    def _on_brush_changed(self, *_args) -> None:
        if self._tool_mode == "select" and self.canvas.selected_ids:
            self._paint_item_ids(self.canvas.selected_ids, self._current_brush_hex())

    def _on_swatch_clicked(self, _btn: Gtk.Button, hex_col: str) -> None:
        self.brush_btn.set_rgba(rgb_to_rgba(parse_hex_color(hex_col)))
        if self.canvas.selected_ids:
            self._paint_item_ids(self.canvas.selected_ids, hex_col)

    def _on_paint_zone(self, _btn: Gtk.Button) -> None:
        zkey = get_dropdown_key(self.dd_zone, ZONE_ITEMS)
        hex_col = self._current_brush_hex()
        for led in ZONES.get(zkey, []):
            self.canvas.led_colors[led] = hex_col
        self.canvas.queue_draw()
        self._switch_to_custom_and_apply()

    def _on_select_zone(self, _btn: Gtk.Button) -> None:
        zkey = get_dropdown_key(self.dd_zone, ZONE_ITEMS)
        zleds = set(ZONES.get(zkey, []))
        self.canvas.selected_ids = {
            item["id"] for item in KEYBOARD_LAYOUT if item["leds"] and any(l in zleds for l in item["leds"])
        }
        self.btn_mode_select.set_active(True)
        self.canvas.queue_draw()

    def _on_paint_selection(self, _btn: Gtk.Button) -> None:
        if not self.canvas.selected_ids:
            self.status_label.set_text("No hay teclas seleccionadas.")
            return
        self._paint_item_ids(self.canvas.selected_ids, self._current_brush_hex())

    def _on_clear_selection(self, _btn: Gtk.Button) -> None:
        self.canvas.selected_ids.clear()
        self.canvas.queue_draw()

    def _on_copy_from_effect(self, _btn: Gtk.Button) -> None:
        mode = get_dropdown_key(self.dd_mode, MODE_ITEMS)
        if mode == "custom":
            mode = "twozone"
        ctype = get_dropdown_key(self.dd_ctype, COLOR_TYPE_ITEMS)
        direction = get_dropdown_key(self.dd_dir, DIRECTION_ITEMS)
        cols = [parse_hex_color(rgba_to_hex(b.get_rgba())) for b in self.color_buttons]
        frames = generate_effect_frames(mode, cols, color_type=ctype, direction=direction, num_frames=1)
        if frames:
            for idx, rgb in enumerate(frames[0], start=1):
                self.canvas.led_colors[idx] = _fmt_rgb(rgb)
            self.canvas.queue_draw()
            self._switch_to_custom_and_apply()

    def _paint_item_ids(self, item_ids: set[str], hex_col: str) -> None:
        for item in KEYBOARD_LAYOUT:
            if item["id"] in item_ids:
                for led in item["leds"]:
                    self.canvas.led_colors[led] = hex_col
        self.canvas.queue_draw()
        self._switch_to_custom_and_apply()

    def _on_canvas_key(self, item: dict, ctrl: bool, is_drag: bool) -> None:
        if self._tool_mode == "select" or ctrl:
            if is_drag:
                self.canvas.selected_ids.add(item["id"])
            else:
                if item["id"] in self.canvas.selected_ids:
                    self.canvas.selected_ids.remove(item["id"])
                else:
                    self.canvas.selected_ids.add(item["id"])
            self.canvas.queue_draw()
            return

        # Paint mode: if key is part of a multi-selection and clicked (not dragged), paint all selected
        if not is_drag and item["id"] in self.canvas.selected_ids and len(self.canvas.selected_ids) > 1:
            self._paint_item_ids(self.canvas.selected_ids, self._current_brush_hex())
            return

        hex_col = self._current_brush_hex()
        for led in item["leds"]:
            self.canvas.led_colors[led] = hex_col
        self.canvas.queue_draw()
        self._switch_to_custom_and_apply(debounced=is_drag)

    def _on_canvas_eyedropper(self, item: dict, hex_col: str) -> None:
        try:
            self.brush_btn.set_rgba(rgb_to_rgba(parse_hex_color(hex_col)))
            name = item["label"] or item["id"]
            self.status_label.set_text(f"Color copiado de '{name}': {hex_col}")
        except Exception:
            pass

    def _on_canvas_hover(self, item: dict | None, hex_col: str) -> None:
        if not item:
            self.hover_label.set_text("")
            return
        name = item["label"] or item["id"]
        leds_str = ",".join(str(l) for l in item["leds"])
        self.hover_label.set_text(f"{name} (LED {leds_str}) · {hex_col}")

    def _switch_to_custom_and_apply(self, debounced: bool = False) -> None:
        self._loading = True
        try:
            self.sw_enabled.set_active(True)
            set_dropdown_key(self.dd_mode, MODE_ITEMS, "custom")
        finally:
            self._loading = False
        if debounced:
            self.schedule_apply()
        else:
            self.apply_now()

    def load_from_config(self) -> None:
        self._loading = True
        try:
            cfg = load_config()
            self.sw_enabled.set_active(bool(cfg.get("enabled", True)))
            self.scale_brightness.set_value(int(cfg.get("brightness", 100)))
            mode = str(cfg.get("mode", "twozone"))
            set_dropdown_key(self.dd_mode, MODE_ITEMS, mode)
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

            # Populate canvas LEDs from custom_leds or current effect preview
            custom_leds = cfg.get("custom_leds") if isinstance(cfg.get("custom_leds"), dict) else {}
            if custom_leds:
                base_col = str(raw_cols[0]) if raw_cols else "#9B30FF"
                for i in range(1, NUM_LEDS + 1):
                    self.canvas.led_colors[i] = str(custom_leds.get(str(i), base_col))
            else:
                cols = [parse_hex_color(str(c)) for c in raw_cols]
                frames = generate_effect_frames(
                    "twozone" if mode == "custom" else mode,
                    cols,
                    color_type=str(cfg.get("color_type", "two")),
                    direction=str(cfg.get("direction", "right")),
                    num_frames=1,
                )
                if frames:
                    for idx, rgb in enumerate(frames[0], start=1):
                        self.canvas.led_colors[idx] = _fmt_rgb(rgb)

            self.canvas.queue_draw()

            _ok, msg = ensure_daemon_running()
            self.status_label.set_text(f"{msg} — Modo actual: {mode} ({cfg.get('brightness')}%)")
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
            "custom_leds": {str(k): v for k, v in self.canvas.led_colors.items()},
        }
        try:
            save_config(cfg)
            _ok, svc_msg = ensure_daemon_running()
            if cfg["enabled"]:
                self.status_label.set_text(
                    f"Aplicado ({svc_msg}): {cfg['mode']} | brillo {cfg['brightness']}%"
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
