#!/usr/bin/env python3
"""
Native Linux RGB controller for the HyperX Alloy Rise 75 (USB 03f0:02a1).

Uses the HyperX Universal HID protocol (Interface 2, Usage Page 0xFF90, 64-byte
interrupt reports) over /dev/hidraw* or automatic libusb-1.0 fallback.

On the Alloy Rise 75, onboard hardware lighting profiles are stored as
FrameLight animations (0x44 0x03 header + 0x44 0x04 frame packets + 0x42 0x02
mode=0x06 commit), so every color and animated effect is written directly into
the keyboard's onboard flash memory and persists across reboots with zero CPU
or background daemon required.
"""

from __future__ import annotations

import argparse
import colorsys
import ctypes
import ctypes.util
import glob
import math
import os
import random
import select
import struct
import sys
import time
from typing import Optional

VENDOR_ID = 0x03F0
PRODUCT_IDS = (0x02A1, 0x03A1)  # Alloy Rise 75 (wired / wireless)
USB_INTERFACE = 2
USB_EP_OUT = 0x04
USB_EP_IN = 0x83
PACKET_SIZE = 64
NUM_LEDS = 103
PACKETS_PER_FRAME = 6  # ceil(103 / 20) = 6 packets per frame

# Exact physical (x, y) normalized coordinates [0.0..1.0] for each 1-based LED
# index derived from ALLOY_RISE75_LED_SEQUENCE_MAP (102 active LEDs, 1..103).
ACCENT_LEDS = {1, 33, 47, 48, 49, 75, 87, 88, 89}  # ESC, WASD, Arrows
SIDE_BAR_LEDS = set(range(90, 104))  # 7 left + 7 right side bar LEDs


def _build_led_coords() -> dict[int, tuple[float, float]]:
    coords: dict[int, tuple[float, float]] = {}
    rows = [
        (range(1, 15), 0.0),    # Row 0: ESC, F1..F12 (14 LEDs)
        (range(15, 31), 0.2),   # Row 1: ` 1..0 - = BS Home (16 LEDs)
        (range(31, 46), 0.4),   # Row 2: Tab Q..P [ ] \ PgUp (15 LEDs)
        (range(46, 61), 0.6),   # Row 3: Caps A..L ; ' Enter PgDn (15 LEDs)
        (range(61, 76), 0.8),   # Row 4: LShift Z..M , . / RShift Up (15 LEDs)
        (range(77, 90), 1.0),   # Row 5: Bottom row + Left/Down/Right (13 LEDs)
    ]
    for r_range, y in rows:
        lst = list(r_range)
        n = max(1, len(lst) - 1)
        for i, led in enumerate(lst):
            coords[led] = (i / n, y)
    # Side bars: even LEDs 90,92..102 = left (x=0.0), odd 91,93..103 = right (x=1.0)
    for i in range(7):
        y = i / 6.0
        coords[90 + i * 2] = (0.0, y)
        coords[91 + i * 2] = (1.0, y)
    coords[76] = (0.5, 0.9)
    return coords


LED_COORDS = _build_led_coords()

EFFECT_MODES = (
    "solid",
    "twozone",
    "breath",
    "wave",
    "mirrorwave",
    "cycle",
    "confetti",
    "alternating",
    "off",
)

DIRECTIONS = ("right", "left", "up", "down", "center-out", "center-in")
COLOR_TYPES = ("one", "two", "three", "spectrum")


def parse_hex_color(value: str) -> tuple[int, int, int]:
    s = value.strip().lstrip("#")
    if len(s) != 6:
        raise ValueError(f"Color invalido '{value}': usa formato RRGGBB (ej. 9B30FF)")
    return int(s[0:2], 16), int(s[2:4], 16), int(s[4:6], 16)


def _fmt_rgb(c: tuple[int, int, int]) -> str:
    return f"#{c[0]:02X}{c[1]:02X}{c[2]:02X}"


def _lerp_rgb(a: tuple[int, int, int], b: tuple[int, int, int], t: float) -> tuple[int, int, int]:
    t = max(0.0, min(1.0, t))
    return (
        round(a[0] + (b[0] - a[0]) * t),
        round(a[1] + (b[1] - a[1]) * t),
        round(a[2] + (b[2] - a[2]) * t),
    )


def _scale_rgb(c: tuple[int, int, int], f: float) -> tuple[int, int, int]:
    f = max(0.0, min(1.0, f))
    return (round(c[0] * f), round(c[1] * f), round(c[2] * f))


def _sample_palette(t: float, colors: list[tuple[int, int, int]], color_type: str) -> tuple[int, int, int]:
    t = t % 1.0
    if color_type == "spectrum":
        r, g, b = colorsys.hsv_to_rgb(t, 1.0, 1.0)
        return (round(r * 255), round(g * 255), round(b * 255))
    if color_type == "one" or len(colors) == 1:
        # Smooth pulse between color[0] and dim color[0]
        w = 0.5 + 0.5 * math.sin(t * 2.0 * math.pi)
        return _scale_rgb(colors[0], 0.15 + 0.85 * w)
    palette = colors[:2] if color_type == "two" else colors[:3]
    n = len(palette)
    pos = t * n
    i0 = int(pos) % n
    i1 = (i0 + 1) % n
    frac = pos - int(pos)
    return _lerp_rgb(palette[i0], palette[i1], frac)


def generate_effect_frames(
    mode: str,
    colors: list[tuple[int, int, int]],
    color_type: str = "one",
    direction: str = "right",
    num_frames: int = 24,
) -> list[list[tuple[int, int, int]]]:
    """Generate FrameLight RGB frames (each frame has NUM_LEDS (r,g,b) tuples)."""
    c1 = colors[0] if len(colors) >= 1 else (155, 48, 255)
    c2 = colors[1] if len(colors) >= 2 else (57, 255, 20)
    c3 = colors[2] if len(colors) >= 3 else (0, 229, 255)
    pal = [c1, c2, c3]

    if mode == "off":
        return [[(0, 0, 0)] * NUM_LEDS]

    if mode == "solid":
        return [[c1] * NUM_LEDS]

    if mode == "twozone":
        frame = [c1] * NUM_LEDS
        for led in ACCENT_LEDS:
            frame[led - 1] = c2
        side_col = c3 if color_type == "three" else c2
        for led in SIDE_BAR_LEDS:
            frame[led - 1] = side_col
        return [frame]

    frames: list[list[tuple[int, int, int]]] = []

    if mode == "breath":
        active = [c1] if color_type == "one" else ([c1, c2] if color_type == "two" else [c1, c2, c3])
        if color_type == "spectrum":
            active = [(255, 0, 0), (0, 255, 0), (0, 128, 255), (180, 0, 255)]
        f_per_col = max(8, num_frames // len(active))
        for col in active:
            for i in range(f_per_col):
                phase = i / f_per_col
                intensity = 0.08 + 0.92 * (0.5 - 0.5 * math.cos(phase * 2.0 * math.pi))
                frames.append([_scale_rgb(col, intensity)] * NUM_LEDS)
        return frames[:30]

    if mode == "cycle":
        eff_ctype = "spectrum" if color_type == "one" else color_type
        for i in range(num_frames):
            col = _sample_palette(i / num_frames, pal, eff_ctype)
            frames.append([col] * NUM_LEDS)
        return frames

    if mode in ("wave", "mirrorwave"):
        eff_ctype = "spectrum" if color_type == "one" else color_type
        for i in range(num_frames):
            phase = i / num_frames
            frame = [(0, 0, 0)] * NUM_LEDS
            for led in range(1, NUM_LEDS + 1):
                x, y = LED_COORDS[led]
                if mode == "mirrorwave":
                    coord = abs(x - 0.5) * 2.0
                elif direction == "right":
                    coord = -x
                elif direction == "left":
                    coord = x
                elif direction == "down":
                    coord = -y
                elif direction == "up":
                    coord = y
                elif direction == "center-out":
                    coord = -math.hypot(x - 0.5, y - 0.5) * 1.4
                else:  # center-in
                    coord = math.hypot(x - 0.5, y - 0.5) * 1.4
                frame[led - 1] = _sample_palette(coord + phase, pal, eff_ctype)
            frames.append(frame)
        return frames

    if mode == "alternating":
        n = 16
        for i in range(n):
            t = 0.5 - 0.5 * math.cos((i / n) * 2.0 * math.pi)
            col_a = _lerp_rgb(c1, c2, t)
            col_b = _lerp_rgb(c2, c1, t)
            frame = [(0, 0, 0)] * NUM_LEDS
            for led in range(1, NUM_LEDS + 1):
                x, y = LED_COORDS[led]
                parity = (round(x * 14) + round(y * 5)) % 2
                frame[led - 1] = col_a if parity == 0 else col_b
            frames.append(frame)
        return frames

    if mode == "confetti":
        rng = random.Random(42)
        base = _scale_rgb(c1, 0.25) if color_type != "spectrum" else (12, 8, 22)
        sparks: dict[int, tuple[int, tuple[int, int, int]]] = {}
        n = 24
        # Pre-seed loopable sparks
        spark_events = []
        for f in range(n):
            for _ in range(7):
                led = rng.randint(1, NUM_LEDS)
                col = _sample_palette(rng.random(), pal, "spectrum" if color_type in ("one", "spectrum") else color_type)
                spark_events.append((f, led, col))
        for f in range(n):
            frame = [base] * NUM_LEDS
            for sf, led, col in spark_events:
                age = (f - sf) % n
                if age < 5:
                    fade = 1.0 - (age / 5.0)
                    frame[led - 1] = _lerp_rgb(base, col, fade)
            frames.append(frame)
        return frames

    return [[c1] * NUM_LEDS]


def find_hidraw_path() -> Optional[str]:
    for sys_dir in sorted(glob.glob("/sys/class/hidraw/hidraw*")):
        uevent_path = os.path.join(sys_dir, "device", "uevent")
        desc_path = os.path.join(sys_dir, "device", "report_descriptor")
        try:
            with open(uevent_path, "r", encoding="utf-8", errors="ignore") as f:
                uevent = f.read().upper()
            matched = any(f"{VENDOR_ID:04X}:{pid:08X}" in uevent for pid in PRODUCT_IDS)
            if not matched:
                continue
            with open(desc_path, "rb") as f:
                desc = f.read()
            if b"\x06\x90\xff" in desc:
                return "/dev/" + os.path.basename(sys_dir)
        except OSError:
            continue
    return None


class _HidrawTransport:
    def __init__(self, path: str):
        self.desc = f"hidraw ({path})"
        self.fd = os.open(path, os.O_RDWR | os.O_NONBLOCK)

    def close(self) -> None:
        if getattr(self, "fd", -1) >= 0:
            os.close(self.fd)
            self.fd = -1

    def drain(self) -> None:
        while True:
            r, _, _ = select.select([self.fd], [], [], 0.0)
            if not r:
                break
            try:
                os.read(self.fd, 128)
            except BlockingIOError:
                break

    def write(self, pkt: bytes) -> None:
        os.write(self.fd, pkt)

    def read(self, timeout: float) -> Optional[bytes]:
        r, _, _ = select.select([self.fd], [], [], max(0.0, timeout))
        if not r:
            return None
        try:
            return os.read(self.fd, 128)
        except BlockingIOError:
            return None


class _LibusbTransport:
    """Direct libusb-1.0 Interrupt transfer on Interface 2 (EP OUT 0x04 / EP IN 0x83)."""

    def __init__(self) -> None:
        lib_name = ctypes.util.find_library("usb-1.0") or "libusb-1.0.so.0"
        self.lib = ctypes.CDLL(lib_name)

        self.lib.libusb_init.argtypes = [ctypes.POINTER(ctypes.c_void_p)]
        self.lib.libusb_init.restype = ctypes.c_int
        self.lib.libusb_exit.argtypes = [ctypes.c_void_p]
        self.lib.libusb_open_device_with_vid_pid.argtypes = [ctypes.c_void_p, ctypes.c_uint16, ctypes.c_uint16]
        self.lib.libusb_open_device_with_vid_pid.restype = ctypes.c_void_p
        self.lib.libusb_close.argtypes = [ctypes.c_void_p]
        self.lib.libusb_kernel_driver_active.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_kernel_driver_active.restype = ctypes.c_int
        self.lib.libusb_detach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_detach_kernel_driver.restype = ctypes.c_int
        self.lib.libusb_attach_kernel_driver.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_attach_kernel_driver.restype = ctypes.c_int
        self.lib.libusb_claim_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_claim_interface.restype = ctypes.c_int
        self.lib.libusb_release_interface.argtypes = [ctypes.c_void_p, ctypes.c_int]
        self.lib.libusb_release_interface.restype = ctypes.c_int
        self.lib.libusb_interrupt_transfer.argtypes = [
            ctypes.c_void_p,
            ctypes.c_ubyte,
            ctypes.c_char_p,
            ctypes.c_int,
            ctypes.POINTER(ctypes.c_int),
            ctypes.c_uint,
        ]
        self.lib.libusb_interrupt_transfer.restype = ctypes.c_int
        self.lib.libusb_error_name.argtypes = [ctypes.c_int]
        self.lib.libusb_error_name.restype = ctypes.c_char_p

        self.ctx = ctypes.c_void_p()
        rc = self.lib.libusb_init(ctypes.byref(self.ctx))
        if rc != 0:
            raise RuntimeError(f"libusb_init fallo: {rc}")

        self.handle = None
        self.pid = None
        for pid in PRODUCT_IDS:
            h = self.lib.libusb_open_device_with_vid_pid(self.ctx, VENDOR_ID, pid)
            if h:
                self.handle = h
                self.pid = pid
                break
        if not self.handle:
            self.lib.libusb_exit(self.ctx)
            self.ctx = None
            raise PermissionError(
                "No se pudo abrir el HyperX Alloy Rise 75 via libusb (verifica que este conectado "
                "y que /etc/udev/rules.d/60-hyperx-alloy-rise.rules tenga permisos uaccess)."
            )

        self.detached = False
        if self.lib.libusb_kernel_driver_active(self.handle, USB_INTERFACE) == 1:
            rc = self.lib.libusb_detach_kernel_driver(self.handle, USB_INTERFACE)
            if rc == 0:
                self.detached = True

        rc = self.lib.libusb_claim_interface(self.handle, USB_INTERFACE)
        if rc != 0:
            err = self.lib.libusb_error_name(rc).decode("utf-8", "replace")
            self.close()
            raise RuntimeError(f"libusb_claim_interface(2) fallo: {err} ({rc})")

        self.desc = f"libusb ({VENDOR_ID:04x}:{self.pid:04x} intf {USB_INTERFACE})"

    def close(self) -> None:
        if getattr(self, "handle", None):
            try:
                self.lib.libusb_release_interface(self.handle, USB_INTERFACE)
            except Exception:
                pass
            if getattr(self, "detached", False):
                try:
                    self.lib.libusb_attach_kernel_driver(self.handle, USB_INTERFACE)
                except Exception:
                    pass
            self.lib.libusb_close(self.handle)
            self.handle = None
        if getattr(self, "ctx", None):
            self.lib.libusb_exit(self.ctx)
            self.ctx = None

    def drain(self) -> None:
        buf = ctypes.create_string_buffer(PACKET_SIZE)
        transferred = ctypes.c_int(0)
        for _ in range(8):
            rc = self.lib.libusb_interrupt_transfer(
                self.handle, USB_EP_IN, buf, PACKET_SIZE, ctypes.byref(transferred), 5
            )
            if rc != 0:
                break

    def write(self, pkt: bytes) -> None:
        buf = ctypes.create_string_buffer(pkt, PACKET_SIZE)
        transferred = ctypes.c_int(0)
        rc = self.lib.libusb_interrupt_transfer(
            self.handle, USB_EP_OUT, buf, PACKET_SIZE, ctypes.byref(transferred), 500
        )
        if rc != 0:
            err = self.lib.libusb_error_name(rc).decode("utf-8", "replace")
            raise OSError(f"libusb write fallo: {err} ({rc})")

    def read(self, timeout: float) -> Optional[bytes]:
        buf = ctypes.create_string_buffer(PACKET_SIZE)
        transferred = ctypes.c_int(0)
        ms = max(1, int(timeout * 1000))
        rc = self.lib.libusb_interrupt_transfer(
            self.handle, USB_EP_IN, buf, PACKET_SIZE, ctypes.byref(transferred), ms
        )
        if rc == 0 and transferred.value > 0:
            return buf.raw[: transferred.value]
        return None


class AlloyRise75:
    def __init__(self, path: Optional[str] = None):
        hid_path = path or find_hidraw_path()
        if hid_path:
            try:
                self._io = _HidrawTransport(hid_path)
                self.path = self._io.desc
                return
            except PermissionError:
                pass
        self._io = _LibusbTransport()
        self.path = self._io.desc

    def close(self) -> None:
        if getattr(self, "_io", None):
            self._io.close()
            self._io = None

    def __enter__(self) -> "AlloyRise75":
        return self

    def __exit__(self, *args) -> None:
        self.close()

    def _wait_cmd_ack(self, report_id: int, sub_id: int, timeout: float = 0.4) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self._io.read(max(0.01, deadline - time.monotonic()))
            if r and r[0] == 0xFF and r[1] == 0x01 and len(r) >= 16:
                if r[14] == report_id and r[15] == sub_id:
                    return True
        return False

    def _wait_frame_seq_ack(self, seq: int, timeout: float = 0.4) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            r = self._io.read(max(0.01, deadline - time.monotonic()))
            if r and r[0] == 0x45 and r[1] == 0x04 and len(r) >= 4:
                if struct.unpack_from("<H", r, 2)[0] == seq:
                    return True
        return False

    def send(self, payload: bytes, expect_reply_id: Optional[int] = None, timeout: float = 0.4) -> Optional[bytes]:
        if len(payload) > PACKET_SIZE:
            raise ValueError(f"Packet too large: {len(payload)}")
        pkt = payload.ljust(PACKET_SIZE, b"\x00")
        self._io.drain()
        self._io.write(pkt)
        if expect_reply_id is None:
            return None
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            remaining = max(0.01, deadline - time.monotonic())
            resp = self._io.read(remaining)
            if resp and resp[0] == expect_reply_id:
                return resp
        return None

    def get_current_profile(self) -> int:
        resp = self.send(bytes([0x14, 0x02]), expect_reply_id=0x15)
        return resp[2] if resp and len(resp) > 2 else 0

    def set_profile(self, profile: int) -> None:
        if not 0 <= profile <= 9:
            raise ValueError("El perfil debe estar entre 0 y 9")
        pkt = struct.pack("<BBHBB", 0x14, 0x01, profile, 0, 0).ljust(PACKET_SIZE, b"\x00")
        self._io.drain()
        self._io.write(pkt)
        self._wait_cmd_ack(0x14, 0x01, timeout=0.25)

    def get_brightness(self, profile: int = 0) -> int:
        resp = self.send(bytes([0x40, 0x02, profile & 0xFF]), expect_reply_id=0x41)
        if resp and len(resp) > 2:
            return round((resp[2] * 100) / 255)
        return 100

    def set_brightness(self, percent: int) -> None:
        percent = max(0, min(100, int(percent)))
        raw = (percent * 255) // 100
        pkt = bytes([0x40, 0x01, 0x00, 0x00, raw]).ljust(PACKET_SIZE, b"\x00")
        self._io.drain()
        self._io.write(pkt)
        self._wait_cmd_ack(0x40, 0x01, timeout=0.15)

    def get_preset_raw(self, profile: int = 0) -> Optional[bytes]:
        return self.send(bytes([0x42, 0x03, profile & 0xFF]), expect_reply_id=0x43)

    def save_framelight(
        self,
        frames: list[list[tuple[int, int, int]]],
        profile: int = 0,
        frame_delay_ms: int = 50,
    ) -> tuple[int, int]:
        """
        Save a 1-frame (static) or multi-frame (animated) FrameLight effect
        permanently into the keyboard's onboard flash memory for `profile`.
        Returns (acked_packets, total_packets).
        """
        num_frames = len(frames)
        if not 1 <= num_frames <= 60:
            raise ValueError("num_frames debe estar entre 1 y 60")
        total_pkts = num_frames * PACKETS_PER_FRAME

        # 1) FrameLightConfigurationPayload (0x44, 0x03)
        cfg = bytearray(PACKET_SIZE)
        cfg[0] = 0x44
        cfg[1] = 0x03
        cfg[2] = num_frames
        struct.pack_into("<H", cfg, 3, total_pkts)
        struct.pack_into("<H", cfg, 5, profile)
        cfg[7] = 0x00  # target: onboard profile
        self._io.drain()
        self._io.write(bytes(cfg))
        self._wait_cmd_ack(0x44, 0x03, timeout=0.5)

        # 2) FrameLightDataPayload packets (0x44, 0x04) with lockstep seq ACK
        acked = 0
        seq = 0
        for frame in frames:
            for pkt_in_frame in range(PACKETS_PER_FRAME):
                p = bytearray(PACKET_SIZE)
                p[0] = 0x44
                p[1] = 0x04
                struct.pack_into("<H", p, 2, seq)
                for slot in range(20):
                    led_idx = pkt_in_frame * 20 + slot
                    if led_idx < len(frame):
                        r, g, b = frame[led_idx]
                        off = 4 + slot * 3
                        p[off] = r
                        p[off + 1] = g
                        p[off + 2] = b
                self._io.write(bytes(p))
                if self._wait_frame_seq_ack(seq, timeout=0.4):
                    acked += 1
                seq += 1

        # 3) Activate & commit FrameLight preset (0x42, 0x02, mode=0x06)
        ps = bytearray(PACKET_SIZE)
        ps[0] = 0x42
        ps[1] = 0x02
        struct.pack_into("<H", ps, 2, profile)
        ps[4] = 0x06  # LightingPresetMode::FrameLight
        struct.pack_into("<H", ps, 5, max(20, min(5000, int(frame_delay_ms))))
        ps[7] = 0x00
        ps[8] = 0x00
        self._io.write(bytes(ps))
        self._wait_cmd_ack(0x42, 0x02, timeout=0.5)
        return acked, total_pkts

    def set_direct_frame(self, frame: list[tuple[int, int, int]]) -> None:
        """Send a volatile Direct Mode frame (reverts after ~2s unless refreshed)."""
        self._io.drain()
        self._io.write(bytes([0x44, 0x01, PACKETS_PER_FRAME]).ljust(PACKET_SIZE, b"\x00"))
        for pkt_idx in range(PACKETS_PER_FRAME):
            buf = bytearray(PACKET_SIZE)
            buf[0] = 0x44
            buf[1] = 0x02
            buf[2] = pkt_idx
            buf[3] = 0x00
            for slot in range(20):
                led_idx = pkt_idx * 20 + slot
                if led_idx < len(frame):
                    off = 4 + slot * 3
                    buf[off : off + 3] = bytes(frame[led_idx])
            self._io.write(bytes(buf))

    def direct_off(self) -> None:
        self._io.write(bytes([0x44, 0x01, 0x00]).ljust(PACKET_SIZE, b"\x00"))


def cmd_info(dev: AlloyRise75, profile: Optional[int]) -> None:
    print(f"Dispositivo : {dev.path}")
    active = dev.get_current_profile()
    target = active if profile is None else profile
    bright = dev.get_brightness(target)
    raw = dev.get_preset_raw(target)
    print(f"Perfil      : {target} (activo: {active})")
    print(f"Brillo      : {bright}%")
    if raw:
        mode = raw[2]
        delay = struct.unpack_from("<H", raw, 3)[0]
        mode_name = "FrameLight (en memoria)" if mode == 0x06 else f"0x{mode:02x}"
        print(f"Modo        : {mode_name} (frame_delay={delay} ms)")


def _update_daemon_config(updates: dict) -> None:
    import json
    import subprocess

    cfg_dir = os.path.join(os.environ.get("XDG_CONFIG_HOME", os.path.expanduser("~/.config")), "hyperx-alloy-rise-rgb")
    cfg_path = os.path.join(cfg_dir, "config.json")
    cfg = {
        "enabled": True,
        "brightness": 100,
        "mode": "twozone",
        "color_type": "two",
        "direction": "right",
        "delay_ms": 50,
        "colors": ["#9B30FF", "#39FF14", "#00E5FF"],
    }
    try:
        with open(cfg_path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            cfg.update(data)
    except Exception:
        pass
    cfg.update(updates)
    os.makedirs(cfg_dir, exist_ok=True)
    tmp = cfg_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(cfg, f, indent=2)
    os.replace(tmp, cfg_path)
    try:
        res = subprocess.run(["systemctl", "--user", "is-active", "hyperx-alloy-rise-rgb.service"], capture_output=True, text=True)
        if res.returncode == 0 and res.stdout.strip() == "active":
            subprocess.run(["systemctl", "--user", "kill", "-s", "HUP", "hyperx-alloy-rise-rgb.service"], capture_output=True)
        else:
            subprocess.run(["systemctl", "--user", "start", "hyperx-alloy-rise-rgb.service"], capture_output=True)
    except Exception:
        pass


def main() -> int:
    parser = argparse.ArgumentParser(description="Control RGB nativo para HyperX Alloy Rise 75")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p_info = sub.add_parser("info", help="Muestra estado actual")
    p_info.add_argument("--profile", type=int, default=None)

    p_solid = sub.add_parser("solid", help="Aplica un color solido en vivo (via servicio RGB)")
    p_solid.add_argument("color", help="Color hex RRGGBB (ej. 9B30FF o #39FF14)")

    p_eff = sub.add_parser("effect", help="Aplica un efecto estatico o animado en vivo (via servicio RGB)")
    p_eff.add_argument("mode", choices=EFFECT_MODES)
    p_eff.add_argument("--color", default="9B30FF", help="Color 1 RRGGBB")
    p_eff.add_argument("--color2", default="39FF14", help="Color 2 RRGGBB")
    p_eff.add_argument("--color3", default="00E5FF", help="Color 3 RRGGBB")
    p_eff.add_argument("--color-type", choices=COLOR_TYPES, default="two")
    p_eff.add_argument("--delay", type=int, default=50, help="Milisegundos por cuadro (25-200, menor = mas rapido)")
    p_eff.add_argument("--direction", choices=DIRECTIONS, default="right")

    p_br = sub.add_parser("brightness", help="Cambia el brillo (0-100)")
    p_br.add_argument("percent", type=int)

    sub.add_parser("native", help="Pausa el control en Linux y vuelve al efecto interno del teclado")

    p_dir = sub.add_parser("direct", help="Envia un solo cuadro Direct Mode")
    p_dir.add_argument("color", help="Color hex RRGGBB")

    sub.add_parser("direct-off", help="Sale de Direct Mode")

    p_prof = sub.add_parser("profile", help="Cambia el perfil activo (0-9)")
    p_prof.add_argument("index", type=int)

    args = parser.parse_args()

    try:
        if args.cmd == "solid":
            c = parse_hex_color(args.color)
            _update_daemon_config({"enabled": True, "mode": "solid", "color_type": "one", "colors": [_fmt_rgb(c), _fmt_rgb(c), _fmt_rgb(c)]})
            print(f"Color solido {_fmt_rgb(c)} aplicado.")
            return 0
        if args.cmd == "effect":
            c1 = parse_hex_color(args.color)
            c2 = parse_hex_color(args.color2)
            c3 = parse_hex_color(args.color3)
            _update_daemon_config({
                "enabled": True,
                "mode": args.mode,
                "color_type": args.color_type,
                "direction": args.direction,
                "delay_ms": max(25, min(200, int(args.delay))),
                "colors": [_fmt_rgb(c1), _fmt_rgb(c2), _fmt_rgb(c3)],
            })
            print(f"Efecto '{args.mode}' aplicado.")
            return 0
        if args.cmd == "brightness":
            pct = max(0, min(100, int(args.percent)))
            _update_daemon_config({"brightness": pct})
            print(f"Brillo ajustado a {pct}%.")
            return 0
        if args.cmd == "native":
            _update_daemon_config({"enabled": False})
            print("Control Linux pausado; usando efecto interno del teclado.")
            return 0

        with AlloyRise75() as dev:
            if args.cmd == "info":
                cmd_info(dev, args.profile)
            elif args.cmd == "direct":
                c = parse_hex_color(args.color)
                dev.set_direct_frame([c] * NUM_LEDS)
                print(f"Direct Mode {_fmt_rgb(c)} enviado.")
            elif args.cmd == "direct-off":
                dev.direct_off()
                print("Direct Mode desactivado.")
            elif args.cmd == "profile":
                dev.set_profile(args.index)
                print(f"Perfil activo cambiado a {args.index}.")
    except Exception as e:
        print(f"Error: {e}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
